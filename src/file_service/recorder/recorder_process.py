from __future__ import annotations

import time
from typing import Any

from lw.logger_setup import LOG
from lw.platform.linux_platform import _set_linux_process_name
from lw.shm_ring import BATCH_SIZE
from file_service.recorder.mmap_batch_writer import MmapBatchWriter
from file_service.parser.native.can_parser_api import MmapHeaderConstract
from file_service.recorder.status import (
    RECORDER_STATUS_IDLE,
    RECORDER_STATUS_STOP,
    RECORDER_STATUS_WRITE,
)
from file_service.recorder.shared_memory_ring_reader import SharedMemoryRingReader


class RecorderProcess:
    def __init__(
        self,
        shm_name: str,
        output_mmap_path: str,
        stop_event: Any,
        wakeup,
    ):
        self._shm_name = shm_name
        self._output_mmap_path = output_mmap_path
        self._stop_event = stop_event
        self._wakeup = wakeup
        self._shm = None
        self._ring: SharedMemoryRingReader | None = None
        self._writer: MmapBatchWriter | None = None

    def _set_status(self, status: int) -> None:
        if self._writer is None:
            return
        self._writer.set_status(int(status))
        self._wakeup.signal()

    def run(self) -> None:
        _set_linux_process_name("CBCM-writer")
        MmapHeaderConstract.load_from_native_binding()

        from multiprocessing import shared_memory

        self._shm = shared_memory.SharedMemory(name=self._shm_name, create=False)
        self._ring = SharedMemoryRingReader(self._shm.buf)
        self._writer = MmapBatchWriter(self._output_mmap_path)
        current_status = int(RECORDER_STATUS_IDLE)
        self._set_status(current_status)
        last_write_idx = int(self._ring.write_idx)

        had_error = False
        last_flush_t = time.perf_counter()
        try:
            while not self._stop_event.is_set():
                current_write_idx = int(self._ring.write_idx)
                if current_write_idx == last_write_idx and current_status != int(RECORDER_STATUS_IDLE):
                    current_status = int(RECORDER_STATUS_IDLE)
                    self._set_status(current_status)

                available = self._ring.available
                if available >= BATCH_SIZE:
                    current_status = self._write_batch(BATCH_SIZE, current_status)
                    last_write_idx = current_write_idx
                    last_flush_t = time.perf_counter()
                    continue

                now_t = time.perf_counter()
                if self._ring.should_flush_partial(available, last_flush_t, now_t):
                    current_status = self._write_batch(available, current_status)
                    last_write_idx = current_write_idx
                    last_flush_t = now_t
                    continue

                self._ring.idle_wait()

            remaining = self._ring.available
            if remaining > 0:
                current_status = self._write_batch(remaining, current_status)

        except Exception:
            LOG.exception("[WRITER] Fatal exception in writer process")
            had_error = True
        finally:
            frames_written = self.frames_written
            bytes_written = self.bytes_written
            self._set_status(int(RECORDER_STATUS_STOP))
            self._close()
            LOG.debug("[WRITER] Exiting — wrote %d frames (%d bytes).", frames_written, bytes_written)

    @property
    def frames_written(self) -> int:
        if self._writer is None:
            return 0
        return int(self._writer.frames_written)

    @property
    def bytes_written(self) -> int:
        if self._writer is None:
            return 0
        return int(self._writer.bytes_written)

    def _write_batch(self, count: int, current_status: int) -> int:
        if self._ring is None or self._writer is None:
            raise RuntimeError("[WRITER][BUG] RecorderProcess not initialized")

        batch_count = min(max(0, int(count)), self._ring.available)
        if batch_count <= 0:
            return int(current_status)

        self._writer.write(self._ring.read_batch(batch_count), batch_count)

        if int(current_status) != int(RECORDER_STATUS_WRITE):
            self._set_status(int(RECORDER_STATUS_WRITE))
            current_status = int(RECORDER_STATUS_WRITE)

        return int(current_status)

    def _close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass


__all__ = ["RecorderProcess"]