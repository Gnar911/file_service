from __future__ import annotations

import struct
import time
from typing import Any

from lw.logger_setup import LOG
from lw.shm_ring import CAPACITY, ENTRY_SIZE


_PARTIAL_FLUSH_IDLE_S = 0.02
_IDLE_SLEEP_S = 0.0001
_WRITE_IDX_OFFSET = 0
_HEADER_SIZE = 8


class SharedMemoryRingReader:
    def __init__(self, shm_buf: Any):
        self._shm_buf = shm_buf
        self._read_idx = self._write_idx()

    def _write_idx(self) -> int:
        return int(struct.unpack_from("<Q", self._shm_buf, _WRITE_IDX_OFFSET)[0])

    @property
    def frames_read(self) -> int:
        return int(self._read_idx)

    @property
    def available(self) -> int:
        return self._write_idx() - int(self._read_idx)

    def read_batch(self, count: int) -> bytearray:
        batch_count = max(0, int(count))
        if batch_count <= 0:
            return bytearray()

        current_write_idx = self._write_idx()
        current_read_idx = int(self._read_idx)
        LOG.info(
            "[RECORDER][RING] read_batch request=%d write_idx=%d read_idx=%d",
            batch_count,
            current_write_idx,
            current_read_idx,
        )

        buf = bytearray(batch_count * ENTRY_SIZE)
        for i in range(batch_count):
            slot = (self._read_idx + i) % CAPACITY
            src_off = _HEADER_SIZE + (slot * ENTRY_SIZE)
            dst_off = i * ENTRY_SIZE
            buf[dst_off : dst_off + ENTRY_SIZE] = self._shm_buf[src_off : src_off + ENTRY_SIZE]

        self._read_idx += batch_count
        return buf

    @staticmethod
    def should_flush_partial(available: int, last_flush_t: float, now_t: float) -> bool:
        return int(available) > 0 and (now_t - last_flush_t) >= _PARTIAL_FLUSH_IDLE_S

    @staticmethod
    def idle_wait() -> None:
        time.sleep(_IDLE_SLEEP_S)


__all__ = ["SharedMemoryRingReader"]