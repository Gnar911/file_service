from __future__ import annotations

import time
from typing import List

from lw.logger_setup import LOG
from lw.shared_ring_buf import SharedRingBuffer
from file_service.module.fs_core import ParsedMmapInterface, LogRecord
from file_service.module.fs_core import LogRecord
import struct

class LogRecordCodec:
    ENTRY_STRUCT = struct.Struct("<d I B B 64s 16s 42x")

    @staticmethod
    def _channel16(record: LogRecord) -> bytes:
        raw = str(record.channel).encode("ascii", "ignore")[:16]
        return raw.ljust(16, b"\0")

    @staticmethod
    def _decode_channel(raw: bytes) -> str:
        return raw.split(b"\0", 1)[0].decode("ascii")

    @classmethod
    def obj_size(cls) -> int:
        return cls.ENTRY_STRUCT.size
    
    @classmethod
    def serialize(cls, record: LogRecord) -> bytes:
        data = bytes(record.data[:64])
        data64 = data.ljust(64, b"\0")

        return cls.ENTRY_STRUCT.pack(
            float(record.timestamp),
            int(record.can_id),
            int(record.direction) & 0xFF,
            int(record.data_len) & 0xFF,
            data64,
            cls._channel16(record),
        )

    @classmethod
    def deserialize(cls, raw: bytes) -> LogRecord:
        ts, can_id, direction, dlc, data64, channel = cls.ENTRY_STRUCT.unpack(raw)

        record = LogRecord()
        record.timestamp = float(ts)
        record.can_id = int(can_id)
        record.direction = int(direction)
        record.data_len = int(dlc)

        # pybind11 property requires whole-array assignment
        record.data = list(data64[:64])

        record.channel = cls._decode_channel(channel)

        return record
    
class LogRecordRing(SharedRingBuffer[LogRecord]):
    CODEC = LogRecordCodec

_PARTIAL_FLUSH_IDLE_S = 0.02
class SharedMemoryRingReader:
    def __init__(self, shm_name: str):
        self._ring = LogRecordRing(mmap_name=str(shm_name), create=False)
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

    """ 20260629-NOTE: The de-load reader no need to know about the data format layout, just take the bytes 
        for each object
    """
    def read_batch(self, count: int) -> list[LogRecord]:
        batch_count = max(0, int(count))
        if batch_count <= 0:
            return []

        current_write_idx = self._write_idx()

        # LOG.info(
        #     "[RECORDER][RING] read_batch request=%d write_idx=%d read_idx=%d",
        #     batch_count,
        #     current_write_idx,
        #     self._read_idx,
        # )

        batch: list[LogRecord] = []

        for i in range(batch_count):
            batch.append(
                self._ring.read_by_index(self._read_idx + i)
            )

        self._read_idx += batch_count
        return batch

    def read_available(self) -> list[LogRecord]:
        return self.read_batch(self.available)

    # def read_batch(self, count: int) -> List[CanLogRingPayload]:
    #     batch_count = max(0, int(count))
    #     if batch_count <= 0:
    #         return []

    #     current_write_idx = self._write_idx()
    #     current_read_idx = int(self._read_idx)
    #     LOG.info(
    #         "[RECORDER][RING] read_batch request=%d write_idx=%d read_idx=%d",
    #         batch_count,
    #         current_write_idx,
    #         current_read_idx,
    #     )

    #     payloads: List[CanLogRingPayload] = []
    #     for i in range(batch_count):
    #         payloads.append(self._ring.read_by_index(self._read_idx + i))

    #     self._read_idx += batch_count
    #     return payloads

    def close(self) -> None:
        self._ring.close()

__all__ = ["SharedMemoryRingReader"]