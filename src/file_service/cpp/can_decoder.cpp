/*
 * can_decoder.cpp
 *
 * C++ hot-path for CAN signal decoding.
 *
 * DBC metadata is loaded ONCE via can_decoder_load_db() and stored in C++
 * heap memory.  The hot-path can_decoder_run() never touches the DB on disk —
 * it only reads data.mmap (parsed CAN entries) and writes (SoA layout):
 *   - signal_dir.mmap   (can_id, signal_id) → (index_offset, value_offset, rawvalue_offset, sample_count, signal_count)
 *   - row_index_changed.mmap uint32[] row index into data.mmap where signal raw value changed
 *   - row_index.mmap    uint32[]  row index into data.mmap per sample
 *   - value.mmap        float64[] physical values per sample
 *   - rawvalue.mmap     int64[]   raw decoded integers per sample
 *
 * Two-pass algorithm:
 *   Pass 1 — count samples per (message, signal) for exact offset computation.
 *   Pass 2 — extract raw values, compute physical values, write into 3 SoA arrays.
 *
 * Build: included in native_sdk_native shared library via CMakeLists.txt
 */

#include <cstdint>
#include <unordered_map>
#include <vector>

#include "can_analyzer_log.h"
#include "can_decoder.h"

#ifndef LOGGING_TRACE_ENABLED
#if defined(__LW_TRACE)
#define LOGGING_TRACE_ENABLED CBCM_INFO("TRACE %s", __FUNCTION__);
#else
#define LOGGING_TRACE_ENABLED
#endif
#endif

std::vector<MessageDef> g_messages;
std::vector<SignalDef> g_signals;
std::unordered_map<uint32_t, uint32_t> g_canid_to_msg;
bool g_db_loaded = false;

// ═══════════════════════════════════════════════════════════════════════════
// CAN signal bit extraction
// ═══════════════════════════════════════════════════════════════════════════

static inline int64_t extract_signal_le(const uint8_t* data, int data_len,
                                         int start_bit, int bit_length) {
    uint64_t raw = 0;
    for (int i = 0; i < bit_length; i++) {
        int bp       = start_bit + i;
        int byte_idx = bp >> 3;
        int bit_idx  = bp & 7;
        if (byte_idx < data_len)
            raw |= (uint64_t)((data[byte_idx] >> bit_idx) & 1) << i;
    }
    return static_cast<int64_t>(raw);
}

static inline int64_t extract_signal_be(const uint8_t* data, int data_len,
                                         int start_bit, int bit_length) {
    uint64_t raw = 0;
    int bp = start_bit;
    for (int i = bit_length - 1; i >= 0; i--) {
        int byte_idx   = bp >> 3;
        int bit_in_byte = bp & 7;
        if (byte_idx < data_len)
            raw |= (uint64_t)((data[byte_idx] >> bit_in_byte) & 1) << i;
        if ((bp & 7) == 0)
            bp += 15;
        else
            bp -= 1;
    }
    return static_cast<int64_t>(raw);
}

static inline int64_t sign_extend(int64_t raw, int bit_length) {
    if (bit_length > 0 && bit_length < 64) {
        uint64_t sign_mask = 1ULL << (bit_length - 1);
        if (static_cast<uint64_t>(raw) & sign_mask) {
            raw |= ~static_cast<int64_t>((1ULL << bit_length) - 1);
        }
    }
    return raw;
}

int64_t extract_signal(const uint8_t* data, int data_len,
                       const SignalDef& sig) {
    int64_t raw;
    if (sig.byte_order == 0)
        raw = extract_signal_le(data, data_len, sig.start_bit, sig.bit_length);
    else
        raw = extract_signal_be(data, data_len, sig.start_bit, sig.bit_length);

    if (sig.is_signed)
        raw = sign_extend(raw, sig.bit_length);

    return raw;
}

extern "C" {

CD_EXPORT int32_t can_decoder_load_db(const MessageDef* messages,
                                      uint32_t msg_count,
                                      const SignalDef* signals,
                                      uint32_t sig_count) {
    LOGGING_TRACE_ENABLED;

    if ((!messages && msg_count > 0) || (!signals && sig_count > 0))
        return -1;

    g_messages.assign(messages, messages + msg_count);
    g_signals.assign(signals, signals + sig_count);

    g_canid_to_msg.clear();
    g_canid_to_msg.reserve(msg_count);
    for (uint32_t i = 0; i < msg_count; i++)
        g_canid_to_msg[g_messages[i].can_id] = i;

    g_db_loaded = true;

    CBCM_INFO("Decoder DB loaded: %u messages, %u signals (in C++ memory)",
              msg_count, sig_count);
    return 0;
}

CD_EXPORT void can_decoder_free_db() {
    g_messages.clear();
    g_messages.shrink_to_fit();
    g_signals.clear();
    g_signals.shrink_to_fit();
    g_canid_to_msg.clear();
    g_db_loaded = false;
    CBCM_INFO("Decoder DB freed");
}

/*
 * can_decoder_test_message
 *
 * Debug helper — decode a single CAN frame using the loaded DBC.
 * No mmap involved — caller provides data bytes directly.
 *
 *   can_id       : CAN arbitration ID
 *   data         : pointer to payload bytes
 *   data_len     : number of payload bytes
 *   out_raw      : output array of int64_t[max_signals]  (raw values)
 *   out_phys     : output array of double[max_signals]   (physical values)
 *   max_signals  : capacity of out_raw / out_phys
 *
 * Returns the number of signals decoded (≥ 0), or:
 *   -1  DB not loaded
 *   -2  can_id not found in DB
 */
CD_EXPORT int32_t can_decoder_test_message(uint32_t       can_id,
                                            const uint8_t* data,
                                            uint8_t        data_len,
                                            int64_t*       out_raw,
                                            double*        out_phys,
                                            uint32_t       max_signals) {
    if (!g_db_loaded) return -1;
    auto it = g_canid_to_msg.find(can_id);
    if (it == g_canid_to_msg.end()) return -2;

    const MessageDef& msg = g_messages[it->second];
    const int dl = static_cast<int>(data_len);
    const uint32_t n = (msg.signal_count < max_signals)
                       ? msg.signal_count : max_signals;

    for (uint32_t si = 0; si < n; si++) {
        const SignalDef& sig = g_signals[msg.signal_offset + si];
        int64_t raw  = extract_signal(data, dl, sig);
        double  phys = static_cast<double>(raw) * sig.scale + sig.offset;
        out_raw[si]  = raw;
        out_phys[si] = phys;
    }
    return static_cast<int32_t>(msg.signal_count);
}

} /* extern "C" */
