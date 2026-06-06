
#include "write_data_segmented.h"

#include <array>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

#include "can_analyzer_log.h"
#include "index_mmap_layout.h"
#include "mmap_header_constract.h"
#include "mmap_wrapper.h"

static constexpr uint32_t kLastTimestampTableSize = 0x2000;
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

struct PrevRaw {
    uint8_t len = 0;
    uint8_t data[64] = {0};
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

struct IndexBuckets {
    std::unordered_map<uint32_t, std::vector<uint32_t>> can_id_rows;
    std::unordered_map<uint32_t, std::vector<uint32_t>> can_id_changed_rows;
    std::unordered_map<uint32_t, std::vector<double>> can_id_timestamps;
    std::vector<std::string> channel_table;
    std::vector<std::vector<uint32_t>> channel_rows;
    std::vector<uint32_t> direction_rows[2];
};

struct DataSegmentWriterCtx {
    std::string base;
    uint32_t seg_idx = 0;
    MMapHandle seg_handle = {};
    file_service::MmapHeaderConstract* seg_hdr = nullptr;
    ParsedEntry* seg_entries = nullptr;
    uint64_t seg_write = 0;
    uint32_t global_row_idx = 0;
    LastTimestampTable last_timestamp_by_id;
    std::unordered_map<uint32_t, PrevRaw> last_raw_by_id;
    std::unordered_map<std::string, uint8_t> channel_to_index;
};

struct SegmentedWriteGlobalState {
    DataSegmentWriterCtx writer;
    IndexBuckets buckets;
    uint64_t total_written = 0;
    uint32_t segment_count = 0;
};

static SegmentedWriteGlobalState g_segmented_state;
static bool g_segment_writers_initialized = false;

static void reset_segmented_state() {
    g_segmented_state = SegmentedWriteGlobalState{};
}

static void close_data_segment() {
    auto& ctx = g_segmented_state.writer;
    if (ctx.seg_handle.addr && ctx.seg_hdr) {
        ctx.seg_hdr->write_count = ctx.seg_write;
        ctx.seg_hdr->status = PARSER_STATUS_DONE;
    }
    g_parser_status_header = nullptr;
    mmap_close(ctx.seg_handle);
    ctx.seg_hdr = nullptr;
    ctx.seg_entries = nullptr;
    ctx.seg_write = 0;
}

static bool open_data_segment(uint32_t index) {
    auto& ctx = g_segmented_state.writer;
    std::string seg_path = make_segment_family_path(ctx.base, "", index);
    size_t seg_size = file_service::kMmapHeaderConstractSize + static_cast<size_t>(kDataSegmentCapacity) * sizeof(ParsedEntry);
    if (!mmap_create_rw(seg_path.c_str(), seg_size, ctx.seg_handle)) return false;
    ctx.seg_hdr = reinterpret_cast<file_service::MmapHeaderConstract*>(ctx.seg_handle.addr);
    ctx.seg_entries = reinterpret_cast<ParsedEntry*>(reinterpret_cast<uint8_t*>(ctx.seg_handle.addr) + file_service::kMmapHeaderConstractSize);
    g_parser_status_header = ctx.seg_hdr;
    file_service::init_mmap_header_constract(*ctx.seg_hdr, kDataSegmentCapacity, PARSER_STATUS_RUNNING);
    ctx.seg_write = 0;
    return true;
}

static int32_t open_and_init_data_segments(const std::string& base) {
    reset_segmented_state();
    auto& ctx = g_segmented_state.writer;
    ctx.base = base;
    ctx.seg_idx = 0;
    if (!open_data_segment(ctx.seg_idx)) {
        return -5;
    }
    return 0;
}

static int32_t perform_data_segment_write(
    const std::vector<ParsedEntry>& parsed_entries) {
    auto& ctx = g_segmented_state.writer;
    auto& buckets = g_segmented_state.buckets;
    for (size_t i = 0; i < parsed_entries.size(); ++i) {
        if (ctx.seg_write >= kDataSegmentCapacity) {
            return -6;
        }
        const ParsedEntry& entry = parsed_entries[i];
        ParsedEntry out_entry = entry;
        out_entry.last_timestamp = ctx.last_timestamp_by_id.update_and_get_prev(out_entry.can_id, out_entry.timestamp);

        auto it = ctx.last_raw_by_id.find(out_entry.can_id);
        if (it == ctx.last_raw_by_id.end()) {
            out_entry.changed = 0;
            PrevRaw prev;
            prev.len = out_entry.data_len;
            if (out_entry.data_len > 0) {
                memcpy(prev.data, out_entry.data, out_entry.data_len);
            }
            ctx.last_raw_by_id.emplace(out_entry.can_id, prev);
        } else {
            const PrevRaw& prev = it->second;
            const bool changed = (prev.len != out_entry.data_len)
                || (out_entry.data_len > 0 && memcmp(prev.data, out_entry.data, out_entry.data_len) != 0);
            out_entry.changed = changed ? 1 : 0;
            it->second.len = out_entry.data_len;
            if (out_entry.data_len > 0) {
                memcpy(it->second.data, out_entry.data, out_entry.data_len);
            }
        }

        ctx.seg_entries[ctx.seg_write] = out_entry;
        const uint32_t row_idx = ctx.global_row_idx++;
        buckets.can_id_rows[out_entry.can_id].push_back(row_idx);
        if (out_entry.changed == 1) {
            buckets.can_id_changed_rows[out_entry.can_id].push_back(row_idx);
        }
        buckets.can_id_timestamps[out_entry.can_id].push_back(out_entry.timestamp);

        const std::string channel_key = normalize_channel_key(out_entry.channel);
        auto ch_it = ctx.channel_to_index.find(channel_key);
        uint8_t channel_idx = 0;
        if (ch_it == ctx.channel_to_index.end()) {
            channel_idx = static_cast<uint8_t>(buckets.channel_table.size());
            ctx.channel_to_index.emplace(channel_key, channel_idx);
            buckets.channel_table.push_back(channel_key);
            buckets.channel_rows.emplace_back();
        } else {
            channel_idx = ch_it->second;
        }
        if (channel_idx < buckets.channel_rows.size()) {
            buckets.channel_rows[channel_idx].push_back(row_idx);
        }
        buckets.direction_rows[(out_entry.direction == 0) ? 0 : 1].push_back(row_idx);

        ++ctx.seg_write;
        ctx.seg_hdr->write_count = ctx.seg_write;
        ++g_segmented_state.total_written;
    }
    return 0;
}

static void close_data_segments_and_finalize() {
    auto& ctx = g_segmented_state.writer;
    close_data_segment();
    g_segmented_state.segment_count = ctx.seg_idx + 1;
}


struct CanIdIndexWriterCtx {
    std::string base;
    uint32_t seg_idx = 0;
    MMapHandle handle = {};
    IndexHeader* hdr = nullptr;
    CANIDFilter* filter_table = nullptr;
    uint32_t* row_pool = nullptr;
    uint32_t* changed_row_pool = nullptr;
    double* ts_pool = nullptr;
    uint32_t filt_idx = 0;
    uint32_t row_pool_off = 0;
    uint32_t changed_row_pool_off = 0;
    uint32_t ts_pool_off = 0;
};

struct ChannelIndexWriterCtx {
    std::string base;
    uint32_t seg_idx = 0;
    MMapHandle handle = {};
    ChannelIndexHeader* hdr = nullptr;
    ChannelFilter* table = nullptr;
    uint32_t* row_pool = nullptr;
    uint32_t tbl_idx = 0;
    uint32_t row_pool_off = 0;
};

struct DirectionIndexWriterCtx {
    std::string base;
    uint32_t seg_idx = 0;
    MMapHandle handle = {};
    DirectionIndexHeader* hdr = nullptr;
    DirectionFilter* table = nullptr;
    uint32_t* row_pool = nullptr;
    uint32_t tbl_idx = 0;
    uint32_t row_pool_off = 0;
};

static CanIdIndexWriterCtx g_canid_ctx;
static const std::vector<uint32_t> kEmptyChangedRows;
static ChannelIndexWriterCtx g_channel_ctx;
static DirectionIndexWriterCtx g_direction_ctx;

static bool is_segment_writers_ready() {
    const auto& data_ctx = g_segmented_state.writer;
    return g_segment_writers_initialized
        && data_ctx.seg_handle.addr != nullptr
        && data_ctx.seg_hdr != nullptr
        && data_ctx.seg_entries != nullptr
        && g_direction_ctx.handle.addr != nullptr
        && g_direction_ctx.hdr != nullptr
        && g_direction_ctx.table != nullptr
        && g_direction_ctx.row_pool != nullptr
        && g_channel_ctx.handle.addr != nullptr
        && g_channel_ctx.hdr != nullptr
        && g_channel_ctx.table != nullptr
        && g_channel_ctx.row_pool != nullptr
        && g_canid_ctx.handle.addr != nullptr
        && g_canid_ctx.hdr != nullptr
        && g_canid_ctx.filter_table != nullptr
        && g_canid_ctx.row_pool != nullptr
        && g_canid_ctx.changed_row_pool != nullptr
        && g_canid_ctx.ts_pool != nullptr;
}

static void reset_canid_ctx() {
    g_canid_ctx = CanIdIndexWriterCtx{};
}

static void close_canid_segment() {
    if (g_canid_ctx.handle.addr && g_canid_ctx.hdr) {
        g_canid_ctx.hdr->can_id_count = g_canid_ctx.filt_idx;
        g_canid_ctx.hdr->row_pool_size = g_canid_ctx.row_pool_off;
        g_canid_ctx.hdr->changed_row_pool_size = g_canid_ctx.changed_row_pool_off;
        g_canid_ctx.hdr->ts_pool_size = g_canid_ctx.ts_pool_off;
        g_canid_ctx.hdr->status = PARSER_STATUS_DONE;
    }
    mmap_close(g_canid_ctx.handle);
    g_canid_ctx.hdr = nullptr;
    g_canid_ctx.filter_table = nullptr;
    g_canid_ctx.row_pool = nullptr;
    g_canid_ctx.changed_row_pool = nullptr;
    g_canid_ctx.ts_pool = nullptr;
    g_canid_ctx.filt_idx = 0;
    g_canid_ctx.row_pool_off = 0;
    g_canid_ctx.changed_row_pool_off = 0;
    g_canid_ctx.ts_pool_off = 0;
}

static bool open_canid_segment(uint32_t index) {
    const std::string idx_path = make_segment_family_path(g_canid_ctx.base, "", index);
    size_t idx_size = sizeof(IndexHeader)
        + static_cast<size_t>(kIndexMaxCanIds) * sizeof(CANIDFilter)
        + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t)
        + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t)
        + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(double);
    if (!mmap_create_rw(idx_path.c_str(), idx_size, g_canid_ctx.handle)) return false;
    g_canid_ctx.hdr = reinterpret_cast<IndexHeader*>(g_canid_ctx.handle.addr);
    g_canid_ctx.hdr->can_id_count = 0;
    g_canid_ctx.hdr->row_pool_size = 0;
    g_canid_ctx.hdr->changed_row_pool_size = 0;
    g_canid_ctx.hdr->ts_pool_size = 0;
    g_canid_ctx.hdr->max_can_ids = kIndexMaxCanIds;
    g_canid_ctx.hdr->max_row_pool_size = kIndexSegmentCapacity;
    g_canid_ctx.hdr->max_changed_row_pool_size = kIndexSegmentCapacity;
    g_canid_ctx.hdr->max_ts_pool_size = kIndexSegmentCapacity;
    g_canid_ctx.hdr->status = PARSER_STATUS_RUNNING;

    g_canid_ctx.filter_table = reinterpret_cast<CANIDFilter*>(
        reinterpret_cast<uint8_t*>(g_canid_ctx.handle.addr) + sizeof(IndexHeader));
    g_canid_ctx.row_pool = reinterpret_cast<uint32_t*>(
        reinterpret_cast<uint8_t*>(g_canid_ctx.handle.addr)
        + sizeof(IndexHeader)
        + static_cast<size_t>(kIndexMaxCanIds) * sizeof(CANIDFilter));
    g_canid_ctx.changed_row_pool = reinterpret_cast<uint32_t*>(
        reinterpret_cast<uint8_t*>(g_canid_ctx.row_pool)
        + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t));
    g_canid_ctx.ts_pool = reinterpret_cast<double*>(
        reinterpret_cast<uint8_t*>(g_canid_ctx.changed_row_pool)
        + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t));

    g_canid_ctx.filt_idx = 0;
    g_canid_ctx.row_pool_off = 0;
    g_canid_ctx.changed_row_pool_off = 0;
    g_canid_ctx.ts_pool_off = 0;
    return true;
}

static int32_t open_and_init_canid_index_segments(const std::string& index_base) {
    reset_canid_ctx();
    g_canid_ctx.base = index_base;
    g_canid_ctx.seg_idx = 0;
    if (!open_canid_segment(g_canid_ctx.seg_idx)) {
        return -7;
    }
    return 0;
}

static int32_t perform_canid_index_segment_write() {
    auto& buckets = g_segmented_state.buckets;
    for (auto& kv : buckets.can_id_rows) {
        const uint32_t can_id = kv.first;
        const auto& rows = kv.second;
        const auto ts_it = buckets.can_id_timestamps.find(can_id);
        if (ts_it == buckets.can_id_timestamps.end()) {
            return -10;
        }
        const auto& timestamps = ts_it->second;
        if (timestamps.size() != rows.size()) {
            return -11;
        }

        const auto changed_it = buckets.can_id_changed_rows.find(can_id);
        const auto& changed_rows = (changed_it != buckets.can_id_changed_rows.end())
            ? changed_it->second
            : kEmptyChangedRows;

        size_t pos = 0;
        size_t changed_pos = 0;
        while (pos < rows.size()) {
            if (g_canid_ctx.row_pool_off >= kIndexSegmentCapacity
                || g_canid_ctx.changed_row_pool_off >= kIndexSegmentCapacity
                || g_canid_ctx.ts_pool_off >= kIndexSegmentCapacity
                || g_canid_ctx.filt_idx >= kIndexMaxCanIds) {
                return -8;
            }

            const uint32_t row_avail = kIndexSegmentCapacity - g_canid_ctx.row_pool_off;
            const uint32_t changed_row_avail = kIndexSegmentCapacity - g_canid_ctx.changed_row_pool_off;
            const uint32_t ts_avail = kIndexSegmentCapacity - g_canid_ctx.ts_pool_off;
            const uint32_t avail_row_ts = (row_avail < ts_avail) ? row_avail : ts_avail;
            const uint32_t avail = (avail_row_ts < changed_row_avail) ? avail_row_ts : changed_row_avail;
            const uint32_t remaining = static_cast<uint32_t>(rows.size() - pos);
            const uint32_t take = (avail < remaining) ? avail : remaining;
            if (take == 0) {
                return -9;
            }

            g_canid_ctx.filter_table[g_canid_ctx.filt_idx].can_id = can_id;
            g_canid_ctx.filter_table[g_canid_ctx.filt_idx].row_offset = g_canid_ctx.row_pool_off;
            g_canid_ctx.filter_table[g_canid_ctx.filt_idx].changed_row_offset = g_canid_ctx.changed_row_pool_off;
            g_canid_ctx.filter_table[g_canid_ctx.filt_idx].ts_offset = g_canid_ctx.ts_pool_off;
            g_canid_ctx.filter_table[g_canid_ctx.filt_idx].count = take;
            g_canid_ctx.filter_table[g_canid_ctx.filt_idx].changed_count = 0;

            for (uint32_t i = 0; i < take; i++) {
                g_canid_ctx.row_pool[g_canid_ctx.row_pool_off + i] = rows[pos + i];
                g_canid_ctx.ts_pool[g_canid_ctx.ts_pool_off + i] = timestamps[pos + i];
            }

            size_t ch = changed_pos;
            for (uint32_t i = 0; i < take; i++) {
                const uint32_t row_value = rows[pos + i];
                while (ch < changed_rows.size() && changed_rows[ch] < row_value) ++ch;
                if (ch < changed_rows.size() && changed_rows[ch] == row_value) {
                    g_canid_ctx.changed_row_pool[g_canid_ctx.changed_row_pool_off + g_canid_ctx.filter_table[g_canid_ctx.filt_idx].changed_count] = row_value;
                    ++g_canid_ctx.filter_table[g_canid_ctx.filt_idx].changed_count;
                    ++ch;
                }
            }
            changed_pos = ch;

            g_canid_ctx.row_pool_off += take;
            g_canid_ctx.changed_row_pool_off += g_canid_ctx.filter_table[g_canid_ctx.filt_idx].changed_count;
            g_canid_ctx.ts_pool_off += take;
            pos += take;
            ++g_canid_ctx.filt_idx;
        }
    }
    return 0;
}

static void close_canid_index_segments_and_finalize() {
    close_canid_segment();
    reset_canid_ctx();
}

static void reset_channel_ctx() {
    g_channel_ctx = ChannelIndexWriterCtx{};
}

static void close_channel_segment() {
    if (g_channel_ctx.handle.addr && g_channel_ctx.hdr) {
        g_channel_ctx.hdr->channel_count = g_channel_ctx.tbl_idx;
        g_channel_ctx.hdr->row_pool_size = g_channel_ctx.row_pool_off;
        g_channel_ctx.hdr->status = PARSER_STATUS_DONE;
    }
    mmap_close(g_channel_ctx.handle);
    g_channel_ctx.hdr = nullptr;
    g_channel_ctx.table = nullptr;
    g_channel_ctx.row_pool = nullptr;
    g_channel_ctx.tbl_idx = 0;
    g_channel_ctx.row_pool_off = 0;
}

static bool open_channel_segment(uint32_t index) {
    const std::string ch_path = make_segment_family_path(g_channel_ctx.base, ".channel", index);
    size_t ch_size = sizeof(ChannelIndexHeader)
        + static_cast<size_t>(kChannelIndexMaxChannels) * sizeof(ChannelFilter)
        + static_cast<size_t>(kChannelIndexSegmentCapacity) * sizeof(uint32_t);
    if (!mmap_create_rw(ch_path.c_str(), ch_size, g_channel_ctx.handle)) return false;

    g_channel_ctx.hdr = reinterpret_cast<ChannelIndexHeader*>(g_channel_ctx.handle.addr);
    g_channel_ctx.hdr->channel_count = 0;
    g_channel_ctx.hdr->row_pool_size = 0;
    g_channel_ctx.hdr->max_channels = kChannelIndexMaxChannels;
    g_channel_ctx.hdr->max_row_pool_size = kChannelIndexSegmentCapacity;
    g_channel_ctx.hdr->status = PARSER_STATUS_RUNNING;

    g_channel_ctx.table = reinterpret_cast<ChannelFilter*>(
        reinterpret_cast<uint8_t*>(g_channel_ctx.handle.addr) + sizeof(ChannelIndexHeader));
    g_channel_ctx.row_pool = reinterpret_cast<uint32_t*>(
        reinterpret_cast<uint8_t*>(g_channel_ctx.handle.addr)
        + sizeof(ChannelIndexHeader)
        + static_cast<size_t>(kChannelIndexMaxChannels) * sizeof(ChannelFilter));

    g_channel_ctx.tbl_idx = 0;
    g_channel_ctx.row_pool_off = 0;
    return true;
}

static int32_t open_and_init_channel_index_segments(const std::string& index_base) {
    reset_channel_ctx();
    g_channel_ctx.base = index_base;
    g_channel_ctx.seg_idx = 0;
    if (!open_channel_segment(g_channel_ctx.seg_idx)) {
        return -12;
    }
    return 0;
}

static int32_t perform_channel_index_segment_write() {
    auto& buckets = g_segmented_state.buckets;
    for (size_t channel_idx = 0; channel_idx < buckets.channel_rows.size(); ++channel_idx) {
        const auto& rows = buckets.channel_rows[channel_idx];
        if (rows.empty()) continue;
        const std::string& channel_name = buckets.channel_table[channel_idx];

        size_t pos = 0;
        while (pos < rows.size()) {
            if (g_channel_ctx.row_pool_off >= kChannelIndexSegmentCapacity
                || g_channel_ctx.tbl_idx >= kChannelIndexMaxChannels) {
                return -13;
            }

            const uint32_t row_avail = kChannelIndexSegmentCapacity - g_channel_ctx.row_pool_off;
            const uint32_t remaining = static_cast<uint32_t>(rows.size() - pos);
            const uint32_t take = (row_avail < remaining) ? row_avail : remaining;
            if (take == 0) {
                return -14;
            }

            g_channel_ctx.table[g_channel_ctx.tbl_idx].channel_index = static_cast<uint8_t>(channel_idx);
            memset(g_channel_ctx.table[g_channel_ctx.tbl_idx].channel, 0, sizeof(g_channel_ctx.table[g_channel_ctx.tbl_idx].channel));
            const size_t ch_copy = (channel_name.size() < sizeof(g_channel_ctx.table[g_channel_ctx.tbl_idx].channel) - 1)
                ? channel_name.size()
                : sizeof(g_channel_ctx.table[g_channel_ctx.tbl_idx].channel) - 1;
            memcpy(g_channel_ctx.table[g_channel_ctx.tbl_idx].channel, channel_name.data(), ch_copy);
            g_channel_ctx.table[g_channel_ctx.tbl_idx].row_offset = g_channel_ctx.row_pool_off;
            g_channel_ctx.table[g_channel_ctx.tbl_idx].count = take;
            g_channel_ctx.table[g_channel_ctx.tbl_idx].reserved = 0;

            for (uint32_t i = 0; i < take; ++i) {
                g_channel_ctx.row_pool[g_channel_ctx.row_pool_off + i] = rows[pos + i];
            }

            g_channel_ctx.row_pool_off += take;
            pos += take;
            ++g_channel_ctx.tbl_idx;
        }
    }
    return 0;
}

static void close_channel_index_segments_and_finalize() {
    close_channel_segment();
    reset_channel_ctx();
}

static void reset_direction_ctx() {
    g_direction_ctx = DirectionIndexWriterCtx{};
}

static void close_direction_segment() {
    if (g_direction_ctx.handle.addr && g_direction_ctx.hdr) {
        g_direction_ctx.hdr->direction_count = g_direction_ctx.tbl_idx;
        g_direction_ctx.hdr->row_pool_size = g_direction_ctx.row_pool_off;
        g_direction_ctx.hdr->status = PARSER_STATUS_DONE;
    }
    mmap_close(g_direction_ctx.handle);
    g_direction_ctx.hdr = nullptr;
    g_direction_ctx.table = nullptr;
    g_direction_ctx.row_pool = nullptr;
    g_direction_ctx.tbl_idx = 0;
    g_direction_ctx.row_pool_off = 0;
}

static bool open_direction_segment(uint32_t index) {
    const std::string dir_path = make_segment_family_path(g_direction_ctx.base, ".direction", index);
    size_t dir_size = sizeof(DirectionIndexHeader)
        + static_cast<size_t>(kDirectionIndexMaxDirections) * sizeof(DirectionFilter)
        + static_cast<size_t>(kDirectionIndexSegmentCapacity) * sizeof(uint32_t);
    if (!mmap_create_rw(dir_path.c_str(), dir_size, g_direction_ctx.handle)) return false;

    g_direction_ctx.hdr = reinterpret_cast<DirectionIndexHeader*>(g_direction_ctx.handle.addr);
    g_direction_ctx.hdr->direction_count = 0;
    g_direction_ctx.hdr->row_pool_size = 0;
    g_direction_ctx.hdr->max_directions = kDirectionIndexMaxDirections;
    g_direction_ctx.hdr->max_row_pool_size = kDirectionIndexSegmentCapacity;
    g_direction_ctx.hdr->status = PARSER_STATUS_RUNNING;

    g_direction_ctx.table = reinterpret_cast<DirectionFilter*>(
        reinterpret_cast<uint8_t*>(g_direction_ctx.handle.addr) + sizeof(DirectionIndexHeader));
    g_direction_ctx.row_pool = reinterpret_cast<uint32_t*>(
        reinterpret_cast<uint8_t*>(g_direction_ctx.handle.addr)
        + sizeof(DirectionIndexHeader)
        + static_cast<size_t>(kDirectionIndexMaxDirections) * sizeof(DirectionFilter));

    g_direction_ctx.tbl_idx = 0;
    g_direction_ctx.row_pool_off = 0;
    return true;
}

static int32_t open_and_init_direction_index_segments(const std::string& index_base) {
    reset_direction_ctx();
    g_direction_ctx.base = index_base;
    g_direction_ctx.seg_idx = 0;
    if (!open_direction_segment(g_direction_ctx.seg_idx)) {
        return -15;
    }
    return 0;
}

static int32_t perform_direction_index_segment_write() {
    auto& buckets = g_segmented_state.buckets;
    for (uint8_t direction = 0; direction < 2; ++direction) {
        const auto& rows = buckets.direction_rows[direction];
        if (rows.empty()) continue;

        size_t pos = 0;
        while (pos < rows.size()) {
            if (g_direction_ctx.row_pool_off >= kDirectionIndexSegmentCapacity
                || g_direction_ctx.tbl_idx >= kDirectionIndexMaxDirections) {
                return -16;
            }

            const uint32_t row_avail = kDirectionIndexSegmentCapacity - g_direction_ctx.row_pool_off;
            const uint32_t remaining = static_cast<uint32_t>(rows.size() - pos);
            const uint32_t take = (row_avail < remaining) ? row_avail : remaining;
            if (take == 0) {
                return -17;
            }

            g_direction_ctx.table[g_direction_ctx.tbl_idx].direction = direction;
            memset(g_direction_ctx.table[g_direction_ctx.tbl_idx].padding0, 0, sizeof(g_direction_ctx.table[g_direction_ctx.tbl_idx].padding0));
            g_direction_ctx.table[g_direction_ctx.tbl_idx].row_offset = g_direction_ctx.row_pool_off;
            g_direction_ctx.table[g_direction_ctx.tbl_idx].count = take;
            g_direction_ctx.table[g_direction_ctx.tbl_idx].reserved = 0;

            for (uint32_t i = 0; i < take; ++i) {
                g_direction_ctx.row_pool[g_direction_ctx.row_pool_off + i] = rows[pos + i];
            }

            g_direction_ctx.row_pool_off += take;
            pos += take;
            ++g_direction_ctx.tbl_idx;
        }
    }
    return 0;
}

static void close_direction_index_segments_and_finalize() {
    close_direction_segment();
    reset_direction_ctx();
}

int32_t open_and_init_all_segment_writers(
    const std::string& base) {
    g_segment_writers_initialized = false;

    int32_t rc = open_and_init_data_segments(base);
    if (rc != 0) return rc;

    rc = open_and_init_direction_index_segments(base);
    if (rc != 0) return rc;

    rc = open_and_init_channel_index_segments(base);
    if (rc != 0) return rc;

    rc = open_and_init_canid_index_segments(base);
    if (rc != 0) return rc;

    g_segment_writers_initialized = true;

    return 0;
}

int32_t perform_all_segment_writes(
    const std::vector<ParsedEntry>& parsed_entries) {
    if (!is_segment_writers_ready()) {
        return -18;
    }

    int32_t rc = perform_data_segment_write(parsed_entries);
    if (rc != 0) return rc;

    rc = perform_direction_index_segment_write();
    if (rc != 0) return rc;

    rc = perform_channel_index_segment_write();
    if (rc != 0) return rc;

    rc = perform_canid_index_segment_write();
    if (rc != 0) return rc;

    return 0;
}

void close_all_segment_writers_and_finalize() {
    close_data_segments_and_finalize();
    close_direction_index_segments_and_finalize();
    close_channel_index_segments_and_finalize();
    close_canid_index_segments_and_finalize();
    g_segment_writers_initialized = false;
}

int32_t write_entries_to_segmented_mmap(
    const std::string& base,
    const std::vector<ParsedEntry>& parsed_entries) {

    const int32_t init_rc = open_and_init_all_segment_writers(base);
    if (init_rc != 0) {
        close_all_segment_writers_and_finalize();
        reset_segmented_state();
        return init_rc;
    }

    const int32_t write_rc = perform_all_segment_writes(parsed_entries);
    if (write_rc != 0) {
        close_all_segment_writers_and_finalize();
        reset_segmented_state();
        return write_rc;
    }

    close_all_segment_writers_and_finalize();

    CBCM_DEBUG("segmented worker done: total_entries=%llu segments=%u",
               static_cast<unsigned long long>(g_segmented_state.total_written),
               g_segmented_state.segment_count);

    reset_segmented_state();
    return 0;
}