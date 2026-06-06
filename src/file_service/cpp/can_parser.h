#pragma once

#include <cstdint>

#if defined(_WIN32)
#  define CP_EXPORT __declspec(dllexport)
#else
#  define CP_EXPORT __attribute__((visibility("default")))
#endif

enum ParserStatus : uint32_t {
    PARSER_STATUS_RUNNING = 0,
    PARSER_STATUS_DONE    = 1,
    PARSER_STATUS_ERROR   = 2,
};

enum FormatType : int {
    FMT_UNKNOWN    = 0,
    FMT_CANOE      = 1,
    FMT_CANOE_FULL = 2,
    FMT_CANOE_CMP  = 3,
    FMT_CANCMD     = 4,
    FMT_FILTER     = 5,
    FMT_CANSUKE    = 6,
    FMT_CANCMD_T2  = 7,
    FMT_CANCMD_T3  = 8,
};

extern "C" {

CP_EXPORT ParserStatus get_status();

// Launch the parser worker.
//
// Progress during parsing is written into shared-memory headers:
//   - each data segment header increments write_count as ParsedEntry rows land
//   - segment status remains PARSER_STATUS_RUNNING until the segment is closed
//   - index-family headers also expose status, but they are finalized per
//     segment close rather than being emitted as a separate event stream
//
// Return value stays compatible with current callers:
//   - 0 on success
//   - negative error code on failure to launch or write mmaps
CP_EXPORT int32_t can_parser_run_worker_segmented(const char* file_path,
                                                  const char* base_path,
                                                  FormatType fmt);

CP_EXPORT int32_t can_parser_run_worker(const char* file_path,
                                        const char* data_path,
                                        const char* index_path,
                                        FormatType fmt,
                                        uint32_t check_interval);

CP_EXPORT int32_t can_parser_run_worker_2pass(const char* file_path,
                                              const char* data_path,
                                              const char* index_path,
                                              FormatType fmt,
                                              uint32_t max_can_ids);

// Segmented lifecycle APIs for Python: open/init, perform, close/finalize.
CP_EXPORT int32_t can_parser_segmented_open_and_init(const char* data_base_path,
                                                     const char* index_base_path);
CP_EXPORT int32_t can_parser_segmented_perform_all(const struct ParsedEntry* entries,
                                                   uint32_t count);
CP_EXPORT void can_parser_segmented_close_and_finalize();

// ParsedEntry / data.mmap layout introspection.
CP_EXPORT uint32_t can_parser_entry_size();
CP_EXPORT uint32_t can_parser_entry_line_number_offset();
CP_EXPORT uint32_t can_parser_entry_timestamp_offset();
CP_EXPORT uint32_t can_parser_entry_last_timestamp_offset();
CP_EXPORT uint32_t can_parser_entry_can_id_offset();
CP_EXPORT uint32_t can_parser_entry_direction_offset();
CP_EXPORT uint32_t can_parser_entry_data_len_offset();
CP_EXPORT uint32_t can_parser_entry_changed_offset();
CP_EXPORT uint32_t can_parser_entry_data_offset();
CP_EXPORT uint32_t can_parser_entry_data_capacity();
CP_EXPORT uint32_t can_parser_entry_channel_offset();
CP_EXPORT uint32_t can_parser_entry_channel_capacity();
CP_EXPORT uint32_t can_parser_data_header_size();

// Index mmap layout introspection.
CP_EXPORT uint32_t can_parser_index_header_size();
CP_EXPORT uint32_t can_parser_index_header_can_id_count_offset();
CP_EXPORT uint32_t can_parser_index_header_row_pool_size_offset();
CP_EXPORT uint32_t can_parser_index_header_changed_row_pool_size_offset();
CP_EXPORT uint32_t can_parser_index_header_ts_pool_size_offset();
CP_EXPORT uint32_t can_parser_index_header_max_can_ids_offset();
CP_EXPORT uint32_t can_parser_index_header_max_row_pool_size_offset();
CP_EXPORT uint32_t can_parser_index_header_max_changed_row_pool_size_offset();
CP_EXPORT uint32_t can_parser_index_header_max_ts_pool_size_offset();

CP_EXPORT uint32_t can_parser_can_id_filter_size();
CP_EXPORT uint32_t can_parser_can_id_filter_can_id_offset();
CP_EXPORT uint32_t can_parser_can_id_filter_row_offset_offset();
CP_EXPORT uint32_t can_parser_can_id_filter_changed_row_offset_offset();
CP_EXPORT uint32_t can_parser_can_id_filter_ts_offset_offset();
CP_EXPORT uint32_t can_parser_can_id_filter_count_offset();
CP_EXPORT uint32_t can_parser_can_id_filter_changed_count_offset();

CP_EXPORT uint32_t can_parser_channel_index_header_size();
CP_EXPORT uint32_t can_parser_channel_index_header_channel_count_offset();
CP_EXPORT uint32_t can_parser_channel_index_header_max_channels_offset();
CP_EXPORT uint32_t can_parser_channel_index_header_max_row_pool_size_offset();

CP_EXPORT uint32_t can_parser_channel_filter_size();
CP_EXPORT uint32_t can_parser_channel_filter_channel_index_offset();
CP_EXPORT uint32_t can_parser_channel_filter_channel_offset();
CP_EXPORT uint32_t can_parser_channel_filter_channel_capacity();
CP_EXPORT uint32_t can_parser_channel_filter_row_offset_offset();
CP_EXPORT uint32_t can_parser_channel_filter_count_offset();

CP_EXPORT uint32_t can_parser_direction_index_header_size();
CP_EXPORT uint32_t can_parser_direction_index_header_direction_count_offset();
CP_EXPORT uint32_t can_parser_direction_index_header_max_directions_offset();
CP_EXPORT uint32_t can_parser_direction_index_header_max_row_pool_size_offset();

CP_EXPORT uint32_t can_parser_direction_filter_size();
CP_EXPORT uint32_t can_parser_direction_filter_direction_offset();
CP_EXPORT uint32_t can_parser_direction_filter_row_offset_offset();
CP_EXPORT uint32_t can_parser_direction_filter_count_offset();

}