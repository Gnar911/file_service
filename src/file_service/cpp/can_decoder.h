#pragma once

#include <cstdint>
#include <unordered_map>
#include <vector>

#include "mmap_header_constract.h"

#if defined(_WIN32)
#  define CD_EXPORT __declspec(dllexport)
#else
#  define CD_EXPORT
#endif

#pragma pack(push, 1)

struct ParsedEntry {
    uint32_t line_number;
    double   timestamp;
    double   last_timestamp;
    uint32_t can_id;
    uint8_t  direction;
    uint8_t  data_len;
    uint8_t  changed;
    uint8_t  data[64];
    char     channel[16];
};

using DataHeader = file_service::MmapHeaderConstract;

struct MessageDef {
    uint32_t can_id;
    uint16_t signal_count;
    uint16_t msg_length;
    uint32_t signal_offset;
    uint32_t padding;
};

struct SignalDef {
    uint16_t start_bit;
    uint16_t bit_length;
    uint8_t  byte_order;
    uint8_t  is_signed;
    uint8_t  has_choices;
    uint8_t  padding1;
    double   scale;
    double   offset;
    uint8_t  padding2[8];
};

struct SignalDirHeader {
    uint32_t entry_count;
    uint32_t status;
    uint8_t  padding[24];
};

struct SignalDirectoryEntry {
    uint32_t can_id;
    uint16_t signal_id;
    uint16_t padding;
    uint64_t index_offset;
    uint64_t value_offset;
    uint64_t rawvalue_offset;
    uint64_t changed_index_offset;
    uint32_t sample_count;
    uint32_t changed_sample_count;
    uint16_t signal_count;
    uint16_t padding2;
};

struct SoAHeader {
    uint64_t sample_count;
    uint32_t capacity;
    uint32_t status;
    uint8_t  padding[16];
};

#pragma pack(pop)

enum DecodeStatus : uint32_t {
    DECODE_STATUS_RUNNING = 0,
    DECODE_STATUS_DONE    = 1,
    DECODE_STATUS_ERROR   = 2,
};

extern std::vector<MessageDef> g_messages;
extern std::vector<SignalDef> g_signals;
extern std::unordered_map<uint32_t, uint32_t> g_canid_to_msg;
extern bool g_db_loaded;

int64_t extract_signal(const uint8_t* data, int data_len, const SignalDef& sig);

extern "C" {

//  DB code info
CD_EXPORT int32_t can_decoder_load_db(const MessageDef* messages,
                                      uint32_t msg_count,
                                      const SignalDef* signals,
                                      uint32_t sig_count);

CD_EXPORT void can_decoder_free_db();

CD_EXPORT int32_t can_decoder_run(const char* data_path,
                                  const char* signal_dir_path,
                                  const char* row_index_changed_path,
                                  const char* row_index_path,
                                  const char* value_path,
                                  const char* rawvalue_path);

CD_EXPORT int32_t can_decoder_test_message(uint32_t can_id,
                                           const uint8_t* data,
                                           uint8_t data_len,
                                           int64_t* out_raw,
                                           double* out_phys,
                                           uint32_t max_signals);

}