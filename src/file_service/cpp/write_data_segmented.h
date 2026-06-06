/*
 * write_data_segmented.h
 *
 * Segmented mmap data writer interface used by can_parser worker.
 */

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "parsed_entry_layout.h"

int32_t open_and_init_all_segment_writers(
	const std::string& base,
	const std::string& index_base);

int32_t perform_all_segment_writes(
	const std::vector<ParsedEntry>& parsed_entries);

void close_all_segment_writers_and_finalize();

int32_t write_entries_to_segmented_mmap(
    const std::string& base,
    const std::vector<ParsedEntry>& parsed_entries);
