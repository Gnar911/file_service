#pragma once

#include <cstddef>
#include <cstdint>

#if defined(_WIN32)
#  define FS_EXPORT __declspec(dllexport)
#else
#  define FS_EXPORT
#endif

namespace file_service {

#pragma pack(push, 1)
struct MmapHeaderConstract {
    uint64_t write_count;
    uint32_t capacity;
    uint32_t status;
    uint8_t reserved[16];
};
#pragma pack(pop)

constexpr std::size_t kMmapHeaderConstractSize = 32;
constexpr std::size_t kMmapHeaderConstractWriteCountOffset = offsetof(MmapHeaderConstract, write_count);
constexpr std::size_t kMmapHeaderConstractCapacityOffset = offsetof(MmapHeaderConstract, capacity);
constexpr std::size_t kMmapHeaderConstractStatusOffset = offsetof(MmapHeaderConstract, status);

void init_mmap_header_constract(MmapHeaderConstract& header, uint32_t capacity, uint32_t status) noexcept;

} // namespace file_service

extern "C" {

FS_EXPORT uint32_t mmap_header_constract_size();
FS_EXPORT uint32_t mmap_header_constract_write_count_offset();
FS_EXPORT uint32_t mmap_header_constract_capacity_offset();
FS_EXPORT uint32_t mmap_header_constract_status_offset();

}
