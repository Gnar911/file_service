#include "mmap_wrapper.h"

#ifdef _WIN32
// ═══════════════════════════════════════════════════════════════════════════
// Windows implementation
// ═══════════════════════════════════════════════════════════════════════════

bool mmap_open_ro(const char* path, MMapHandle& out) {
    HANDLE file = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ,
                              NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return false;

    LARGE_INTEGER fsize;
    if (!GetFileSizeEx(file, &fsize) || fsize.QuadPart == 0) {
        CloseHandle(file);
        return false;
    }

    HANDLE mapping = CreateFileMappingA(file, NULL, PAGE_READONLY, 0, 0, NULL);
    if (!mapping) { CloseHandle(file); return false; }

    void* addr = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 0);
    if (!addr) { CloseHandle(mapping); CloseHandle(file); return false; }

    out.file    = file;
    out.mapping = mapping;
    out.addr    = addr;
    out.size    = static_cast<size_t>(fsize.QuadPart);
    return true;
}

bool mmap_open_rw(const char* path, MMapHandle& out) {
    HANDLE file = CreateFileA(path, GENERIC_READ | GENERIC_WRITE, 0,
                              NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return false;

    LARGE_INTEGER fsize;
    if (!GetFileSizeEx(file, &fsize) || fsize.QuadPart == 0) {
        CloseHandle(file);
        return false;
    }

    HANDLE mapping = CreateFileMappingA(file, NULL, PAGE_READWRITE, 0, 0, NULL);
    if (!mapping) { CloseHandle(file); return false; }

    void* addr = MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, 0);
    if (!addr) { CloseHandle(mapping); CloseHandle(file); return false; }

    out.file    = file;
    out.mapping = mapping;
    out.addr    = addr;
    out.size    = static_cast<size_t>(fsize.QuadPart);
    return true;
}

bool mmap_create_rw(const char* path, size_t size, MMapHandle& out) {
    HANDLE file = CreateFileA(path, GENERIC_READ | GENERIC_WRITE, 0,
                              NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return false;

    // Extend the file to the requested size.
    LARGE_INTEGER li;
    li.QuadPart = static_cast<LONGLONG>(size);
    if (!SetFilePointerEx(file, li, NULL, FILE_BEGIN) || !SetEndOfFile(file)) {
        CloseHandle(file);
        return false;
    }

    DWORD hi = static_cast<DWORD>(size >> 32);
    DWORD lo = static_cast<DWORD>(size & 0xFFFFFFFF);
    HANDLE mapping = CreateFileMappingA(file, NULL, PAGE_READWRITE, hi, lo, NULL);
    if (!mapping) { CloseHandle(file); return false; }

    void* addr = MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, size);
    if (!addr) { CloseHandle(mapping); CloseHandle(file); return false; }

    out.file    = file;
    out.mapping = mapping;
    out.addr    = addr;
    out.size    = size;
    return true;
}

void mmap_close(MMapHandle& h) {
    if (h.addr)                          { UnmapViewOfFile(h.addr); h.addr = nullptr; }
    if (h.mapping)                       { CloseHandle(h.mapping);  h.mapping = NULL; }
    if (h.file != INVALID_HANDLE_VALUE)  { CloseHandle(h.file);    h.file = INVALID_HANDLE_VALUE; }
    h.size = 0;
}

bool mmap_file_exists(const char* path) {
    DWORD attrs = GetFileAttributesA(path);
    return (attrs != INVALID_FILE_ATTRIBUTES) && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

#else
// ═══════════════════════════════════════════════════════════════════════════
// POSIX (Linux / macOS) implementation
// ═══════════════════════════════════════════════════════════════════════════

#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

bool mmap_open_ro(const char* path, MMapHandle& out) {
    int fd = ::open(path, O_RDONLY);
    if (fd < 0) return false;

    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size == 0) { ::close(fd); return false; }

    void* addr = mmap(nullptr, static_cast<size_t>(st.st_size),
                       PROT_READ, MAP_PRIVATE, fd, 0);
    if (addr == MAP_FAILED) { ::close(fd); return false; }

    out.fd   = fd;
    out.addr = addr;
    out.size = static_cast<size_t>(st.st_size);
    return true;
}

bool mmap_open_rw(const char* path, MMapHandle& out) {
    int fd = ::open(path, O_RDWR);
    if (fd < 0) return false;

    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size == 0) { ::close(fd); return false; }

    void* addr = mmap(nullptr, static_cast<size_t>(st.st_size),
                       PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (addr == MAP_FAILED) { ::close(fd); return false; }

    out.fd   = fd;
    out.addr = addr;
    out.size = static_cast<size_t>(st.st_size);
    return true;
}

bool mmap_create_rw(const char* path, size_t size, MMapHandle& out) {
    int fd = ::open(path, O_RDWR | O_CREAT | O_TRUNC, 0666);
    if (fd < 0) return false;

    if (ftruncate(fd, static_cast<off_t>(size)) != 0) { ::close(fd); return false; }

    void* addr = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (addr == MAP_FAILED) { ::close(fd); return false; }

    out.fd   = fd;
    out.addr = addr;
    out.size = size;
    return true;
}

void mmap_close(MMapHandle& h) {
    if (h.addr) { munmap(h.addr, h.size); h.addr = nullptr; }
    if (h.fd >= 0) { ::close(h.fd); h.fd = -1; }
    h.size = 0;
}

bool mmap_file_exists(const char* path) {
    return ::access(path, F_OK) == 0;
}

#endif
