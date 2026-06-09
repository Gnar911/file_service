from __future__ import annotations

import time
from typing import Any

from lw.logger_setup import LOG
from lw.platform.linux_platform import _set_linux_process_name
from file_service.repository.file_handler.ring_handler import BATCH_SIZE
from file_service.recorder.rcd_batch_writer import MmapBatchWriter
from file_service.api.status import RecorderStatus
from file_service.recorder.rcd_ring_reader import SharedMemoryRingReader


class RecorderProcess:
    def __init__(
        self,
        shm_name: str,
        output_mmap_path: str,
        stop_event: Any,
        wakeup,
        state,
    ):
        self._shm_name = shm_name
        self._output_mmap_path = output_mmap_path
        self._stop_event = stop_event
        self._wakeup = wakeup
        self._state = state
        self._ring: SharedMemoryRingReader | None = None
        self._writer: MmapBatchWriter | None = None

    def _set_status(self, status: int) -> None:
        self._state.value = int(status)
        self._wakeup.signal()

    def run(self) -> None:
        _set_linux_process_name("CBCM-writer")
        self._ring = SharedMemoryRingReader(self._shm_name)
        self._writer = MmapBatchWriter(self._output_mmap_path)
        current_status = int(RecorderStatus.IDLE)
        self._set_status(current_status)
        last_write_idx = int(self._ring.write_idx)

        had_error = False
        last_flush_t = time.perf_counter()
        try:
            while not self._stop_event.is_set():
                current_write_idx = int(self._ring.write_idx)
                if current_write_idx == last_write_idx and current_status != int(RecorderStatus.IDLE):
                    current_status = int(RecorderStatus.PAUSED)
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
            if had_error:
                self._set_status(int(RecorderStatus.FAILED))
            else:
                self._set_status(int(RecorderStatus.STOPPED))
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

        self._writer.write(self._ring.read_batch(batch_count))

        if int(current_status) != int(RecorderStatus.RECORDING):
            self._set_status(int(RecorderStatus.RECORDING))
            current_status = int(RecorderStatus.RECORDING)

        return int(current_status)

    def _close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._ring is not None:
            try:
                self._ring.close()
            except Exception:
                pass


__all__ = ["RecorderProcess"]