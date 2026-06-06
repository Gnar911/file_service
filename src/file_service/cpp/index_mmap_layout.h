#pragma once

#include <cstddef>
#include <cstdint>

#pragma pack(push, 1)
struct IndexHeader {
    uint32_t can_id_count;
    uint32_t row_pool_size;
    uint32_t changed_row_pool_size;
    uint32_t ts_pool_size;
    uint32_t max_can_ids;
    uint32_t max_row_pool_size;
    uint32_t max_changed_row_pool_size;
    uint32_t max_ts_pool_size;
    uint32_t status;
    uint8_t  padding[4];
};

struct CANIDFilter {
    uint32_t can_id;
    uint64_t row_offset;
    uint64_t changed_row_offset;
    uint64_t ts_offset;
    uint32_t count;
    uint32_t changed_count;
};

struct ChannelIndexHeader {
    uint32_t channel_count;
    uint32_t row_pool_size;
    uint32_t max_channels;
    uint32_t max_row_pool_size;
    uint32_t status;
    uint8_t  padding[12];
};

struct ChannelFilter {
    uint8_t  channel_index;
    char     channel[15];
    uint64_t row_offset;
    uint32_t count;
    uint32_t reserved;
};

struct DirectionIndexHeader {
    uint32_t direction_count;
    uint32_t row_pool_size;
    uint32_t max_directions;
    uint32_t max_row_pool_size;
    uint32_t status;
    uint8_t  padding[12];
};

struct DirectionFilter {
    uint8_t  direction;
    uint8_t  padding0[7];
    uint64_t row_offset;
    uint32_t count;
    uint32_t reserved;
};
#pragma pack(pop)

inline constexpr uint32_t kIndexHeaderSize = static_cast<uint32_t>(sizeof(IndexHeader));
inline constexpr uint32_t kIndexHeaderCanIdCountOffset = static_cast<uint32_t>(offsetof(IndexHeader, can_id_count));
inline constexpr uint32_t kIndexHeaderRowPoolSizeOffset = static_cast<uint32_t>(offsetof(IndexHeader, row_pool_size));
inline constexpr uint32_t kIndexHeaderChangedRowPoolSizeOffset = static_cast<uint32_t>(offsetof(IndexHeader, changed_row_pool_size));
inline constexpr uint32_t kIndexHeaderTsPoolSizeOffset = static_cast<uint32_t>(offsetof(IndexHeader, ts_pool_size));
inline constexpr uint32_t kIndexHeaderMaxCanIdsOffset = static_cast<uint32_t>(offsetof(IndexHeader, max_can_ids));
inline constexpr uint32_t kIndexHeaderMaxRowPoolSizeOffset = static_cast<uint32_t>(offsetof(IndexHeader, max_row_pool_size));
inline constexpr uint32_t kIndexHeaderMaxChangedRowPoolSizeOffset = static_cast<uint32_t>(offsetof(IndexHeader, max_changed_row_pool_size));
inline constexpr uint32_t kIndexHeaderMaxTsPoolSizeOffset = static_cast<uint32_t>(offsetof(IndexHeader, max_ts_pool_size));

inline constexpr uint32_t kCANIDFilterSize = static_cast<uint32_t>(sizeof(CANIDFilter));
inline constexpr uint32_t kCANIDFilterCanIdOffset = static_cast<uint32_t>(offsetof(CANIDFilter, can_id));
inline constexpr uint32_t kCANIDFilterRowOffsetOffset = static_cast<uint32_t>(offsetof(CANIDFilter, row_offset));
inline constexpr uint32_t kCANIDFilterChangedRowOffsetOffset = static_cast<uint32_t>(offsetof(CANIDFilter, changed_row_offset));
inline constexpr uint32_t kCANIDFilterTsOffsetOffset = static_cast<uint32_t>(offsetof(CANIDFilter, ts_offset));
inline constexpr uint32_t kCANIDFilterCountOffset = static_cast<uint32_t>(offsetof(CANIDFilter, count));
inline constexpr uint32_t kCANIDFilterChangedCountOffset = static_cast<uint32_t>(offsetof(CANIDFilter, changed_count));

inline constexpr uint32_t kChannelIndexHeaderSize = static_cast<uint32_t>(sizeof(ChannelIndexHeader));
inline constexpr uint32_t kChannelIndexHeaderChannelCountOffset = static_cast<uint32_t>(offsetof(ChannelIndexHeader, channel_count));
inline constexpr uint32_t kChannelIndexHeaderMaxChannelsOffset = static_cast<uint32_t>(offsetof(ChannelIndexHeader, max_channels));
inline constexpr uint32_t kChannelIndexHeaderMaxRowPoolSizeOffset = static_cast<uint32_t>(offsetof(ChannelIndexHeader, max_row_pool_size));

inline constexpr uint32_t kChannelFilterSize = static_cast<uint32_t>(sizeof(ChannelFilter));
inline constexpr uint32_t kChannelFilterChannelIndexOffset = static_cast<uint32_t>(offsetof(ChannelFilter, channel_index));
inline constexpr uint32_t kChannelFilterChannelOffset = static_cast<uint32_t>(offsetof(ChannelFilter, channel));
inline constexpr uint32_t kChannelFilterChannelCapacity = static_cast<uint32_t>(sizeof(ChannelFilter::channel));
inline constexpr uint32_t kChannelFilterRowOffsetOffset = static_cast<uint32_t>(offsetof(ChannelFilter, row_offset));
inline constexpr uint32_t kChannelFilterCountOffset = static_cast<uint32_t>(offsetof(ChannelFilter, count));

inline constexpr uint32_t kDirectionIndexHeaderSize = static_cast<uint32_t>(sizeof(DirectionIndexHeader));
inline constexpr uint32_t kDirectionIndexHeaderDirectionCountOffset = static_cast<uint32_t>(offsetof(DirectionIndexHeader, direction_count));
inline constexpr uint32_t kDirectionIndexHeaderMaxDirectionsOffset = static_cast<uint32_t>(offsetof(DirectionIndexHeader, max_directions));
inline constexpr uint32_t kDirectionIndexHeaderMaxRowPoolSizeOffset = static_cast<uint32_t>(offsetof(DirectionIndexHeader, max_row_pool_size));

inline constexpr uint32_t kDirectionFilterSize = static_cast<uint32_t>(sizeof(DirectionFilter));
inline constexpr uint32_t kDirectionFilterDirectionOffset = static_cast<uint32_t>(offsetof(DirectionFilter, direction));
inline constexpr uint32_t kDirectionFilterRowOffsetOffset = static_cast<uint32_t>(offsetof(DirectionFilter, row_offset));
inline constexpr uint32_t kDirectionFilterCountOffset = static_cast<uint32_t>(offsetof(DirectionFilter, count));

static_assert(sizeof(IndexHeader) == 40, "IndexHeader size mismatch");
static_assert(sizeof(CANIDFilter) == 36, "CANIDFilter size mismatch");
static_assert(sizeof(ChannelIndexHeader) == 32, "ChannelIndexHeader size mismatch");
static_assert(sizeof(ChannelFilter) == 32, "ChannelFilter size mismatch");
static_assert(sizeof(DirectionIndexHeader) == 32, "DirectionIndexHeader size mismatch");
static_assert(sizeof(DirectionFilter) == 24, "DirectionFilter size mismatch");
