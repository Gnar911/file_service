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
#include "mmap_header_constract.h"

#ifndef LOGGING_TRACE_ENABLED
#if defined(__LW_TRACE)
#define LOGGING_TRACE_ENABLED __LW_TRACE()
#else
#define LOGGING_TRACE_ENABLED ;
#endif
#endif

// ─────────────────────────────────────────────────────────────────────────────
// Packed result struct  (matches ctypes Structure with _pack_ = 1)
// ─────────────────────────────────────────────────────────────────────────────
#pragma pack(push, 1)
struct ParsedEntry {
    uint32_t line_number;       //  4
    double   timestamp;         //  8
    double   last_timestamp;    //  8  previous timestamp of same CAN ID
    uint32_t can_id;            //  4  (0xFFFFFFFF = parse failure)
    uint8_t  direction;         //  1  (0 = Rx, 1 = Tx)
    uint8_t  data_len;          //  1  (0-64)
    uint8_t  changed;           //  1  (0 = same as previous CAN-ID payload, 1 = changed)
    uint8_t  data[64];          // 64
    char     channel[16];       // 16  (null-terminated)
};
// Total: 4+8+8+4+1+1+1+64+16 = 107 bytes
#pragma pack(pop)

static constexpr uint32_t kLastTimestampTableSize = 0x2000;

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

// ─────────────────────────────────────────────────────────────────────────────
// String/token helpers
// ─────────────────────────────────────────────────────────────────────────────

static inline bool is_hex_char(char c) {
    return (c >= '0' && c <= '9') ||
           (c >= 'a' && c <= 'f') ||
           (c >= 'A' && c <= 'F');
}

static inline bool is_hex_byte_sv(const char* p, size_t n) {
    return n == 2 && is_hex_char(p[0]) && is_hex_char(p[1]);
}

static inline int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return c - 'A' + 10;
}

static inline bool is_valid_dlc(int v) {
    return v == 0 || v == 1 || v == 2 || v == 3 || v == 4 || v == 5 ||
           v == 6 || v == 7 || v == 8 || v == 12 || v == 16 || v == 20 ||
           v == 24 || v == 32 || v == 48 || v == 64;
}

// Tokenise a line by whitespace; stores <ptr, len> pairs into out.
// Returns number of tokens.
struct Tok { const char* p; size_t n; };
static int tokenize(const char* line, size_t len,
                    Tok* out, int max_tokens) {
    int count = 0;
    const char* end = line + len;
    const char* p   = line;
    while (p < end && count < max_tokens) {
        // skip whitespace
        while (p < end && (unsigned char)*p <= ' ') ++p;
        if (p >= end) break;
        const char* start = p;
        while (p < end && (unsigned char)*p > ' ') ++p;
        out[count].p = start;
        out[count].n = (size_t)(p - start);
        ++count;
    }
    return count;
}

// Token string compare (case-sensitive)
static inline bool tok_eq(const Tok& t, const char* s) {
    size_t l = strlen(s);
    return t.n == l && memcmp(t.p, s, l) == 0;
}

// Token string compare case-insensitive
static inline bool tok_eqi(const Tok& t, const char* s) {
    if (t.n != strlen(s)) return false;
    for (size_t i = 0; i < t.n; i++)
        if (tolower((unsigned char)t.p[i]) != tolower((unsigned char)s[i])) return false;
    return true;
}

// Fast float parser for CAN timestamps (e.g. "123.456789").
// Handles unsigned decimal with optional fractional part — no scientific
// notation, no locale, no sign, no NaN/Inf.  Avoids memcpy + null-term +
// strtod overhead (~2.5M calls on a 5M-line file).
static bool tok_double(const Tok& t, double& out) {
    if (t.n == 0) return false;
    const char* p   = t.p;
    const char* end = p + t.n;

    // Integer part
    if (!isdigit((unsigned char)*p)) return false;
    uint64_t int_part = 0;
    while (p < end && isdigit((unsigned char)*p))
        int_part = int_part * 10 + (uint64_t)(*p++ - '0');

    if (p == end) {                     // no fractional part
        out = (double)int_part;
        return true;
    }

    if (*p != '.') return false;        // not a decimal point → fail
    ++p;                                // skip '.'

    // Fractional part — accumulate digits and track divisor
    uint64_t frac_part = 0;
    double   divisor   = 1.0;
    while (p < end && isdigit((unsigned char)*p)) {
        frac_part = frac_part * 10 + (uint64_t)(*p++ - '0');
        divisor *= 10.0;
    }

    if (p != end) return false;         // trailing non-digit chars → fail

    out = (double)int_part + (double)frac_part / divisor;
    return true;
}

// Parse hex CAN ID (strip trailing x/X, leading 0x); returns false on failure
static bool tok_can_id(const Tok& t, uint32_t& out) {
    if (t.n == 0) return false;
    const char* p = t.p;
    size_t      n = t.n;
    // strip trailing x/X
    while (n > 0 && (p[n-1] == 'x' || p[n-1] == 'X')) --n;
    // skip 0x prefix
    if (n >= 2 && p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) { p += 2; n -= 2; }
    if (n == 0) return false;
    uint32_t val = 0;
    for (size_t i = 0; i < n; i++) {
        char c = p[i];
        if (!is_hex_char(c)) return false;
        val = (val << 4) | (uint32_t)hex_digit(c);
    }
    out = val;
    return true;
}

// Parse decimal uint from token
static bool tok_uint(const Tok& t, unsigned& out) {
    if (t.n == 0) return false;
    unsigned val = 0;
    for (size_t i = 0; i < t.n; i++) {
        if (!isdigit((unsigned char)t.p[i])) return false;
        val = val * 10u + (unsigned)(t.p[i] - '0');
    }
    out = val;
    return true;
}

// Safe strncpy null-terminating at dst[max-1]
static void safe_strcpy(char* dst, size_t dst_max, const char* src, size_t src_len) {
    size_t copy = (src_len < dst_max - 1) ? src_len : dst_max - 1;
    memcpy(dst, src, copy);
    dst[copy] = '\0';
}

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

// Fill ParsedEntry data[] from token array starting at dlc_idx+1, for dlc bytes
static bool fill_data(ParsedEntry& e, const Tok* toks, int ntoks,
                      int dlc_idx, int dlc) {
    if (dlc_idx + dlc >= ntoks) return false;
    e.data_len = (uint8_t)(dlc < 64 ? dlc : 64);
    for (int i = 0; i < e.data_len; i++) {
        const Tok& bt = toks[dlc_idx + 1 + i];
        if (!is_hex_byte_sv(bt.p, bt.n)) return false;
        e.data[i] = (uint8_t)((hex_digit(bt.p[0]) << 4) | hex_digit(bt.p[1]));
    }
    // memset zero-fill removed for performance — data_len marks valid bytes
    return true;
}

// Scan tokens from start_from looking for a valid DLC followed by a hex byte.
// Returns token index of DLC, or -1.
static int find_dlc_idx(const Tok* toks, int ntoks, int start_from) {
    for (int i = start_from; i < ntoks - 1; i++) {
        const Tok& t = toks[i];
        if (t.n == 0 || t.n > 2) continue;
        // must be all decimal digits
        bool all_dec = true;
        for (size_t k = 0; k < t.n; k++)
            if (!isdigit((unsigned char)t.p[k])) { all_dec = false; break; }
        if (!all_dec) continue;
        int val = 0;
        for (size_t k = 0; k < t.n; k++) val = val*10 + (t.p[k]-'0');
        if (is_valid_dlc(val) && is_hex_byte_sv(toks[i+1].p, toks[i+1].n))
            return i;
    }
    return -1;
}

// Find index of token equal to s (case-insensitive), return -1 if not found
static int find_tok(const Tok* toks, int ntoks, const char* s) {
    for (int i = 0; i < ntoks; i++)
        if (tok_eqi(toks[i], s)) return i;
    return -1;
}

// Find direction token; returns dir_idx, sets dir (0=Rx 1=Tx), or -1
static int find_dir(const Tok* toks, int ntoks, uint8_t& dir) {
    for (int i = 0; i < ntoks; i++) {
        if (tok_eqi(toks[i], "Rx")) { dir = 0; return i; }
        if (tok_eqi(toks[i], "Tx")) { dir = 1; return i; }
    }
    return -1;
}

// ─────────────────────────────────────────────────────────────────────────────
// Per-format parsers
// Each takes pre-tokenised line and tries to fill ParsedEntry.
// Returns true on success.
// ─────────────────────────────────────────────────────────────────────────────

// FMT_CANOE: "ts ... CANFD chan ... Tx/Rx CAN_ID [name] flags DLC bytes ..."
static bool parse_canoe(const Tok* toks, int ntoks,
                        uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 8) return false;
    double ts;
    if (!tok_double(toks[0], ts)) return false;

    int canfd_idx = find_tok(toks, ntoks, "CANFD");
    if (canfd_idx < 0 || canfd_idx + 1 >= ntoks) return false;

    uint8_t dir;
    int dir_idx = find_dir(toks, ntoks, dir);
    if (dir_idx < 0 || dir_idx + 1 >= ntoks) return false;

    uint32_t can_id;
    if (!tok_can_id(toks[dir_idx + 1], can_id)) return false;

    int dlc_idx = find_dlc_idx(toks, ntoks, dir_idx + 2);
    if (dlc_idx < 0) return false;

    int dlc = 0;
    for (size_t k = 0; k < toks[dlc_idx].n; k++) dlc = dlc*10 + (toks[dlc_idx].p[k]-'0');

    if (!fill_data(e, toks, ntoks, dlc_idx, dlc)) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;

    // Channel: token after CANFD
    safe_strcpy(e.channel, sizeof(e.channel), toks[canfd_idx+1].p, toks[canfd_idx+1].n);
    return true;
}

// FMT_CANOE_FULL: "DATE TIME ts ... CANFD chan CAN_ID Tx/Rx ... DLC bytes"
// tokens[0]=date, tokens[1]=time, tokens[2]=timestamp, tokens[3..]=CANFD chan
static bool parse_canoe_full(const Tok* toks, int ntoks,
                              uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 10) return false;
    // tokens[0] must contain '-' (date)
    if (!memchr(toks[0].p, '-', toks[0].n)) return false;
    double ts;
    if (!tok_double(toks[2], ts)) return false;

    int canfd_idx = find_tok(toks, ntoks, "CANFD");
    if (canfd_idx < 0 || canfd_idx + 1 >= ntoks) return false;

    uint8_t dir;
    int dir_idx = find_dir(toks, ntoks, dir);
    if (dir_idx < 0 || dir_idx - 1 < 0) return false;

    // CAN ID is token before Tx/Rx
    uint32_t can_id;
    if (!tok_can_id(toks[dir_idx - 1], can_id)) return false;

    int dlc_idx = find_dlc_idx(toks, ntoks, dir_idx + 1);
    if (dlc_idx < 0) return false;

    int dlc = 0;
    for (size_t k = 0; k < toks[dlc_idx].n; k++) dlc = dlc*10 + (toks[dlc_idx].p[k]-'0');

    if (!fill_data(e, toks, ntoks, dlc_idx, dlc)) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;

    // Channel = token after CANFD
    safe_strcpy(e.channel, sizeof(e.channel), toks[canfd_idx+1].p, toks[canfd_idx+1].n);
    return true;
}

// FMT_CANOE_CMP: "ts chan CAN_ID Tx/Rx d DLC bytes..."
// tokens[0]=ts, [1]=chan, [2]=id, [3]=Tx/Rx, [4]="d", [5]=dlc, [6..]=bytes
static bool parse_canoe_compact(const Tok* toks, int ntoks,
                                 uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 7) return false;
    double ts;
    if (!tok_double(toks[0], ts)) return false;
    if (!tok_eqi(toks[4], "d")) return false;

    uint8_t dir;
    if      (tok_eqi(toks[3], "Rx")) dir = 0;
    else if (tok_eqi(toks[3], "Tx")) dir = 1;
    else return false;

    uint32_t can_id;
    if (!tok_can_id(toks[2], can_id)) return false;

    unsigned dlc;
    if (!tok_uint(toks[5], dlc) || !is_valid_dlc((int)dlc)) return false;
    if (6 + (int)dlc > ntoks) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;
    e.data_len    = (uint8_t)dlc;
    safe_strcpy(e.channel, sizeof(e.channel), toks[1].p, toks[1].n);

    for (unsigned i = 0; i < dlc && i < 64; i++) {
        const Tok& bt = toks[6 + i];
        if (!is_hex_byte_sv(bt.p, bt.n)) return false;
        e.data[i] = (uint8_t)((hex_digit(bt.p[0]) << 4) | hex_digit(bt.p[1]));
    }
    // memset zero-fill removed for performance — data_len marks valid bytes
    return true;
}

// FMT_CANCMD: "date time ts 1 CANFD 1 CAN_ID Tx/Rx name X DLC bytes"
// tokens[0]=date, [1]=time, [2]=ts, [3..5]=bus/flags, [6]=CAN_ID, [7]=dir, [8]=name ...
static bool parse_cancmd(const Tok* toks, int ntoks,
                         uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 10) return false;
    if (!memchr(toks[0].p, '-', toks[0].n)) return false;  // date check
    double ts;
    if (!tok_double(toks[2], ts)) return false;

    uint8_t dir;
    int dir_idx = find_dir(toks, ntoks, dir);
    if (dir_idx < 0 || dir_idx < 1) return false;

    uint32_t can_id;
    if (!tok_can_id(toks[dir_idx - 1], can_id)) return false;

    int dlc_idx = find_dlc_idx(toks, ntoks, dir_idx + 1);
    if (dlc_idx < 0) return false;

    int dlc = 0;
    for (size_t k = 0; k < toks[dlc_idx].n; k++) dlc = dlc*10 + (toks[dlc_idx].p[k]-'0');

    if (!fill_data(e, toks, ntoks, dlc_idx, dlc)) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;
    e.channel[0]  = '\0';
    return true;
}

// FMT_FILTER: "ts SOMETHING NUM Tx/Rx CAN_ID name DLC bytes"
// tokens[0]=ts, [1]=channel-like, [2]=num, [3]=dir, [4]=can_id, [5]=name, [6]=dlc, ...
static bool parse_filter_log(const Tok* toks, int ntoks,
                              uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 7) return false;
    double ts;
    if (!tok_double(toks[0], ts)) return false;

    uint8_t dir;
    int dir_idx = find_dir(toks, ntoks, dir);
    if (dir_idx < 0 || dir_idx + 1 >= ntoks) return false;

    uint32_t can_id;
    if (!tok_can_id(toks[dir_idx + 1], can_id)) return false;

    int dlc_idx = find_dlc_idx(toks, ntoks, dir_idx + 2);
    if (dlc_idx < 0) return false;

    int dlc = 0;
    for (size_t k = 0; k < toks[dlc_idx].n; k++) dlc = dlc*10 + (toks[dlc_idx].p[k]-'0');

    if (!fill_data(e, toks, ntoks, dlc_idx, dlc)) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;
    e.channel[0]  = '\0';
    return true;
}

// FMT_CANSUKE: "ts NUM CAN_ID Tx/Rx name DLC bytes"
// tokens[0]=ts, [1]=channel-num, [2]=can_id, [3]=dir, [4]=name, [5+]=dlc+bytes
static bool parse_cansuke(const Tok* toks, int ntoks,
                           uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 6) return false;
    double ts;
    if (!tok_double(toks[0], ts)) return false;

    uint8_t dir;
    int dir_idx = find_dir(toks, ntoks, dir);
    if (dir_idx < 0 || dir_idx < 1) return false;

    uint32_t can_id;
    if (!tok_can_id(toks[dir_idx - 1], can_id)) return false;

    // DLC is the first decimal after dir_idx
    int dlc_idx = -1;
    for (int i = dir_idx + 1; i < ntoks - 1; i++) {
        const Tok& t = toks[i];
        bool all_dec = true;
        for (size_t k = 0; k < t.n; k++)
            if (!isdigit((unsigned char)t.p[k])) { all_dec = false; break; }
        if (all_dec) { dlc_idx = i; break; }
    }
    if (dlc_idx < 0) return false;

    int dlc = 0;
    for (size_t k = 0; k < toks[dlc_idx].n; k++) dlc = dlc*10 + (toks[dlc_idx].p[k]-'0');
    if (!is_valid_dlc(dlc)) return false;

    if (!fill_data(e, toks, ntoks, dlc_idx, dlc)) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;
    e.channel[0]  = '\0';
    return true;
}

// FMT_CANCMD_T2: TAB-separated
// cols[0]=ts, [1]=ch, [2]=id, [3]=name, [4]=dlc, [5]=data bytes, [6]=dir
static bool parse_cancmd_t2(const char* line, size_t len,
                             uint32_t line_num, ParsedEntry& e) {
    // LOGGING_TRACE_ENABLED;
    // Split by TAB
    const char* col[16];
    size_t      col_len[16];
    int         ncols = 0;
    const char* p = line;
    const char* end = line + len;
    const char* start = p;
    while (p <= end && ncols < 15) {
        if (p == end || *p == '\t') {
            col[ncols]     = start;
            col_len[ncols] = (size_t)(p - start);
            ++ncols;
            start = p + 1;
        }
        ++p;
    }
    if (ncols < 7) return false;

    // Trim whitespace from cols
    auto trim = [](const char*& s, size_t& n) {
        while (n > 0 && (unsigned char)*s <= ' ') { ++s; --n; }
        while (n > 0 && (unsigned char)s[n-1] <= ' ') --n;
    };
    for (int i = 0; i < ncols; i++) trim(col[i], col_len[i]);

    // Timestamp
    Tok t0{col[0], col_len[0]};
    double ts;
    // if purely digits → milliseconds
    bool all_digit = true;
    for (size_t k = 0; k < col_len[0]; k++)
        if (!isdigit((unsigned char)col[0][k])) { all_digit = false; break; }
    if (all_digit) {
        unsigned long long ms = 0;
        for (size_t k = 0; k < col_len[0]; k++) ms = ms*10 + (col[0][k]-'0');
        ts = (double)ms / 1000.0;
    } else {
        if (!tok_double(t0, ts)) return false;
    }

    Tok tid{col[2], col_len[2]};
    uint32_t can_id;
    if (!tok_can_id(tid, can_id)) return false;

    // Direction
    uint8_t dir;
    if      (col_len[6] >= 2 && (col[6][0]=='R'||col[6][0]=='r') && (col[6][1]=='x'||col[6][1]=='X')) dir = 0;
    else if (col_len[6] >= 2 && (col[6][0]=='T'||col[6][0]=='t') && (col[6][1]=='x'||col[6][1]=='X')) dir = 1;
    else return false;

    // DLC: col[4] - may be hex DLC code (A=16, B=20, ...)
    static const int CANFD_DLC_MAP[16] = {0,1,2,3,4,5,6,7,8,12,16,20,24,32,48,64};
    int dlc;
    if (col_len[4] == 1) {
        char c = col[4][0];
        if (c >= '0' && c <= '9') dlc = c - '0';
        else if (c >= 'A' && c <= 'F') dlc = CANFD_DLC_MAP[10 + (c-'A')];
        else if (c >= 'a' && c <= 'f') dlc = CANFD_DLC_MAP[10 + (c-'a')];
        else return false;
    } else {
        unsigned v;
        Tok tdlc{col[4], col_len[4]};
        if (!tok_uint(tdlc, v)) return false;
        dlc = (int)v;
    }
    if (!is_valid_dlc(dlc)) return false;

    // Data bytes in col[5] separated by spaces
    Tok data_toks[64];
    int nb = tokenize(col[5], col_len[5], data_toks, 64);
    if (nb < dlc) dlc = nb; // tolerate fewer bytes than declared
    e.data_len = (uint8_t)dlc;
    for (int i = 0; i < dlc; i++) {
        if (!is_hex_byte_sv(data_toks[i].p, data_toks[i].n)) return false;
        e.data[i] = (uint8_t)((hex_digit(data_toks[i].p[0]) << 4) | hex_digit(data_toks[i].p[1]));
    }
    // memset zero-fill removed for performance — data_len marks valid bytes

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;
    safe_strcpy(e.channel, sizeof(e.channel), col[1], col_len[1]);
    return true;
}

// FMT_CANCMD_T3: "timediff chan CAN_ID hex_flag bytes... Tx/Rx TYPE chan"
// tokens[0]=timediff(ms), [1]=chan, [2]=can_id, then data until Tx/Rx
static bool parse_cancmd_t3(const Tok* toks, int ntoks,
                             uint32_t line_num, ParsedEntry& e) {
    if (ntoks < 6) return false;
    double ts;
    {
        unsigned ms;
        if (!tok_uint(toks[0], ms)) return false;
        ts = ms * 0.001;
    }

    uint32_t can_id;
    if (!tok_can_id(toks[2], can_id)) return false;

    uint8_t dir;
    int dir_idx = find_dir(toks, ntoks, dir);
    if (dir_idx < 0) return false;

    int dlc_idx = find_dlc_idx(toks, ntoks, 3);
    if (dlc_idx < 0 || dlc_idx >= dir_idx) return false;

    int dlc = 0;
    for (size_t k = 0; k < toks[dlc_idx].n; k++) dlc = dlc*10 + (toks[dlc_idx].p[k]-'0');

    if (!fill_data(e, toks, ntoks, dlc_idx, dlc)) return false;

    e.line_number = line_num;
    e.timestamp   = ts;
    e.can_id      = can_id;
    e.direction   = dir;
    safe_strcpy(e.channel, sizeof(e.channel), toks[1].p, toks[1].n);
    return true;
}

// ─────────────────────────────────────────────────────────────────────────────
// Try all parsers in order; return matched format or FMT_UNKNOWN
// ─────────────────────────────────────────────────────────────────────────────
static FormatType detect_and_parse(const char* line, size_t len,
                                   uint32_t line_num, ParsedEntry& e) {
    // LOGGING_TRACE_ENABLED;
    // Fast check: skip obviously empty/comment lines
    const char* p = line;
    while (p < line + len && (unsigned char)*p <= ' ') ++p;
    if (p >= line + len) return FMT_UNKNOWN;
    if (*p == '/' || *p == '#' || *p == ';') return FMT_UNKNOWN;

    e.changed = 0;
    e.last_timestamp = e.timestamp;

    // Check for TAB-separated (FMT_CANCMD_T2) first - it's structurally distinct
    for (size_t i = 0; i < len; i++) {
        if (line[i] == '\t') {
            if (parse_cancmd_t2(line, len, line_num, e)) return FMT_CANCMD_T2;
            break; // if has tabs but failed, don't try others
        }
    }

    // Tokenise once for the remaining parsers
    Tok toks[256];
    int ntoks = tokenize(line, len, toks, 256);
    if (ntoks < 4) return FMT_UNKNOWN;

    // Try parsers in priority order (mirrors Python pattern_parsers list)
    if (parse_canoe       (toks, ntoks, line_num, e)) return FMT_CANOE;
    if (parse_canoe_full  (toks, ntoks, line_num, e)) return FMT_CANOE_FULL;
    if (parse_canoe_compact(toks, ntoks, line_num, e)) return FMT_CANOE_CMP;
    if (parse_cancmd      (toks, ntoks, line_num, e)) return FMT_CANCMD;
    if (parse_filter_log  (toks, ntoks, line_num, e)) return FMT_FILTER;
    if (parse_cansuke     (toks, ntoks, line_num, e)) return FMT_CANSUKE;
    if (parse_cancmd_t3   (toks, ntoks, line_num, e)) return FMT_CANCMD_T3;
    return FMT_UNKNOWN;
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

// ─────────────────────────────────────────────────────────────────────────────
// CAN-ID index mmap structures
// ─────────────────────────────────────────────────────────────────────────────
#pragma pack(push, 1)
struct IndexHeader {
    uint32_t can_id_count;       //  4  unique CAN IDs written (set after parse)
    uint32_t row_pool_size;      //  4  total row indices in row-index pool
    uint32_t changed_row_pool_size; // 4 total row indices in changed-row-index pool
    uint32_t ts_pool_size;       //  4  total timestamps in timestamp pool
    uint32_t max_can_ids;        //  4  CANIDFilter table capacity (set at creation)
    uint32_t max_row_pool_size;  //  4  row-index pool capacity   (set at creation)
    uint32_t max_changed_row_pool_size; // 4 changed-row-index pool capacity
    uint32_t max_ts_pool_size;   //  4  timestamp pool capacity   (set at creation)
    uint32_t status;             //  4  DataStatus
    uint8_t  padding[4];         //  4
    // total: 40 bytes
};

struct CANIDFilter {
    uint32_t can_id;       //  4  CAN ID (integer, hex-parsed)
    uint64_t row_offset;   //  8  start index into row-index pool (uint32_t units)
    uint64_t changed_row_offset; // 8 start index into changed-row-index pool (uint32_t units)
    uint64_t ts_offset;    //  8  start index into timestamp pool (double units)
    uint32_t count;        //  4  number of elements for row/timestamp pools
    uint32_t changed_count; // 4 number of elements for changed-row pool
    // total: 36 bytes
};

struct ChannelIndexHeader {
    uint32_t channel_count;        // channels written in this segment
    uint32_t row_pool_size;        // total channel-row indices in row pool
    uint32_t max_channels;         // channel table capacity
    uint32_t max_row_pool_size;    // row pool capacity
    uint32_t status;               // DataStatus
    uint8_t  padding[12];          // total header = 32 bytes
};

struct ChannelFilter {
    uint8_t  channel_index;        // 0=can0, 1=can1, 2=can2 ...
    char     channel[15];          // normalized channel name
    uint64_t row_offset;           // start index into row pool (uint32 units)
    uint32_t count;                // number of rows for this channel in segment
    uint32_t reserved;
};

struct DirectionIndexHeader {
    uint32_t direction_count;      // directions written in this segment
    uint32_t row_pool_size;        // total direction-row indices in row pool
    uint32_t max_directions;       // direction table capacity
    uint32_t max_row_pool_size;    // row pool capacity
    uint32_t status;               // DataStatus
    uint8_t  padding[12];          // total header = 32 bytes
};

struct DirectionFilter {
    uint8_t  direction;            // 0=Rx, 1=Tx
    uint8_t  padding0[7];
    uint64_t row_offset;           // start index into row pool (uint32 units)
    uint32_t count;                // number of rows for this direction in segment
    uint32_t reserved;
};
#pragma pack(pop)

// ─────────────────────────────────────────────────────────────────────────────
// Public C API
// ─────────────────────────────────────────────────────────────────────────────
extern "C" {
CP_EXPORT DataStatus get_status() {
    if (g_parser_status_header == nullptr) {
        return DATA_STATUS_DONE;
    }
    return static_cast<DataStatus>(g_parser_status_header->status);
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

// #if defined(_WIN32)

// #include <Windows.h>

// using IPCSignalHandle = HANDLE;

// #else

// using IPCSignalHandle = int;

// #endif

// inline void ipc_signal(
//     IPCSignalHandle handle)
// {
// #if defined(_WIN32)

//     SetEvent(handle);

// #else

//     uint8_t b = 1;

//     write(handle, &b, 1);

// #endif
// }







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
                                                  const char* data_base_path,
                                                  const char* index_base_path,
                                                  FormatType fmt) {
    LOGGING_TRACE_ENABLED;
    constexpr uint64_t kProgressLogEvery = 400'000;
    constexpr int kNumThreads = 4;
    constexpr uint32_t kDataSegmentCapacity = 1'000'000;
    constexpr uint32_t kIndexSegmentCapacity = 1'000'000;
    constexpr uint32_t kIndexMaxCanIds = 4096;
    constexpr uint32_t kChannelIndexSegmentCapacity = 1'000'000;
    constexpr uint32_t kChannelIndexMaxChannels = 64;
    constexpr uint32_t kDirectionIndexSegmentCapacity = 1'000'000;
    constexpr uint32_t kDirectionIndexMaxDirections = 8;
    if (!file_path || file_path[0] == '\0' || !data_base_path || data_base_path[0] == '\0') {
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

    struct ByteRange { const char* begin; const char* end; };
    ByteRange ranges[kNumThreads];
    {
        const char* prev_end = src;
        const size_t chunk_bytes = in_size / static_cast<size_t>(kNumThreads);
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

    struct ThreadOut {
        EntryBuf buf;
        uint64_t parsed = 0;
    };
    ThreadOut tout[kNumThreads];

    {
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
                        // if ((tout[t].parsed % kProgressLogEvery) == 0) {
                        //     CBCM_DEBUG("seg-thread=%d parsed=%llu line=%u",
                        //                t,
                        //                static_cast<unsigned long long>(tout[t].parsed),
                        //                lnum);
                        // }
                    }

                    cur = eol ? eol + 1 : chunk_end;
                }
            });
        }
        for (auto& th : threads) th.join();
    }

    const std::string base = data_base_path;
    const bool has_index = (index_base_path && index_base_path[0] != '\0');
    const std::string index_base = has_index ? std::string(index_base_path) : std::string();

    auto make_segment_path = [&](uint32_t seg_idx) -> std::string {
        std::string stem = base;
        if (stem.size() >= 5 && stem.compare(stem.size() - 5, 5, ".mmap") == 0) {
            stem.resize(stem.size() - 5);
        }
        char num[16];
        snprintf(num, sizeof(num), ".%03u.mmap", seg_idx);
        return stem + num;
    };

    auto make_index_segment_path = [&](uint32_t seg_idx) -> std::string {
        std::string stem = index_base;
        if (stem.size() >= 5 && stem.compare(stem.size() - 5, 5, ".mmap") == 0) {
            stem.resize(stem.size() - 5);
        }
        char num[16];
        snprintf(num, sizeof(num), ".%03u.mmap", seg_idx);
        return stem + num;
    };

    auto make_channel_index_segment_path = [&](uint32_t seg_idx) -> std::string {
        std::string stem = index_base;
        if (stem.size() >= 5 && stem.compare(stem.size() - 5, 5, ".mmap") == 0) {
            stem.resize(stem.size() - 5);
        }
        stem += ".channel";
        char num[16];
        snprintf(num, sizeof(num), ".%03u.mmap", seg_idx);
        return stem + num;
    };

    auto make_direction_index_segment_path = [&](uint32_t seg_idx) -> std::string {
        std::string stem = index_base;
        if (stem.size() >= 5 && stem.compare(stem.size() - 5, 5, ".mmap") == 0) {
            stem.resize(stem.size() - 5);
        }
        stem += ".direction";
        char num[16];
        snprintf(num, sizeof(num), ".%03u.mmap", seg_idx);
        return stem + num;
    };

    uint32_t seg_idx = 0;
    MMapHandle seg_handle = {};
    file_service::MmapHeaderConstract* seg_hdr = nullptr;
    ParsedEntry* seg_entries = nullptr;
    uint64_t seg_write = 0;
    uint64_t total_written = 0;
    std::unordered_map<uint32_t, std::vector<uint32_t>> global_can_id_index;
    std::unordered_map<uint32_t, std::vector<uint32_t>> global_can_id_changed_index;
    std::unordered_map<uint32_t, std::vector<double>> global_can_id_timestamps;
    std::unordered_map<std::string, uint8_t> channel_to_index;
    std::vector<std::string> channel_table;
    std::vector<std::vector<uint32_t>> global_channel_rows;
    std::array<std::vector<uint32_t>, 2> global_direction_rows;
    uint32_t global_row_idx = 0;
    LastTimestampTable last_timestamp_by_id;
    struct PrevRaw {
        uint8_t len = 0;
        uint8_t data[64] = {0};
    };
    std::unordered_map<uint32_t, PrevRaw> last_raw_by_id;

    auto close_segment = [&]() {
        if (seg_handle.addr && seg_hdr) {
            seg_hdr->write_count = seg_write;
            seg_hdr->status = DATA_STATUS_DONE;
        }
        g_parser_status_header = nullptr;
        mmap_close(seg_handle);
        seg_hdr = nullptr;
        seg_entries = nullptr;
        seg_write = 0;
    };

    auto open_segment = [&](uint32_t index) -> bool {
        std::string seg_path = make_segment_path(index);
        size_t seg_size = file_service::kMmapHeaderConstractSize + static_cast<size_t>(kDataSegmentCapacity) * sizeof(ParsedEntry);
        if (!mmap_create_rw(seg_path.c_str(), seg_size, seg_handle)) return false;
        seg_hdr = reinterpret_cast<file_service::MmapHeaderConstract*>(seg_handle.addr);
        seg_entries = reinterpret_cast<ParsedEntry*>(reinterpret_cast<uint8_t*>(seg_handle.addr) + file_service::kMmapHeaderConstractSize);
        g_parser_status_header = seg_hdr;
        file_service::init_mmap_header_constract(*seg_hdr, kDataSegmentCapacity, DATA_STATUS_RUNNING);
        seg_write = 0;
        return true;
    };

    if (!open_segment(seg_idx)) {
        mmap_close(in_handle);
        return -5;
    }

    for (int t = 0; t < kNumThreads; t++) {
        for (uint32_t i = 0; i < tout[t].buf.size; i++) {
            if (seg_write >= kDataSegmentCapacity) {
                close_segment();
                ++seg_idx;
                if (!open_segment(seg_idx)) {
                    mmap_close(in_handle);
                    return -6;
                }
            }
            const ParsedEntry& entry = tout[t].buf.data[i];
            ParsedEntry out_entry = entry;
            out_entry.last_timestamp = last_timestamp_by_id.update_and_get_prev(out_entry.can_id, out_entry.timestamp);

            auto it = last_raw_by_id.find(out_entry.can_id);
            if (it == last_raw_by_id.end()) {
                out_entry.changed = 0;
                PrevRaw prev;
                prev.len = out_entry.data_len;
                if (out_entry.data_len > 0) {
                    memcpy(prev.data, out_entry.data, out_entry.data_len);
                }
                last_raw_by_id.emplace(out_entry.can_id, prev);
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

            seg_entries[seg_write] = out_entry;
            const uint32_t row_idx = global_row_idx++;
            global_can_id_index[out_entry.can_id].push_back(row_idx);
            if (out_entry.changed == 1) {
                global_can_id_changed_index[out_entry.can_id].push_back(row_idx);
            }
            global_can_id_timestamps[out_entry.can_id].push_back(out_entry.timestamp);

            const std::string channel_key = normalize_channel_key(out_entry.channel);
            auto ch_it = channel_to_index.find(channel_key);
            uint8_t channel_idx = 0;
            if (ch_it == channel_to_index.end()) {
                channel_idx = static_cast<uint8_t>(channel_table.size());
                channel_to_index.emplace(channel_key, channel_idx);
                channel_table.push_back(channel_key);
                global_channel_rows.emplace_back();
            } else {
                channel_idx = ch_it->second;
            }
            if (channel_idx < global_channel_rows.size()) {
                global_channel_rows[channel_idx].push_back(row_idx);
            }
            global_direction_rows[(out_entry.direction == 0) ? 0 : 1].push_back(row_idx);

            ++seg_write;
            seg_hdr->write_count = seg_write;
            ++total_written;
        }
    }

    close_segment();

    if (has_index) {
        uint32_t idx_seg_idx = 0;
        MMapHandle idx_handle = {};
        IndexHeader* ihdr = nullptr;
        CANIDFilter* filter_table = nullptr;
        uint32_t* row_pool = nullptr;
        uint32_t* changed_row_pool = nullptr;
        double* ts_pool = nullptr;
        uint32_t filt_idx = 0;
        uint32_t row_pool_off = 0;
        uint32_t changed_row_pool_off = 0;
        uint32_t ts_pool_off = 0;

        auto close_index_segment = [&]() {
            if (idx_handle.addr && ihdr) {
                ihdr->can_id_count = filt_idx;
                ihdr->row_pool_size = row_pool_off;
                ihdr->changed_row_pool_size = changed_row_pool_off;
                ihdr->ts_pool_size = ts_pool_off;
                ihdr->status = DATA_STATUS_DONE;
            }
            mmap_close(idx_handle);
            ihdr = nullptr;
            filter_table = nullptr;
            row_pool = nullptr;
            changed_row_pool = nullptr;
            ts_pool = nullptr;
            filt_idx = 0;
            row_pool_off = 0;
            changed_row_pool_off = 0;
            ts_pool_off = 0;
        };

        auto open_index_segment = [&](uint32_t index) -> bool {
            const std::string idx_path = make_index_segment_path(index);
            size_t idx_size = sizeof(IndexHeader)
                + static_cast<size_t>(kIndexMaxCanIds) * sizeof(CANIDFilter)
                + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t)
                + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t)
                + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(double);
            if (!mmap_create_rw(idx_path.c_str(), idx_size, idx_handle)) return false;
            ihdr = reinterpret_cast<IndexHeader*>(idx_handle.addr);
            ihdr->can_id_count = 0;
            ihdr->row_pool_size = 0;
            ihdr->changed_row_pool_size = 0;
            ihdr->ts_pool_size = 0;
            ihdr->max_can_ids = kIndexMaxCanIds;
            ihdr->max_row_pool_size = kIndexSegmentCapacity;
            ihdr->max_changed_row_pool_size = kIndexSegmentCapacity;
            ihdr->max_ts_pool_size = kIndexSegmentCapacity;
            ihdr->status = DATA_STATUS_RUNNING;

            filter_table = reinterpret_cast<CANIDFilter*>(
                reinterpret_cast<uint8_t*>(idx_handle.addr) + sizeof(IndexHeader));
            row_pool = reinterpret_cast<uint32_t*>(
                reinterpret_cast<uint8_t*>(idx_handle.addr)
                + sizeof(IndexHeader)
                + static_cast<size_t>(kIndexMaxCanIds) * sizeof(CANIDFilter));
            changed_row_pool = reinterpret_cast<uint32_t*>(
                reinterpret_cast<uint8_t*>(row_pool)
                + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t));
            ts_pool = reinterpret_cast<double*>(
                reinterpret_cast<uint8_t*>(changed_row_pool)
                + static_cast<size_t>(kIndexSegmentCapacity) * sizeof(uint32_t));

            filt_idx = 0;
            row_pool_off = 0;
            changed_row_pool_off = 0;
            ts_pool_off = 0;
            return true;
        };

        if (!open_index_segment(idx_seg_idx)) {
            mmap_close(in_handle);
            return -7;
        }

        static const std::vector<uint32_t> kEmptyChangedRows;

        for (auto& kv : global_can_id_index) {
            const uint32_t can_id = kv.first;
            const auto& rows = kv.second;
            const auto ts_it = global_can_id_timestamps.find(can_id);
            if (ts_it == global_can_id_timestamps.end()) {
                mmap_close(in_handle);
                return -10;
            }
            const auto& timestamps = ts_it->second;
            if (timestamps.size() != rows.size()) {
                mmap_close(in_handle);
                return -11;
            }

            const auto changed_it = global_can_id_changed_index.find(can_id);
            const auto& changed_rows = (changed_it != global_can_id_changed_index.end())
                ? changed_it->second
                : kEmptyChangedRows;

            size_t pos = 0;
            size_t changed_pos = 0;
            while (pos < rows.size()) {
                if (row_pool_off >= kIndexSegmentCapacity
                    || changed_row_pool_off >= kIndexSegmentCapacity
                    || ts_pool_off >= kIndexSegmentCapacity
                    || filt_idx >= kIndexMaxCanIds) {
                    close_index_segment();
                    ++idx_seg_idx;
                    if (!open_index_segment(idx_seg_idx)) {
                        mmap_close(in_handle);
                        return -8;
                    }
                }

                const uint32_t row_avail = kIndexSegmentCapacity - row_pool_off;
                const uint32_t changed_row_avail = kIndexSegmentCapacity - changed_row_pool_off;
                const uint32_t ts_avail = kIndexSegmentCapacity - ts_pool_off;
                const uint32_t avail_row_ts = (row_avail < ts_avail) ? row_avail : ts_avail;
                const uint32_t avail = (avail_row_ts < changed_row_avail) ? avail_row_ts : changed_row_avail;
                const uint32_t remaining = static_cast<uint32_t>(rows.size() - pos);
                const uint32_t take = (avail < remaining) ? avail : remaining;
                if (take == 0) {
                    close_index_segment();
                    ++idx_seg_idx;
                    if (!open_index_segment(idx_seg_idx)) {
                        mmap_close(in_handle);
                        return -9;
                    }
                    continue;
                }

                filter_table[filt_idx].can_id = can_id;
                filter_table[filt_idx].row_offset = row_pool_off;
                filter_table[filt_idx].changed_row_offset = changed_row_pool_off;
                filter_table[filt_idx].ts_offset = ts_pool_off;
                filter_table[filt_idx].count = take;
                filter_table[filt_idx].changed_count = 0;

                for (uint32_t i = 0; i < take; i++) {
                    row_pool[row_pool_off + i] = rows[pos + i];
                    ts_pool[ts_pool_off + i] = timestamps[pos + i];
                }

                size_t ch = changed_pos;
                for (uint32_t i = 0; i < take; i++) {
                    const uint32_t row_value = rows[pos + i];
                    while (ch < changed_rows.size() && changed_rows[ch] < row_value) ++ch;
                    if (ch < changed_rows.size() && changed_rows[ch] == row_value) {
                        changed_row_pool[changed_row_pool_off + filter_table[filt_idx].changed_count] = row_value;
                        ++filter_table[filt_idx].changed_count;
                        ++ch;
                    }
                }
                changed_pos = ch;

                row_pool_off += take;
                changed_row_pool_off += filter_table[filt_idx].changed_count;
                ts_pool_off += take;
                pos += take;
                ++filt_idx;
            }
        }

        close_index_segment();

        // ── Channel index mmap (separate file family) ─────────────────────
        uint32_t ch_seg_idx = 0;
        MMapHandle ch_handle = {};
        ChannelIndexHeader* ch_hdr = nullptr;
        ChannelFilter* ch_table = nullptr;
        uint32_t* ch_row_pool = nullptr;
        uint32_t ch_tbl_idx = 0;
        uint32_t ch_row_pool_off = 0;

        auto close_channel_index_segment = [&]() {
            if (ch_handle.addr && ch_hdr) {
                ch_hdr->channel_count = ch_tbl_idx;
                ch_hdr->row_pool_size = ch_row_pool_off;
                ch_hdr->status = DATA_STATUS_DONE;
            }
            mmap_close(ch_handle);
            ch_hdr = nullptr;
            ch_table = nullptr;
            ch_row_pool = nullptr;
            ch_tbl_idx = 0;
            ch_row_pool_off = 0;
        };

        auto open_channel_index_segment = [&](uint32_t index) -> bool {
            const std::string ch_path = make_channel_index_segment_path(index);
            size_t ch_size = sizeof(ChannelIndexHeader)
                + static_cast<size_t>(kChannelIndexMaxChannels) * sizeof(ChannelFilter)
                + static_cast<size_t>(kChannelIndexSegmentCapacity) * sizeof(uint32_t);
            if (!mmap_create_rw(ch_path.c_str(), ch_size, ch_handle)) return false;

            ch_hdr = reinterpret_cast<ChannelIndexHeader*>(ch_handle.addr);
            ch_hdr->channel_count = 0;
            ch_hdr->row_pool_size = 0;
            ch_hdr->max_channels = kChannelIndexMaxChannels;
            ch_hdr->max_row_pool_size = kChannelIndexSegmentCapacity;
            ch_hdr->status = DATA_STATUS_RUNNING;

            ch_table = reinterpret_cast<ChannelFilter*>(
                reinterpret_cast<uint8_t*>(ch_handle.addr) + sizeof(ChannelIndexHeader));
            ch_row_pool = reinterpret_cast<uint32_t*>(
                reinterpret_cast<uint8_t*>(ch_handle.addr)
                + sizeof(ChannelIndexHeader)
                + static_cast<size_t>(kChannelIndexMaxChannels) * sizeof(ChannelFilter));

            ch_tbl_idx = 0;
            ch_row_pool_off = 0;
            return true;
        };

        if (!open_channel_index_segment(ch_seg_idx)) {
            mmap_close(in_handle);
            return -12;
        }

        for (size_t channel_idx = 0; channel_idx < global_channel_rows.size(); ++channel_idx) {
            const auto& rows = global_channel_rows[channel_idx];
            if (rows.empty()) continue;
            const std::string& channel_name = channel_table[channel_idx];

            size_t pos = 0;
            while (pos < rows.size()) {
                if (ch_row_pool_off >= kChannelIndexSegmentCapacity
                    || ch_tbl_idx >= kChannelIndexMaxChannels) {
                    close_channel_index_segment();
                    ++ch_seg_idx;
                    if (!open_channel_index_segment(ch_seg_idx)) {
                        mmap_close(in_handle);
                        return -13;
                    }
                }

                const uint32_t row_avail = kChannelIndexSegmentCapacity - ch_row_pool_off;
                const uint32_t remaining = static_cast<uint32_t>(rows.size() - pos);
                const uint32_t take = (row_avail < remaining) ? row_avail : remaining;
                if (take == 0) {
                    close_channel_index_segment();
                    ++ch_seg_idx;
                    if (!open_channel_index_segment(ch_seg_idx)) {
                        mmap_close(in_handle);
                        return -14;
                    }
                    continue;
                }

                ch_table[ch_tbl_idx].channel_index = static_cast<uint8_t>(channel_idx);
                memset(ch_table[ch_tbl_idx].channel, 0, sizeof(ch_table[ch_tbl_idx].channel));
                const size_t ch_copy = (channel_name.size() < sizeof(ch_table[ch_tbl_idx].channel) - 1)
                    ? channel_name.size()
                    : sizeof(ch_table[ch_tbl_idx].channel) - 1;
                memcpy(ch_table[ch_tbl_idx].channel, channel_name.data(), ch_copy);
                ch_table[ch_tbl_idx].row_offset = ch_row_pool_off;
                ch_table[ch_tbl_idx].count = take;
                ch_table[ch_tbl_idx].reserved = 0;

                for (uint32_t i = 0; i < take; ++i) {
                    ch_row_pool[ch_row_pool_off + i] = rows[pos + i];
                }

                ch_row_pool_off += take;
                pos += take;
                ++ch_tbl_idx;
            }
        }

        close_channel_index_segment();

        // ── Direction index mmap (separate file family) ───────────────────
        uint32_t dir_seg_idx = 0;
        MMapHandle dir_handle = {};
        DirectionIndexHeader* dir_hdr = nullptr;
        DirectionFilter* dir_table = nullptr;
        uint32_t* dir_row_pool = nullptr;
        uint32_t dir_tbl_idx = 0;
        uint32_t dir_row_pool_off = 0;

        auto close_direction_index_segment = [&]() {
            if (dir_handle.addr && dir_hdr) {
                dir_hdr->direction_count = dir_tbl_idx;
                dir_hdr->row_pool_size = dir_row_pool_off;
                dir_hdr->status = DATA_STATUS_DONE;
            }
            mmap_close(dir_handle);
            dir_hdr = nullptr;
            dir_table = nullptr;
            dir_row_pool = nullptr;
            dir_tbl_idx = 0;
            dir_row_pool_off = 0;
        };

        auto open_direction_index_segment = [&](uint32_t index) -> bool {
            const std::string dir_path = make_direction_index_segment_path(index);
            size_t dir_size = sizeof(DirectionIndexHeader)
                + static_cast<size_t>(kDirectionIndexMaxDirections) * sizeof(DirectionFilter)
                + static_cast<size_t>(kDirectionIndexSegmentCapacity) * sizeof(uint32_t);
            if (!mmap_create_rw(dir_path.c_str(), dir_size, dir_handle)) return false;

            dir_hdr = reinterpret_cast<DirectionIndexHeader*>(dir_handle.addr);
            dir_hdr->direction_count = 0;
            dir_hdr->row_pool_size = 0;
            dir_hdr->max_directions = kDirectionIndexMaxDirections;
            dir_hdr->max_row_pool_size = kDirectionIndexSegmentCapacity;
            dir_hdr->status = DATA_STATUS_RUNNING;

            dir_table = reinterpret_cast<DirectionFilter*>(
                reinterpret_cast<uint8_t*>(dir_handle.addr) + sizeof(DirectionIndexHeader));
            dir_row_pool = reinterpret_cast<uint32_t*>(
                reinterpret_cast<uint8_t*>(dir_handle.addr)
                + sizeof(DirectionIndexHeader)
                + static_cast<size_t>(kDirectionIndexMaxDirections) * sizeof(DirectionFilter));

            dir_tbl_idx = 0;
            dir_row_pool_off = 0;
            return true;
        };

        if (!open_direction_index_segment(dir_seg_idx)) {
            mmap_close(in_handle);
            return -15;
        }

        for (uint8_t direction = 0; direction < 2; ++direction) {
            const auto& rows = global_direction_rows[direction];
            if (rows.empty()) continue;

            size_t pos = 0;
            while (pos < rows.size()) {
                if (dir_row_pool_off >= kDirectionIndexSegmentCapacity
                    || dir_tbl_idx >= kDirectionIndexMaxDirections) {
                    close_direction_index_segment();
                    ++dir_seg_idx;
                    if (!open_direction_index_segment(dir_seg_idx)) {
                        mmap_close(in_handle);
                        return -16;
                    }
                }

                const uint32_t row_avail = kDirectionIndexSegmentCapacity - dir_row_pool_off;
                const uint32_t remaining = static_cast<uint32_t>(rows.size() - pos);
                const uint32_t take = (row_avail < remaining) ? row_avail : remaining;
                if (take == 0) {
                    close_direction_index_segment();
                    ++dir_seg_idx;
                    if (!open_direction_index_segment(dir_seg_idx)) {
                        mmap_close(in_handle);
                        return -17;
                    }
                    continue;
                }

                dir_table[dir_tbl_idx].direction = direction;
                memset(dir_table[dir_tbl_idx].padding0, 0, sizeof(dir_table[dir_tbl_idx].padding0));
                dir_table[dir_tbl_idx].row_offset = dir_row_pool_off;
                dir_table[dir_tbl_idx].count = take;
                dir_table[dir_tbl_idx].reserved = 0;

                for (uint32_t i = 0; i < take; ++i) {
                    dir_row_pool[dir_row_pool_off + i] = rows[pos + i];
                }

                dir_row_pool_off += take;
                pos += take;
                ++dir_tbl_idx;
            }
        }

        close_direction_index_segment();
    }

    mmap_close(in_handle);
    CBCM_DEBUG("segmented worker done: total_entries=%llu segments=%u",
               static_cast<unsigned long long>(total_written),
               seg_idx + 1);
    return 0;
}

CP_EXPORT int32_t can_parser_run_worker(const char* file_path,
                                        const char* data_path,
                                        const char* index_path,
                                        FormatType fmt,
                                        uint32_t check_interval) {
    (void)check_interval;
    return can_parser_run_worker_segmented(file_path, data_path, index_path, fmt);
}

CP_EXPORT int32_t can_parser_run_worker_2pass(const char* file_path,
                                              const char* data_path,
                                              const char* index_path,
                                              FormatType fmt,
                                              uint32_t max_can_ids) {
    (void)max_can_ids;
    return can_parser_run_worker_segmented(file_path, data_path, index_path, fmt);
}

} // extern "C"
