#pragma once

#include <cstdint>

#if defined(_WIN32)
#  define CP_EXPORT __declspec(dllexport)
#else
#  define CP_EXPORT
#endif

enum DataStatus : uint32_t {
    DATA_STATUS_RUNNING = 0,
    DATA_STATUS_DONE    = 1,
    DATA_STATUS_ERROR   = 2,
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

CP_EXPORT DataStatus get_status();

// Launch the parser worker.
//
// Progress during parsing is written into shared-memory headers:
//   - each data segment header increments write_count as ParsedEntry rows land
//   - segment status remains DATA_STATUS_RUNNING until the segment is closed
//   - index-family headers also expose status, but they are finalized per
//     segment close rather than being emitted as a separate event stream
//
// Return value stays compatible with current callers:
//   - 0 on success
//   - negative error code on failure to launch or write mmaps
CP_EXPORT int32_t can_parser_run_worker_segmented(const char* file_path,
                                                  const char* data_base_path,
                                                  const char* index_base_path,
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

}