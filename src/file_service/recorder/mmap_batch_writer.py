from __future__ import annotations

import json
import mmap as _mmap
import struct
from pathlib import Path

from lw.logger_setup import LOG
from lw.shm_ring import BATCH_SIZE, ENTRY_SIZE

from file_service.parser.native.can_parser_api import MmapHeaderConstract
from file_service.recorder.status import (
    RECORDER_STATUS_IDLE,
    RECORDER_STATUS_WRITE,
)


_PREALLOC_BYTES = BATCH_SIZE * ENTRY_SIZE * 1024
_HEADER_WRITE_COUNT_OFFSET = MmapHeaderConstract.WRITE_COUNT_OFFSET
_HEADER_CAPACITY_OFFSET = MmapHeaderConstract.CAPACITY_OFFSET
_HEADER_STATUS_OFFSET = MmapHeaderConstract.STATUS_OFFSET
_HEADER_SIZE = MmapHeaderConstract.SIZE


class MmapBatchWriter:
    def __init__(self, output_mmap_path: str | Path):
        self._out = Path(output_mmap_path)
        self._state_path = self._out.parent / f"{self._out.name}.state.json"
        self._out.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._out, "w+b")
        self._file.truncate(_HEADER_SIZE + _PREALLOC_BYTES)
        self._mmap = _mmap.mmap(self._file.fileno(), _HEADER_SIZE + _PREALLOC_BYTES, access=_mmap.ACCESS_WRITE)
        self._file_size = _HEADER_SIZE + _PREALLOC_BYTES
        self._file_off = _HEADER_SIZE
        self._frames_written = 0
        self._batch_write_count = 0
        self._set_capacity(max(0, _PREALLOC_BYTES // ENTRY_SIZE))
        self.set_status(int(RECORDER_STATUS_IDLE))
        self._set_frame_count(0)
        self._write_state_file()

    @property
    def frames_written(self) -> int:
        return int(self._frames_written)

    @property
    def bytes_written(self) -> int:
        return int(max(0, self._file_off - _HEADER_SIZE))

    @property
    def total_bytes_written(self) -> int:
        return int(self._file_off)

    def set_status(self, status: int) -> None:
        struct.pack_into("<I", self._mmap, _HEADER_STATUS_OFFSET, int(status))

    def _set_capacity(self, capacity: int) -> None:
        struct.pack_into("<I", self._mmap, _HEADER_CAPACITY_OFFSET, int(capacity))

    def _set_frame_count(self, frame_count: int) -> None:
        struct.pack_into("<Q", self._mmap, _HEADER_WRITE_COUNT_OFFSET, int(frame_count))

    def write(self, batch: bytearray, frame_count: int) -> None:
        if frame_count <= 0 or not batch:
            return

        required = self._file_off + len(batch)
        if required > self._file_size:
            new_size = max(required, self._file_size * 2)
            self._mmap.resize(new_size)
            self._file_size = new_size

        self._mmap[self._file_off : self._file_off + len(batch)] = batch
        self._file_off += len(batch)
        self._frames_written += int(frame_count)
        self._batch_write_count += 1
        self._set_frame_count(self._frames_written)
        self.set_status(int(RECORDER_STATUS_WRITE))
        LOG.info(
            "[RECORDER][MMAP] batch_written batch_no=%d frame_count=%d frames_written=%d bytes_written=%d",
            int(self._batch_write_count),
            int(frame_count),
            int(self._frames_written),
            int(self._file_off),
        )
        self._write_state_file()

    def close(self) -> None:
        try:
            self._mmap.flush()
            self._mmap.close()
        except Exception:
            pass
        try:
            self._file.truncate(self._file_off)
            self._file.close()
        except Exception:
            pass

    def _write_state_file(self) -> None:
        state_path_tmp = self._state_path.with_suffix(".tmp")
        payload = {
            "frames_written": int(self._frames_written),
            "bytes_written": int(self.bytes_written),
            "total_bytes": int(self._file_off),
            "batch_write_count": int(self._batch_write_count),
        }
        try:
            state_path_tmp.write_text(json.dumps(payload), encoding="utf-8")
            state_path_tmp.replace(self._state_path)
        except Exception:
            LOG.exception("[RECORDER][MMAP] failed to update staging state")
