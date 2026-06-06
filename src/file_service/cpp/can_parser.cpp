/*
 * can_parser.cpp
 *
 * C++ hot-path for CAN log file parsing.
 * Mirrors the Python LogParser format detection and parsing logic,
 * but processes the file (or individual lines) at native speed.
 *
 * Build: included in native_sdk_native shared library via CMakeLists.txt
 */

#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <cctype>
#include <array>
#include <string>
#include <vector>
#include <unordered_map>
#include <fstream>
#include <limits>
#include <mutex>
#include <thread>
#include <stdexcept>

#if defined(_WIN32)
#include <windows.h>
#endif

#if !defined(_WIN32)
#include <sys/eventfd.h>
#include <unistd.h>
#endif

#include "mmap_wrapper.h"
#include "can_analyzer_log.h"
#include "can_parser.h"
#include "prs_token.h"
#include "mmap_header_constract.h"
#include "index_mmap_layout.h"
#include "parsed_entry_layout.h"
#include "write_data_segmented.h"

extern "C" {

#ifndef LOGGING_TRACE_ENABLED
#if defined(__LW_TRACE)
#define LOGGING_TRACE_ENABLED __LW_TRACE()
#else
#define LOGGING_TRACE_ENABLED ;
#endif
#endif

static constexpr uint32_t kLastTimestampTableSize = 0x2000;
static constexpr int kNumThreads = 4;
static constexpr uint32_t kDataSegmentCapacity = 1'000'000;
static constexpr uint32_t kIndexSegmentCapacity = 1'000'000;
static constexpr uint32_t kIndexMaxCanIds = 4096;
static constexpr uint32_t kChannelIndexSegmentCapacity = 1'000'000;
static constexpr uint32_t kChannelIndexMaxChannels = 64;
static constexpr uint32_t kDirectionIndexSegmentCapacity = 1'000'000;
static constexpr uint32_t kDirectionIndexMaxDirections = 8;

struct LastTimestampTable {
    std::array<double, kLastTimestampTableSize> last{};
    std::array<uint8_t, kLastTimestampTableSize> seen{};

    inline double update_and_get_prev(uint32_t can_id, double ts) {
        if (can_id >= kLastTimestampTableSize) return ts;
        const double prev = seen[can_id] ? last[can_id] : ts;
        last[can_id] = ts;
        seen[can_id] = 1;
        return prev;
    }
};

static file_service::MmapHeaderConstract* g_parser_status_header = nullptr;


static std::string normalize_channel_key(const char* ch) {
    if (!ch || ch[0] == '\0') return "unknown";
    std::string s(ch);
    while (!s.empty() && (unsigned char)s.back() <= ' ') s.pop_back();
    size_t start = 0;
    while (start < s.size() && (unsigned char)s[start] <= ' ') ++start;
    if (start > 0) s.erase(0, start);
    if (s.empty()) return "unknown";
    for (char& c : s) c = (char)tolower((unsigned char)c);
    return s;
}

// Parse with a known format (skip detection; faster hot path)
static bool parse_with_fmt(const char* line, size_t len,
                            FormatType fmt, uint32_t line_num,
                            ParsedEntry& e) {
    e.changed = 0;
    e.last_timestamp = e.timestamp;

    if (fmt == FMT_CANCMD_T2)
        return parse_cancmd_t2(line, len, line_num, e);

    Tok toks[256];
    int ntoks = tokenize(line, len, toks, 256);
    if (ntoks < 4) return false;

    switch (fmt) {
    case FMT_CANOE:      return parse_canoe       (toks, ntoks, line_num, e);
    case FMT_CANOE_FULL: return parse_canoe_full  (toks, ntoks, line_num, e);
    case FMT_CANOE_CMP:  return parse_canoe_compact(toks, ntoks, line_num, e);
    case FMT_CANCMD:     return parse_cancmd      (toks, ntoks, line_num, e);
    case FMT_FILTER:     return parse_filter_log  (toks, ntoks, line_num, e);
    case FMT_CANSUKE:    return parse_cansuke     (toks, ntoks, line_num, e);
    case FMT_CANCMD_T3:  return parse_cancmd_t3   (toks, ntoks, line_num, e);
    default:             return false;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Dynamic array helper
// ─────────────────────────────────────────────────────────────────────────────
struct EntryBuf {
    ParsedEntry* data  = nullptr;
    uint32_t     size  = 0;
    uint32_t     cap   = 0;

    bool push(const ParsedEntry& e) {
        if (size == cap) {
            uint32_t new_cap = cap == 0 ? 4096 : cap * 2;
            ParsedEntry* p = (ParsedEntry*)realloc(data, new_cap * sizeof(ParsedEntry));
            if (!p) return false;
            data = p; cap = new_cap;
        }
        data[size++] = e;
        return true;
    }

    ParsedEntry* release_to_heap() {
        // Shrink to exact size (best-effort)
        if (size == 0) return nullptr;
        ParsedEntry* p = (ParsedEntry*)realloc(data, size * sizeof(ParsedEntry));
        data = nullptr; cap = 0; size = 0;
        return p;
    }

    ~EntryBuf() { free(data); }
};

struct ThreadOut {
    EntryBuf buf;
    uint64_t parsed = 0;
};

struct PrevRaw {
    uint8_t len = 0;
    uint8_t data[64] = {0};
};

static std::string make_segment_family_path(const std::string& base,
                                            const char* family_suffix,
                                            uint32_t seg_idx) {
    std::string stem = base;
    if (stem.size() >= 5 && stem.compare(stem.size() - 5, 5, ".mmap") == 0) {
        stem.resize(stem.size() - 5);
    }
    if (family_suffix && family_suffix[0] != '\0') {
        stem += family_suffix;
    }
    char num[16];
    snprintf(num, sizeof(num), ".%03u.mmap", seg_idx);
    return stem + num;
}

static void parse_input_parallel(const char* src,
                                 const char* end,
                                 FormatType detected,
                                 std::array<ThreadOut, kNumThreads>& tout) {
    struct ByteRange { const char* begin; const char* end; };
    ByteRange ranges[kNumThreads];
    {
        const char* prev_end = src;
        const size_t input_size = static_cast<size_t>(end - src);
        const size_t chunk_bytes = input_size / static_cast<size_t>(kNumThreads);
        for (int i = 0; i < kNumThreads; i++) {
            ranges[i].begin = prev_end;
            if (i == kNumThreads - 1 || prev_end >= end) {
                ranges[i].end = end;
                for (int j = i + 1; j < kNumThreads; j++) {
                    ranges[j].begin = end;
                    ranges[j].end = end;
                }
                break;
            }
            const char* nominal = src + static_cast<size_t>(i + 1) * chunk_bytes;
            if (nominal >= end) nominal = end - 1;
            const char* nl = reinterpret_cast<const char*>(
                memchr(nominal, '\n', static_cast<size_t>(end - nominal)));
            ranges[i].end = nl ? nl + 1 : end;
            prev_end = ranges[i].end;
        }
    }

    uint32_t chunk_newline_count[kNumThreads] = {};
    {
        std::vector<std::thread> count_threads;
        count_threads.reserve(kNumThreads);
        for (int i = 0; i < kNumThreads; i++) {
            count_threads.emplace_back([&, i]() {
                uint32_t cnt = 0;
                const char* p = ranges[i].begin;
                const char* e = ranges[i].end;
                while (p < e) {
                    const char* nl = reinterpret_cast<const char*>(
                        memchr(p, '\n', static_cast<size_t>(e - p)));
                    if (!nl) break;
                    ++cnt;
                    p = nl + 1;
                }
                chunk_newline_count[i] = cnt;
            });
        }
        for (auto& th : count_threads) th.join();
    }

    uint32_t chunk_start_line[kNumThreads];
    chunk_start_line[0] = 0;
    for (int i = 1; i < kNumThreads; i++) {
        chunk_start_line[i] = chunk_start_line[i - 1] + chunk_newline_count[i - 1];
    }

    std::vector<std::thread> threads;
    threads.reserve(kNumThreads);
    for (int t = 0; t < kNumThreads; t++) {
        threads.emplace_back([&, t]() {
            const char* cur = ranges[t].begin;
            const char* chunk_end = ranges[t].end;
            uint32_t lnum = chunk_start_line[t];
            FormatType local_fmt = detected;

            while (cur < chunk_end) {
                const char* eol = reinterpret_cast<const char*>(
                    memchr(cur, '\n', static_cast<size_t>(chunk_end - cur)));
                const char* line_end = eol ? eol : chunk_end;
                size_t len = static_cast<size_t>(line_end - cur);
                while (len > 0 && cur[len - 1] == '\r') --len;
                ++lnum;

                ParsedEntry e;
                bool ok = false;
                if (local_fmt == FMT_UNKNOWN) {
                    FormatType f2 = detect_and_parse(cur, len, lnum, e);
                    if (f2 != FMT_UNKNOWN) { local_fmt = f2; ok = true; }
                } else {
                    ok = parse_with_fmt(cur, len, local_fmt, lnum, e);
                }

                if (ok) {
                    if (!tout[t].buf.push(e)) {
                        break;
                    }
                    ++tout[t].parsed;
                }

                cur = eol ? eol + 1 : chunk_end;
            }
        });
    }
    for (auto& th : threads) th.join();
}

static std::vector<ParsedEntry> collect_entries_from_threads(
    const std::array<ThreadOut, kNumThreads>& tout) {
    size_t total = 0;
    for (int t = 0; t < kNumThreads; ++t) {
        total += tout[t].buf.size;
    }

    std::vector<ParsedEntry> entries;
    entries.reserve(total);
    for (int t = 0; t < kNumThreads; ++t) {
        for (uint32_t i = 0; i < tout[t].buf.size; ++i) {
            entries.push_back(tout[t].buf.data[i]);
        }
    }
    return entries;
}

/*
 * can_parser_parse_file
 *   Parses an entire CAN log text file.
 *   Detects format from the first valid line, then applies that parser to all
 *   subsequent lines.
 *
 *   path        : UTF-8 file path
 *   out_entries : receives pointer to malloc'd ParsedEntry array (caller frees
 *                 with can_parser_free_entries)
 *   out_count   : receives number of entries
 *   Returns 0 on success, negative on error.
 */
CP_EXPORT int32_t can_parser_parse_file(const char*    path,
                                         ParsedEntry**  out_entries,
                                         uint32_t*      out_count) {
    // LOGGING_TRACE_ENABLED;
    if (!path || !out_entries || !out_count) return -1;
    *out_entries = nullptr;
    *out_count   = 0;

    FILE* f = fopen(path, "rb");
    if (!f) return -2;

    EntryBuf buf;
    FormatType detected = FMT_UNKNOWN;
    LastTimestampTable last_timestamp_by_id;

    char   line[16384];
    uint32_t line_num = 0;
    ParsedEntry e;

    while (fgets(line, sizeof(line), f)) {
        ++line_num;
        size_t len = strlen(line);
        // Strip trailing \r\n
        while (len > 0 && (line[len-1] == '\r' || line[len-1] == '\n')) --len;
        line[len] = '\0';

        if (detected == FMT_UNKNOWN) {
            // Detection phase: try all parsers
            FormatType fmt = detect_and_parse(line, len, line_num, e);
            if (fmt != FMT_UNKNOWN) {
                e.last_timestamp = last_timestamp_by_id.update_and_get_prev(e.can_id, e.timestamp);
                detected = fmt;
                buf.push(e);
            }
        } else {
            // Hot path: use cached parser
            if (parse_with_fmt(line, len, detected, line_num, e)) {
                e.last_timestamp = last_timestamp_by_id.update_and_get_prev(e.can_id, e.timestamp);
                buf.push(e);
            }
        }
    }
    fclose(f);

    *out_count   = buf.size;
    *out_entries = buf.release_to_heap();
    return 0;
}

/*
 * can_parser_parse_file_with_fmt
 *   Like can_parser_parse_file, but skips auto-detection.
 *   Python detects the format via regex, then passes the FormatType int here
 *   so C++ uses parse_with_fmt for every line (pure hot path, zero detection).
 *
 *   fmt         : FormatType integer (1..8)
 *   path        : UTF-8 file path
 *   out_entries : receives pointer to malloc'd ParsedEntry array (caller frees)
 *   out_count   : receives number of entries
 *   Returns 0 on success, negative on error.
 */
CP_EXPORT int32_t can_parser_parse_file_with_fmt(const char*    path,
                                                  int32_t        fmt,
                                                  ParsedEntry**  out_entries,
                                                  uint32_t*      out_count) {
    // LOGGING_TRACE_ENABLED;
    if (!path || !out_entries || !out_count) return -1;
    if (fmt < 1 || fmt > 8) return -1;  // invalid FormatType
    *out_entries = nullptr;
    *out_count   = 0;

    FILE* f = fopen(path, "rb");
    if (!f) return -2;

    EntryBuf buf;
    FormatType format = static_cast<FormatType>(fmt);
    LastTimestampTable last_timestamp_by_id;

    char      line[16384];
    uint32_t  line_num = 0;
    ParsedEntry e;

    while (fgets(line, sizeof(line), f)) {
        ++line_num;
        size_t len = strlen(line);
        while (len > 0 && (line[len-1] == '\r' || line[len-1] == '\n')) --len;
        line[len] = '\0';

        if (parse_with_fmt(line, len, format, line_num, e)) {
            e.last_timestamp = last_timestamp_by_id.update_and_get_prev(e.can_id, e.timestamp);
            buf.push(e);
        }
    }
    fclose(f);

    *out_count   = buf.size;
    *out_entries = buf.release_to_heap();
    return 0;
}

/*
 * can_parser_parse_line
 *   Parse a single line (trying all formats). Useful for CSV/Excel per-row calls.
 *   Returns 1 on success, 0 on failure.
 */
CP_EXPORT int32_t can_parser_parse_line(const char*  line,
                                         uint32_t     line_num,
                                         ParsedEntry* out) {
    // LOGGING_TRACE_ENABLED;
    if (!line || !out) return 0;
    size_t len = strlen(line);
    // strip trailing whitespace
    while (len > 0 && (unsigned char)line[len-1] <= ' ') --len;
    // note: we can't modify line (const); work with len
    FormatType fmt = detect_and_parse(line, len, line_num, *out);
    if (fmt != FMT_UNKNOWN) {
        out->last_timestamp = out->timestamp;
    }
    return fmt != FMT_UNKNOWN ? 1 : 0;
}

/*
 * can_parser_free_entries
 *   Free the array returned by can_parser_parse_file.
 */
CP_EXPORT void can_parser_free_entries(ParsedEntry* ptr) {
    free(ptr);
}

/*
1.
file_path is the input log file path to parse.

2.
data_base_path is the base filename/path for parsed data mmap segments.
It generates files like:
data_stem.000.mmap
data_stem.001.mmap
data_stem.002.mmap
and so on.

3.
index_base_path is the base filename/path for index mmap segments (only if provided and not empty).
It generates three index file families:
CAN-ID index: index_stem.000.mmap, index_stem.001.mmap...
Channel index: index_stem.channel.000.mmap, ...
Direction index: index_stem.direction.000.mmap, ...
*/
CP_EXPORT int32_t can_parser_run_worker_segmented(const char* file_path,
                                                  const char* base_path,
                                                  FormatType fmt) {
    LOGGING_TRACE_ENABLED;
    if (!file_path || file_path[0] == '\0' || !base_path || base_path[0] == '\0') {
        return -1;
    }

    MMapHandle in_handle = {};
    if (!mmap_open_ro(file_path, in_handle)) {
        return -2;
    }

    const size_t in_size = in_handle.size;
    const char* src = reinterpret_cast<const char*>(in_handle.addr);
    const char* end = src + in_size;

    const FormatType detected = (fmt > 0 && fmt <= 8)
                                ? static_cast<FormatType>(fmt)
                                : FMT_UNKNOWN;
    std::array<ThreadOut, kNumThreads> tout;
    parse_input_parallel(src, end, detected, tout);
    const std::vector<ParsedEntry> parsed_entries = collect_entries_from_threads(tout);

    const std::string base = base_path;

    const int32_t data_rc = write_entries_to_segmented_mmap(
        base,
        parsed_entries);
    if (data_rc != 0) {
        mmap_close(in_handle);
        return data_rc;
    }

    mmap_close(in_handle);
    return 0;
}

CP_EXPORT int32_t can_parser_run_worker(const char* file_path,
                                        const char* data_path,
                                        const char* index_path,
                                        FormatType fmt,
                                        uint32_t check_interval) {
    (void)check_interval;
    return can_parser_run_worker_segmented(file_path, data_path, fmt);
}

CP_EXPORT int32_t can_parser_run_worker_2pass(const char* file_path,
                                              const char* data_path,
                                              const char* index_path,
                                              FormatType fmt,
                                              uint32_t max_can_ids) {
    (void)max_can_ids;
    return can_parser_run_worker_segmented(file_path, data_path, fmt);
}

CP_EXPORT int32_t can_parser_segmented_open_and_init(const char* base_path) {
    if (!base_path || base_path[0] == '\0') {
        return -1;
    }
    const std::string base = base_path;
    return open_and_init_all_segment_writers(base, base);
}

CP_EXPORT int32_t can_parser_segmented_perform_all(const ParsedEntry* entries,
                                                   uint32_t count) {
    if (count > 0 && !entries) {
        return -1;
    }
    std::vector<ParsedEntry> parsed_entries;
    if (count > 0) {
        parsed_entries.assign(entries, entries + count);
    }
    return perform_all_segment_writes(parsed_entries);
}

CP_EXPORT void can_parser_segmented_close_and_finalize() {
    close_all_segment_writers_and_finalize();
}

} // extern "C"
