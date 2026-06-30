from __future__ import annotations

import time
from typing import Any

from lw.logger_setup import LOG
from lw.platform.linux_platform import _set_linux_process_name
from file_service.repository.file_handler.ring_handler import BATCH_SIZE
from file_service.recorder.rcd_batch_writer import MmapBatchWriter
from file_service.status import RecorderStatus
from file_service.recorder.rcd_ring_reader import SharedMemoryRingReader


class RecorderProcess:
    def __init__(
        self,
        shm_name: str,
        base_path: str,
        stop_event: Any,
        wakeup,
        state,
    ):
        self._shm_name = shm_name
        self._base_path = base_path
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
        self._writer = MmapBatchWriter(self._base_path)
        current_status = int(RecorderStatus.WAIT_RING)
        self._set_status(current_status)
        # last_write_idx = int(self._ring.write_idx)

        # last_flush_t = time.perf_counter()
        try:
            while not self._stop_event.is_set():
                available = self._ring.available

                if available == 0:
                    if current_status != int(RecorderStatus.WAIT_RING):
                        current_status = int(RecorderStatus.WAIT_RING)
                        self._set_status(current_status)

                    time.sleep(0.001)
                    continue

                #current_status = self._write_batch(available, current_status)
                self._writer.write(self._ring.read_available())

            remaining = self._ring.available
            if remaining > 0:
                current_status = self._write_batch(remaining, current_status)

            self._set_status(int(RecorderStatus.STOPPED))

        except Exception:
            LOG.exception("[WRITER] Fatal exception in writer process")

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

    def _write_batch(self, count: int, current_status: int) -> int:
        if self._ring is None or self._writer is None:
            raise RuntimeError("[WRITER][BUG] RecorderProcess not initialized")

        batch_count = min(max(0, int(count)), self._ring.available)
        if batch_count <= 0:
            return int(current_status)

        self._writer.write(self._ring.read_batch(batch_count))

        if int(current_status) != int(RecorderStatus.WRITE_BATCH):
            self._set_status(int(RecorderStatus.WRITE_BATCH))
            current_status = int(RecorderStatus.WRITE_BATCH)

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