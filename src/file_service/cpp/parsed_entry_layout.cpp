#include "parsed_entry_layout.h"

#include "index_mmap_layout.h"
#include "mmap_header_constract.h"

extern "C" {

uint32_t can_parser_entry_size() {
    return kParsedEntrySize;
}

uint32_t can_parser_entry_line_number_offset() {
    return kParsedEntryLineNumberOffset;
}

uint32_t can_parser_entry_timestamp_offset() {
    return kParsedEntryTimestampOffset;
}

uint32_t can_parser_entry_last_timestamp_offset() {
    return kParsedEntryLastTimestampOffset;
}

uint32_t can_parser_entry_can_id_offset() {
    return kParsedEntryCanIdOffset;
}

uint32_t can_parser_entry_direction_offset() {
    return kParsedEntryDirectionOffset;
}

uint32_t can_parser_entry_data_len_offset() {
    return kParsedEntryDataLenOffset;
}

uint32_t can_parser_entry_changed_offset() {
    return kParsedEntryChangedOffset;
}

uint32_t can_parser_entry_data_offset() {
    return kParsedEntryDataOffset;
}

uint32_t can_parser_entry_data_capacity() {
    return kParsedEntryDataCapacity;
}

uint32_t can_parser_entry_channel_offset() {
    return kParsedEntryChannelOffset;
}

uint32_t can_parser_entry_channel_capacity() {
    return kParsedEntryChannelCapacity;
}

uint32_t can_parser_data_header_size() {
    return static_cast<uint32_t>(file_service::kMmapHeaderConstractSize);
}

uint32_t can_parser_index_header_size() {
    return kIndexHeaderSize;
}

uint32_t can_parser_index_header_can_id_count_offset() {
    return kIndexHeaderCanIdCountOffset;
}

uint32_t can_parser_index_header_row_pool_size_offset() {
    return kIndexHeaderRowPoolSizeOffset;
}

uint32_t can_parser_index_header_changed_row_pool_size_offset() {
    return kIndexHeaderChangedRowPoolSizeOffset;
}

uint32_t can_parser_index_header_ts_pool_size_offset() {
    return kIndexHeaderTsPoolSizeOffset;
}

uint32_t can_parser_index_header_max_can_ids_offset() {
    return kIndexHeaderMaxCanIdsOffset;
}

uint32_t can_parser_index_header_max_row_pool_size_offset() {
    return kIndexHeaderMaxRowPoolSizeOffset;
}

uint32_t can_parser_index_header_max_changed_row_pool_size_offset() {
    return kIndexHeaderMaxChangedRowPoolSizeOffset;
}

uint32_t can_parser_index_header_max_ts_pool_size_offset() {
    return kIndexHeaderMaxTsPoolSizeOffset;
}

uint32_t can_parser_can_id_filter_size() {
    return kCANIDFilterSize;
}

uint32_t can_parser_can_id_filter_can_id_offset() {
    return kCANIDFilterCanIdOffset;
}

uint32_t can_parser_can_id_filter_row_offset_offset() {
    return kCANIDFilterRowOffsetOffset;
}

uint32_t can_parser_can_id_filter_changed_row_offset_offset() {
    return kCANIDFilterChangedRowOffsetOffset;
}

uint32_t can_parser_can_id_filter_ts_offset_offset() {
    return kCANIDFilterTsOffsetOffset;
}

uint32_t can_parser_can_id_filter_count_offset() {
    return kCANIDFilterCountOffset;
}

uint32_t can_parser_can_id_filter_changed_count_offset() {
    return kCANIDFilterChangedCountOffset;
}

uint32_t can_parser_channel_index_header_size() {
    return kChannelIndexHeaderSize;
}

uint32_t can_parser_channel_index_header_channel_count_offset() {
    return kChannelIndexHeaderChannelCountOffset;
}

uint32_t can_parser_channel_index_header_max_channels_offset() {
    return kChannelIndexHeaderMaxChannelsOffset;
}

uint32_t can_parser_channel_index_header_max_row_pool_size_offset() {
    return kChannelIndexHeaderMaxRowPoolSizeOffset;
}

uint32_t can_parser_channel_filter_size() {
    return kChannelFilterSize;
}

uint32_t can_parser_channel_filter_channel_index_offset() {
    return kChannelFilterChannelIndexOffset;
}

uint32_t can_parser_channel_filter_channel_offset() {
    return kChannelFilterChannelOffset;
}

uint32_t can_parser_channel_filter_channel_capacity() {
    return kChannelFilterChannelCapacity;
}

uint32_t can_parser_channel_filter_row_offset_offset() {
    return kChannelFilterRowOffsetOffset;
}

uint32_t can_parser_channel_filter_count_offset() {
    return kChannelFilterCountOffset;
}

uint32_t can_parser_direction_index_header_size() {
    return kDirectionIndexHeaderSize;
}

uint32_t can_parser_direction_index_header_direction_count_offset() {
    return kDirectionIndexHeaderDirectionCountOffset;
}

uint32_t can_parser_direction_index_header_max_directions_offset() {
    return kDirectionIndexHeaderMaxDirectionsOffset;
}

uint32_t can_parser_direction_index_header_max_row_pool_size_offset() {
    return kDirectionIndexHeaderMaxRowPoolSizeOffset;
}

uint32_t can_parser_direction_filter_size() {
    return kDirectionFilterSize;
}

uint32_t can_parser_direction_filter_direction_offset() {
    return kDirectionFilterDirectionOffset;
}

uint32_t can_parser_direction_filter_row_offset_offset() {
    return kDirectionFilterRowOffsetOffset;
}

uint32_t can_parser_direction_filter_count_offset() {
    return kDirectionFilterCountOffset;
}

}
