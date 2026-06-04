#include "mmap_header_constract.h"

#include <cstring>

namespace file_service {

static_assert(kMmapHeaderConstractWriteCountOffset == 0, "MmapHeaderConstract.write_count offset mismatch");
static_assert(kMmapHeaderConstractCapacityOffset == 8, "MmapHeaderConstract.capacity offset mismatch");
static_assert(kMmapHeaderConstractStatusOffset == 12, "MmapHeaderConstract.status offset mismatch");
static_assert(sizeof(MmapHeaderConstract) == kMmapHeaderConstractSize, "MmapHeaderConstract size mismatch");

void init_mmap_header_constract(MmapHeaderConstract& header, uint32_t capacity, uint32_t status) noexcept {
    header.write_count = 0;
    header.capacity = capacity;
    header.status = status;
    std::memset(header.reserved, 0, sizeof(header.reserved));
}

} // namespace file_service

extern "C" {

uint32_t mmap_header_constract_size() {
    return static_cast<uint32_t>(file_service::kMmapHeaderConstractSize);
}

uint32_t mmap_header_constract_write_count_offset() {
    return static_cast<uint32_t>(file_service::kMmapHeaderConstractWriteCountOffset);
}

uint32_t mmap_header_constract_capacity_offset() {
    return static_cast<uint32_t>(file_service::kMmapHeaderConstractCapacityOffset);
}

uint32_t mmap_header_constract_status_offset() {
    return static_cast<uint32_t>(file_service::kMmapHeaderConstractStatusOffset);
}

}
