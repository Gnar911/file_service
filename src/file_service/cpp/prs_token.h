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
#include "index_mmap_layout.h"
#include "parsed_entry_layout.h"

#ifndef LOGGING_TRACE_ENABLED
#if defined(__LW_TRACE)
#define LOGGING_TRACE_ENABLED __LW_TRACE()
#else
#define LOGGING_TRACE_ENABLED ;
#endif
#endif



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