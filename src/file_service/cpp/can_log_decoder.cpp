
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <string>
#include <thread>
#include <vector>

#include "can_analyzer_log.h"
#include "can_decoder.h"
#include "mmap_wrapper.h"

#ifndef LOGGING_TRACE_ENABLED
#if defined(__LW_TRACE)
#define LOGGING_TRACE_ENABLED CBCM_INFO("TRACE %s", __FUNCTION__);
#else
#define LOGGING_TRACE_ENABLED
#endif
#endif

struct MmapRegion {
    MMapHandle _handle = {};
    void* base = nullptr;
    size_t size = 0;

    bool open_rw(const char* path) {
        if (!mmap_open_rw(path, _handle)) return false;
        base = _handle.addr;
        size = _handle.size;
        return true;
    }

    bool open_ro(const char* path) {
        if (!mmap_open_ro(path, _handle)) return false;
        base = _handle.addr;
        size = _handle.size;
        return true;
    }

    void release() {
        mmap_close(_handle);
        base = nullptr;
        size = 0;
    }

    ~MmapRegion() { release(); }

    MmapRegion() = default;
    MmapRegion(const MmapRegion&) = delete;
    MmapRegion& operator=(const MmapRegion&) = delete;
    MmapRegion(MmapRegion&& other) noexcept {
        _handle = other._handle;
        base = other.base;
        size = other.size;
        other._handle = {};
        other.base = nullptr;
        other.size = 0;
    }
    MmapRegion& operator=(MmapRegion&& other) noexcept {
        if (this != &other) {
            release();
            _handle = other._handle;
            base = other.base;
            size = other.size;
            other._handle = {};
            other.base = nullptr;
            other.size = 0;
        }
        return *this;
    }
};

extern "C" {

CD_EXPORT int32_t can_decoder_run(const char* data_path,
                                   const char* signal_dir_path,
                                   const char* row_index_changed_path,
                                   const char* row_index_path,
                                   const char* value_path,
                                   const char* rawvalue_path) {
    LOGGING_TRACE_ENABLED;

    if (!data_path || !signal_dir_path || !row_index_changed_path ||
        !row_index_path || !value_path || !rawvalue_path)
        return -1;

    if (!g_db_loaded) {
        CBCM_ERROR("can_decoder_run: DB not loaded — call can_decoder_load_db first");
        return -3;
    }

    const MessageDef* msg_table = g_messages.data();
    const SignalDef*  sig_pool  = g_signals.data();
    const uint32_t    msg_count = static_cast<uint32_t>(g_messages.size());
    const uint32_t    total_sigs = static_cast<uint32_t>(g_signals.size());

    constexpr uint32_t kDirSegmentCapacity = 1'000'000;
    constexpr uint32_t kSampleSegmentCapacity = 1'000'000;
    constexpr int NUM_THREADS = 4;

    auto file_exists = [](const std::string& p) -> bool {
        return mmap_file_exists(p.c_str());
    };

    auto make_segment_path = [](const char* base_path, uint32_t seg_idx) -> std::string {
        std::string stem(base_path);
        if (stem.size() >= 5 && stem.compare(stem.size() - 5, 5, ".mmap") == 0) {
            stem.resize(stem.size() - 5);
        }
        char num[16];
        snprintf(num, sizeof(num), ".%03u.mmap", seg_idx);
        return stem + num;
    };

    struct InputSegment {
        MmapRegion mm;
        const DataHeader* hdr = nullptr;
        const ParsedEntry* entries = nullptr;
        uint64_t count = 0;
        uint64_t global_start = 0;
    };

    std::vector<std::string> data_paths;
    for (uint32_t i = 0; i < 10'000; i++) {
        std::string p = make_segment_path(data_path, i);
        if (!file_exists(p)) {
            if (i == 0) break;
            break;
        }
        data_paths.push_back(p);
    }
    if (data_paths.empty()) {
        CBCM_ERROR("No segmented data mmap found for decode stem: %s", data_path);
        CBCM_ERROR("Probe first segment: %s", make_segment_path(data_path, 0).c_str());
        return -2;
    }

    std::vector<InputSegment> in_segments;
    in_segments.reserve(data_paths.size());
    uint64_t entry_count = 0;
    for (const auto& p : data_paths) {
        InputSegment seg;
        if (!seg.mm.open_ro(p.c_str())) {
            CBCM_ERROR("Failed to open data segment: %s", p.c_str());
            return -2;
        }
        seg.hdr = reinterpret_cast<const DataHeader*>(seg.mm.base);
        if (seg.hdr->status != 1) {
            CBCM_ERROR("data segment not DONE: %s status=%u", p.c_str(), seg.hdr->status);
            return -6;
        }
        seg.entries = reinterpret_cast<const ParsedEntry*>(
            reinterpret_cast<const uint8_t*>(seg.mm.base) + sizeof(DataHeader));
        seg.count = seg.hdr->write_count;
        seg.global_start = entry_count;
        entry_count += seg.count;
        in_segments.push_back(std::move(seg));
    }

    CBCM_INFO("Decoder: %llu parsed entries to decode across %zu segment(s)",
              static_cast<unsigned long long>(entry_count), in_segments.size());

    std::vector<uint32_t> counts(total_sigs, 0);
    std::vector<uint32_t> changed_counts(total_sigs, 0);
    std::vector<uint8_t> has_prev_raw(total_sigs, 0);
    std::vector<int64_t> prev_raw(total_sigs, 0);
    for (const auto& seg : in_segments) {
        for (uint64_t i = 0; i < seg.count; i++) {
            auto it = g_canid_to_msg.find(seg.entries[i].can_id);
            if (it == g_canid_to_msg.end()) continue;
            const MessageDef& msg = msg_table[it->second];
            const ParsedEntry& entry = seg.entries[i];
            const int dl = static_cast<int>(entry.data_len);
            for (uint16_t si = 0; si < msg.signal_count; si++) {
                const uint32_t flat = msg.signal_offset + si;
                counts[flat]++;

                const SignalDef& sig = sig_pool[flat];
                const int64_t raw = extract_signal(entry.data, dl, sig);
                if (has_prev_raw[flat]) {
                    if (raw != prev_raw[flat] && changed_counts[flat] < std::numeric_limits<uint32_t>::max()) {
                        changed_counts[flat]++;
                    }
                } else {
                    has_prev_raw[flat] = 1;
                }
                prev_raw[flat] = raw;
            }
        }
    }

    std::vector<uint64_t> offsets(total_sigs, 0);
    std::vector<uint64_t> changed_offsets(total_sigs, 0);
    uint32_t dir_count = 0;
    uint64_t sample_total = 0;
    uint64_t changed_sample_total = 0;
    for (uint32_t mi = 0; mi < msg_count; mi++) {
        const MessageDef& msg = msg_table[mi];
        for (uint16_t si = 0; si < msg.signal_count; si++) {
            const uint32_t flat = msg.signal_offset + si;
            const uint32_t cnt = counts[flat];
            const uint32_t changed_cnt = changed_counts[flat];
            if (cnt == 0) continue;
            offsets[flat] = sample_total;
            changed_offsets[flat] = changed_sample_total;
            sample_total += cnt;
            changed_sample_total += changed_cnt;
            dir_count++;
        }
    }

    CBCM_INFO("Decoder: %u directory entries, %llu total samples, %llu changed samples to write",
              dir_count,
              static_cast<unsigned long long>(sample_total),
              static_cast<unsigned long long>(changed_sample_total));

    auto create_rw_region = [](const std::string& path, size_t size, MmapRegion& out) -> bool {
        out.release();
        if (!mmap_create_rw(path.c_str(), size, out._handle)) return false;
        out.base = out._handle.addr;
        out.size = out._handle.size;
        return true;
    };

    struct DirSegment {
        MmapRegion mm;
        SignalDirHeader* hdr = nullptr;
        SignalDirectoryEntry* entries = nullptr;
        uint32_t written = 0;
    };

    struct SampleSegment {
        MmapRegion ridx_changed_mm;
        MmapRegion ridx_mm;
        MmapRegion val_mm;
        MmapRegion raw_mm;
        SoAHeader* ridx_changed_hdr = nullptr;
        SoAHeader* ridx_hdr = nullptr;
        SoAHeader* val_hdr = nullptr;
        SoAHeader* raw_hdr = nullptr;
        uint32_t* row_index_changed_arr = nullptr;
        uint32_t* row_index_arr = nullptr;
        double* value_arr = nullptr;
        int64_t* rawvalue_arr = nullptr;
        uint32_t written = 0;
        uint32_t changed_written = 0;
    };

    const uint32_t dir_seg_count = (dir_count == 0) ? 1U : static_cast<uint32_t>((static_cast<uint64_t>(dir_count) + kDirSegmentCapacity - 1) / kDirSegmentCapacity);
    const uint32_t sample_seg_count = (sample_total == 0) ? 1U : static_cast<uint32_t>((sample_total + kSampleSegmentCapacity - 1) / kSampleSegmentCapacity);

    std::vector<DirSegment> dir_segments;
    dir_segments.reserve(dir_seg_count);
    for (uint32_t s = 0; s < dir_seg_count; s++) {
        const std::string p = make_segment_path(signal_dir_path, s);
        const size_t seg_size = sizeof(SignalDirHeader) + static_cast<size_t>(kDirSegmentCapacity) * sizeof(SignalDirectoryEntry);
        DirSegment seg;
        if (!create_rw_region(p, seg_size, seg.mm)) return -4;
        seg.hdr = reinterpret_cast<SignalDirHeader*>(seg.mm.base);
        seg.entries = reinterpret_cast<SignalDirectoryEntry*>(reinterpret_cast<uint8_t*>(seg.mm.base) + sizeof(SignalDirHeader));
        seg.hdr->entry_count = 0;
        seg.hdr->status = DECODE_STATUS_RUNNING;
        dir_segments.push_back(std::move(seg));
    }

    std::vector<SampleSegment> sample_segments;
    sample_segments.reserve(sample_seg_count);
    for (uint32_t s = 0; s < sample_seg_count; s++) {
        const std::string ridx_changed_p = make_segment_path(row_index_changed_path, s);
        const std::string ridx_p = make_segment_path(row_index_path, s);
        const std::string val_p  = make_segment_path(value_path, s);
        const std::string raw_p  = make_segment_path(rawvalue_path, s);
        const size_t ridx_changed_size = sizeof(SoAHeader) + static_cast<size_t>(kSampleSegmentCapacity) * sizeof(uint32_t);
        const size_t ridx_size = sizeof(SoAHeader) + static_cast<size_t>(kSampleSegmentCapacity) * sizeof(uint32_t);
        const size_t val_size  = sizeof(SoAHeader) + static_cast<size_t>(kSampleSegmentCapacity) * sizeof(double);
        const size_t raw_size  = sizeof(SoAHeader) + static_cast<size_t>(kSampleSegmentCapacity) * sizeof(int64_t);
        SampleSegment seg;
        if (!create_rw_region(ridx_changed_p, ridx_changed_size, seg.ridx_changed_mm)) return -10;
        if (!create_rw_region(ridx_p, ridx_size, seg.ridx_mm)) return -5;
        if (!create_rw_region(val_p,  val_size,  seg.val_mm))  return -7;
        if (!create_rw_region(raw_p,  raw_size,  seg.raw_mm))  return -8;

        seg.ridx_changed_hdr = reinterpret_cast<SoAHeader*>(seg.ridx_changed_mm.base);
        seg.ridx_hdr = reinterpret_cast<SoAHeader*>(seg.ridx_mm.base);
        seg.val_hdr  = reinterpret_cast<SoAHeader*>(seg.val_mm.base);
        seg.raw_hdr  = reinterpret_cast<SoAHeader*>(seg.raw_mm.base);
        seg.row_index_changed_arr = reinterpret_cast<uint32_t*>(reinterpret_cast<uint8_t*>(seg.ridx_changed_mm.base) + sizeof(SoAHeader));
        seg.row_index_arr = reinterpret_cast<uint32_t*>(reinterpret_cast<uint8_t*>(seg.ridx_mm.base) + sizeof(SoAHeader));
        seg.value_arr     = reinterpret_cast<double*>(reinterpret_cast<uint8_t*>(seg.val_mm.base) + sizeof(SoAHeader));
        seg.rawvalue_arr  = reinterpret_cast<int64_t*>(reinterpret_cast<uint8_t*>(seg.raw_mm.base) + sizeof(SoAHeader));

        seg.ridx_changed_hdr->sample_count = 0;
        seg.ridx_changed_hdr->capacity = kSampleSegmentCapacity;
        seg.ridx_changed_hdr->status = DECODE_STATUS_RUNNING;

        seg.ridx_hdr->sample_count = 0;
        seg.ridx_hdr->capacity = kSampleSegmentCapacity;
        seg.ridx_hdr->status = DECODE_STATUS_RUNNING;

        seg.val_hdr->sample_count = 0;
        seg.val_hdr->capacity = kSampleSegmentCapacity;
        seg.val_hdr->status = DECODE_STATUS_RUNNING;

        seg.raw_hdr->sample_count = 0;
        seg.raw_hdr->capacity = kSampleSegmentCapacity;
        seg.raw_hdr->status = DECODE_STATUS_RUNNING;

        sample_segments.push_back(std::move(seg));
    }

    auto publish_sample_headers = [&](uint64_t written_total,
                                     uint64_t changed_written_total,
                                     bool done) {
        for (uint32_t seg_idx = 0; seg_idx < sample_segments.size(); seg_idx++) {
            const uint64_t seg_start = static_cast<uint64_t>(seg_idx) * kSampleSegmentCapacity;

            uint64_t seg_written = 0;
            if (written_total > seg_start) {
                seg_written = written_total - seg_start;
                if (seg_written > kSampleSegmentCapacity) seg_written = kSampleSegmentCapacity;
            }

            uint64_t seg_changed_written = 0;
            if (changed_written_total > seg_start) {
                seg_changed_written = changed_written_total - seg_start;
                if (seg_changed_written > kSampleSegmentCapacity) seg_changed_written = kSampleSegmentCapacity;
            }

            SampleSegment& seg = sample_segments[seg_idx];
            seg.ridx_hdr->sample_count = seg_written;
            seg.val_hdr->sample_count = seg_written;
            seg.raw_hdr->sample_count = seg_written;
            seg.ridx_changed_hdr->sample_count = seg_changed_written;

            seg.ridx_hdr->status = done ? DECODE_STATUS_DONE : DECODE_STATUS_RUNNING;
            seg.val_hdr->status = done ? DECODE_STATUS_DONE : DECODE_STATUS_RUNNING;
            seg.raw_hdr->status = done ? DECODE_STATUS_DONE : DECODE_STATUS_RUNNING;
            seg.ridx_changed_hdr->status = done ? DECODE_STATUS_DONE : DECODE_STATUS_RUNNING;
        }
    };

    std::atomic<uint64_t> sample_progress(0);
    std::atomic<uint64_t> changed_sample_progress(0);
    std::atomic<bool> progress_stop(false);

    std::thread progress_thread([&]() {
        while (!progress_stop.load(std::memory_order_relaxed)) {
            publish_sample_headers(
                sample_progress.load(std::memory_order_relaxed),
                changed_sample_progress.load(std::memory_order_relaxed),
                false);
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
    });

    uint32_t dir_out_idx = 0;
    uint64_t sample_cursor = 0;
    uint64_t changed_sample_cursor = 0;
    for (uint32_t mi = 0; mi < msg_count; mi++) {
        const MessageDef& msg = msg_table[mi];
        for (uint16_t si = 0; si < msg.signal_count; si++) {
            const uint32_t flat = msg.signal_offset + si;
            const uint32_t cnt = counts[flat];
            const uint32_t changed_cnt = changed_counts[flat];
            if (cnt == 0) continue;

            const uint32_t dseg_idx = dir_out_idx / kDirSegmentCapacity;
            const uint32_t dlocal = dir_out_idx % kDirSegmentCapacity;
            SignalDirectoryEntry& de = dir_segments[dseg_idx].entries[dlocal];
            de.can_id = msg.can_id;
            de.signal_id = si;
            de.padding = 0;
            de.index_offset = sample_cursor;
            de.value_offset = sample_cursor;
            de.rawvalue_offset = sample_cursor;
            de.changed_index_offset = changed_sample_cursor;
            de.sample_count = cnt;
            de.changed_sample_count = changed_cnt;
            de.signal_count = msg.signal_count;
            de.padding2 = 0;

            dir_segments[dseg_idx].written++;
            dir_out_idx++;
            sample_cursor += cnt;
            changed_sample_cursor += changed_cnt;
        }
    }

    auto for_each_entry_in_range = [&](uint64_t start, uint64_t end, auto&& fn) {
        for (const auto& seg : in_segments) {
            const uint64_t seg_start = seg.global_start;
            const uint64_t seg_end = seg.global_start + seg.count;
            if (end <= seg_start || start >= seg_end) continue;
            const uint64_t local_begin = (start > seg_start) ? (start - seg_start) : 0;
            const uint64_t local_end = (end < seg_end) ? (end - seg_start) : seg.count;
            for (uint64_t i = local_begin; i < local_end; i++) {
                const uint64_t global_idx = seg_start + i;
                fn(global_idx, seg.entries[i]);
            }
        }
    };

    std::vector<std::vector<uint32_t>> thread_counts(NUM_THREADS, std::vector<uint32_t>(total_sigs, 0));
    {
        const uint64_t slice = (entry_count == 0) ? 0 : (entry_count / NUM_THREADS);
        for (int t = 0; t < NUM_THREADS; t++) {
            const uint64_t start = t * slice;
            const uint64_t end = (t == NUM_THREADS - 1) ? entry_count : (start + slice);
            auto& tc = thread_counts[t];
            for_each_entry_in_range(start, end, [&](uint64_t, const ParsedEntry& e) {
                auto it = g_canid_to_msg.find(e.can_id);
                if (it == g_canid_to_msg.end()) return;
                const MessageDef& msg = msg_table[it->second];
                for (uint16_t si = 0; si < msg.signal_count; si++) {
                    tc[msg.signal_offset + si]++;
                }
            });
        }
    }

    std::vector<std::vector<uint64_t>> thread_offsets(NUM_THREADS, std::vector<uint64_t>(total_sigs, 0));
    for (uint32_t flat = 0; flat < total_sigs; flat++) {
        uint64_t running = offsets[flat];
        for (int t = 0; t < NUM_THREADS; t++) {
            thread_offsets[t][flat] = running;
            running += thread_counts[t][flat];
        }
    }

    auto write_sample = [&](uint64_t pos, uint32_t row_idx, double phys, int64_t raw) {
        const uint32_t seg_idx = static_cast<uint32_t>(pos / kSampleSegmentCapacity);
        const uint32_t local = static_cast<uint32_t>(pos % kSampleSegmentCapacity);
        SampleSegment& seg = sample_segments[seg_idx];
        seg.row_index_arr[local] = row_idx;
        seg.value_arr[local] = phys;
        seg.rawvalue_arr[local] = raw;
    };

    auto write_changed_sample = [&](uint64_t pos, uint32_t row_idx) {
        const uint32_t seg_idx = static_cast<uint32_t>(pos / kSampleSegmentCapacity);
        const uint32_t local = static_cast<uint32_t>(pos % kSampleSegmentCapacity);
        SampleSegment& seg = sample_segments[seg_idx];
        seg.row_index_changed_arr[local] = row_idx;
    };

    auto decode_slice = [&](int tid, uint64_t start, uint64_t end) {
        auto& my_offsets = thread_offsets[tid];
        uint64_t local_written = 0;
        for_each_entry_in_range(start, end, [&](uint64_t global_row, const ParsedEntry& e) {
            auto it = g_canid_to_msg.find(e.can_id);
            if (it == g_canid_to_msg.end()) return;
            const MessageDef& msg = msg_table[it->second];
            const int dl = static_cast<int>(e.data_len);
            for (uint16_t si = 0; si < msg.signal_count; si++) {
                const uint32_t flat = msg.signal_offset + si;
                const SignalDef& sig = sig_pool[flat];
                const int64_t raw = extract_signal(e.data, dl, sig);
                const double phys = static_cast<double>(raw) * sig.scale + sig.offset;
                const uint64_t pos = my_offsets[flat]++;
                write_sample(pos, static_cast<uint32_t>(global_row), phys, raw);
                local_written++;
                if (local_written >= 4096) {
                    sample_progress.fetch_add(local_written, std::memory_order_relaxed);
                    local_written = 0;
                }
            }
        });
        if (local_written > 0) {
            sample_progress.fetch_add(local_written, std::memory_order_relaxed);
        }
    };

    {
        const uint64_t slice = (entry_count == 0) ? 0 : (entry_count / NUM_THREADS);
        std::vector<std::thread> threads;
        threads.reserve(NUM_THREADS);
        for (int t = 0; t < NUM_THREADS; t++) {
            const uint64_t start = t * slice;
            const uint64_t end = (t == NUM_THREADS - 1) ? entry_count : (start + slice);
            threads.emplace_back(decode_slice, t, start, end);
        }
        for (auto& th : threads) th.join();
    }

    std::vector<uint64_t> changed_write_offsets = changed_offsets;
    std::vector<uint8_t> has_prev_changed(total_sigs, 0);
    std::vector<int64_t> prev_raw_changed(total_sigs, 0);

    uint64_t local_changed_written = 0;
    for (const auto& seg : in_segments) {
        for (uint64_t i = 0; i < seg.count; i++) {
            const uint64_t global_row = seg.global_start + i;
            const ParsedEntry& e = seg.entries[i];
            auto it = g_canid_to_msg.find(e.can_id);
            if (it == g_canid_to_msg.end()) continue;
            const MessageDef& msg = msg_table[it->second];
            const int dl = static_cast<int>(e.data_len);
            for (uint16_t si = 0; si < msg.signal_count; si++) {
                const uint32_t flat = msg.signal_offset + si;
                const SignalDef& sig = sig_pool[flat];
                const int64_t raw = extract_signal(e.data, dl, sig);
                if (has_prev_changed[flat]) {
                    if (raw != prev_raw_changed[flat]) {
                        const uint64_t pos = changed_write_offsets[flat]++;
                        write_changed_sample(pos, static_cast<uint32_t>(global_row));
                        local_changed_written++;
                        if (local_changed_written >= 4096) {
                            changed_sample_progress.fetch_add(local_changed_written, std::memory_order_relaxed);
                            local_changed_written = 0;
                        }
                    }
                } else {
                    has_prev_changed[flat] = 1;
                }
                prev_raw_changed[flat] = raw;
            }
        }
    }

    if (local_changed_written > 0) {
        changed_sample_progress.fetch_add(local_changed_written, std::memory_order_relaxed);
    }

    sample_progress.store(sample_total, std::memory_order_relaxed);
    changed_sample_progress.store(changed_sample_total, std::memory_order_relaxed);
    progress_stop.store(true, std::memory_order_relaxed);
    if (progress_thread.joinable()) {
        progress_thread.join();
    }

    for (auto& seg : dir_segments) {
        seg.hdr->entry_count = seg.written;
        seg.hdr->status = DECODE_STATUS_DONE;
    }

    publish_sample_headers(sample_total, changed_sample_total, true);

    CBCM_INFO("Decode complete: %u dir entries, %llu samples, %llu changed samples from %llu entries (segmented)",
              dir_count,
              static_cast<unsigned long long>(sample_total),
              static_cast<unsigned long long>(changed_sample_total),
              static_cast<unsigned long long>(entry_count));

    return 0;
}

} /* extern "C" */