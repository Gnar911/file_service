#pragma once

#include <cstddef>
#include <cstdint>

#include "can_parser.h"

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
#pragma pack(pop)

inline constexpr uint32_t kParsedEntrySize = static_cast<uint32_t>(sizeof(ParsedEntry));
inline constexpr uint32_t kParsedEntryLineNumberOffset = static_cast<uint32_t>(offsetof(ParsedEntry, line_number));
inline constexpr uint32_t kParsedEntryTimestampOffset = static_cast<uint32_t>(offsetof(ParsedEntry, timestamp));
inline constexpr uint32_t kParsedEntryLastTimestampOffset = static_cast<uint32_t>(offsetof(ParsedEntry, last_timestamp));
inline constexpr uint32_t kParsedEntryCanIdOffset = static_cast<uint32_t>(offsetof(ParsedEntry, can_id));
inline constexpr uint32_t kParsedEntryDirectionOffset = static_cast<uint32_t>(offsetof(ParsedEntry, direction));
inline constexpr uint32_t kParsedEntryDataLenOffset = static_cast<uint32_t>(offsetof(ParsedEntry, data_len));
inline constexpr uint32_t kParsedEntryChangedOffset = static_cast<uint32_t>(offsetof(ParsedEntry, changed));
inline constexpr uint32_t kParsedEntryDataOffset = static_cast<uint32_t>(offsetof(ParsedEntry, data));
inline constexpr uint32_t kParsedEntryDataCapacity = static_cast<uint32_t>(sizeof(ParsedEntry::data));
inline constexpr uint32_t kParsedEntryChannelOffset = static_cast<uint32_t>(offsetof(ParsedEntry, channel));
inline constexpr uint32_t kParsedEntryChannelCapacity = static_cast<uint32_t>(sizeof(ParsedEntry::channel));

static_assert(kParsedEntrySize == 107, "ParsedEntry size mismatch");
static_assert(kParsedEntryLineNumberOffset == 0, "ParsedEntry.line_number offset mismatch");
static_assert(kParsedEntryTimestampOffset == 4, "ParsedEntry.timestamp offset mismatch");
static_assert(kParsedEntryLastTimestampOffset == 12, "ParsedEntry.last_timestamp offset mismatch");
static_assert(kParsedEntryCanIdOffset == 20, "ParsedEntry.can_id offset mismatch");
static_assert(kParsedEntryDirectionOffset == 24, "ParsedEntry.direction offset mismatch");
static_assert(kParsedEntryDataLenOffset == 25, "ParsedEntry.data_len offset mismatch");
static_assert(kParsedEntryChangedOffset == 26, "ParsedEntry.changed offset mismatch");
static_assert(kParsedEntryDataOffset == 27, "ParsedEntry.data offset mismatch");
static_assert(kParsedEntryDataCapacity == 64, "ParsedEntry.data size mismatch");
static_assert(kParsedEntryChannelOffset == 91, "ParsedEntry.channel offset mismatch");
static_assert(kParsedEntryChannelCapacity == 16, "ParsedEntry.channel size mismatch");
