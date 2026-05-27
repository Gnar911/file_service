from __future__ import annotations

import time
from typing import Any

from lw.logger_setup import LOG
from lw.platform.linux_platform import _set_linux_process_name
from lw.shm_ring import BATCH_SIZE

from file_service.recorder.mmap_batch_writer import MmapBatchWriter
from file_service.recorder.shared_memory_ring_reader import SharedMemoryRingReader


class RecorderProcess:
    def __init__(
        self,
        shm_name: str,
        stop_event: Any,
    ):
        self._shm_name = shm_name
        self._stop_event = stop_event
        self._shm = None
        self._ring: SharedMemoryRingReader | None = None
        self._writer: MmapBatchWriter | None = None

    def run(self) -> None:
        _set_linux_process_name("CBCM-writer")

        from multiprocessing import shared_memory

        self._shm = shared_memory.SharedMemory(name=self._shm_name, create=False)
        self._ring = SharedMemoryRingReader(self._shm.buf)
        self._writer = MmapBatchWriter()

        last_flush_t = time.perf_counter()
        try:
            while not self._stop_event.is_set():
                available = self._ring.available
                if available >= BATCH_SIZE:
                    self._write_batch(BATCH_SIZE)
                    last_flush_t = time.perf_counter()
                    continue

                now_t = time.perf_counter()
                if self._ring.should_flush_partial(available, last_flush_t, now_t):
                    self._write_batch(available)
                    last_flush_t = now_t
                    continue

                self._ring.idle_wait()

            remaining = self._ring.available
            if remaining > 0:
                self._write_batch(remaining)

        except Exception:
            LOG.exception("[WRITER] Fatal exception in writer process")
        finally:
            frames_written = self.frames_written
            bytes_written = self.bytes_written
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

    def _write_batch(self, count: int) -> None:
        if self._ring is None or self._writer is None:
            raise RuntimeError("[WRITER][BUG] RecorderProcess not initialized")

        batch_count = min(max(0, int(count)), self._ring.available)
        if batch_count <= 0:
            return

        self._writer.write(self._ring.read_batch(batch_count), batch_count)

    def _close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass


__all__ = ["RecorderProcess"]