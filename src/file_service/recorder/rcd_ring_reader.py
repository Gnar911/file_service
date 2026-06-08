from __future__ import annotations

import time
from typing import List

from lw.logger_setup import LOG
from file_service.repository.file_handler.ring_handler import (
    CAPACITY,
    ENTRY_SIZE,
    ENTRY_STRUCT,
    CanLogRingHandler,
    CanLogRingPayload,
)


_PARTIAL_FLUSH_IDLE_S = 0.02
_IDLE_SLEEP_S = 0.0001


class SharedMemoryRingReader:
    def __init__(self, shm_name: str):
        self._ring = CanLogRingHandler(mmap_name=str(shm_name), create=False)
        self._ring.open()
        self._read_idx = self._write_idx()

    def _write_idx(self) -> int:
        return int(self._ring.read_header().write_idx)

    @property
    def frames_read(self) -> int:
        return int(self._read_idx)

    @property
    def write_idx(self) -> int:
        return int(self._write_idx())

    @property
    def available(self) -> int:
        return self._write_idx() - int(self._read_idx)

    def read_batch(self, count: int) -> List[CanLogRingPayload]:
        batch_count = max(0, int(count))
        if batch_count <= 0:
            return []

        current_write_idx = self._write_idx()
        current_read_idx = int(self._read_idx)
        LOG.info(
            "[RECORDER][RING] read_batch request=%d write_idx=%d read_idx=%d",
            batch_count,
            current_write_idx,
            current_read_idx,
        )

        payloads: List[CanLogRingPayload] = []
        for i in range(batch_count):
            payloads.append(self._ring.read_by_index(self._read_idx + i))

        self._read_idx += batch_count
        return payloads

    def close(self) -> None:
        self._ring.close()

    @staticmethod
    def should_flush_partial(available: int, last_flush_t: float, now_t: float) -> bool:
        return int(available) > 0 and (now_t - last_flush_t) >= _PARTIAL_FLUSH_IDLE_S

    @staticmethod
    def idle_wait() -> None:
        time.sleep(_IDLE_SLEEP_S)


__all__ = ["SharedMemoryRingReader"]