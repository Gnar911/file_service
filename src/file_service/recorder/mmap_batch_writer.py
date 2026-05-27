from __future__ import annotations

import json
import mmap as _mmap
import tempfile
from pathlib import Path
from typing import Any

from lw.logger_setup import LOG
from lw.shm_ring import BATCH_SIZE, ENTRY_SIZE


_PREALLOC_BYTES = BATCH_SIZE * ENTRY_SIZE * 1024


def recorder_staging_path() -> Path:
	tmp_dir = Path(tempfile.gettempdir()) / "cbcm_recorder"
	tmp_dir.mkdir(parents=True, exist_ok=True)
	return tmp_dir / "can_service.writer.ring.bin"


def recorder_staging_state_path() -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / "cbcm_recorder"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / "can_service.writer.ring.state.json"


def recorder_staging_bytes_written() -> int:
    state_path = recorder_staging_state_path()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return max(0, int(state.get("bytes_written", 0) or 0))
    except Exception:
        return 0


class MmapBatchWriter:
    def __init__(self):
        self._out = recorder_staging_path()
        self._state_path = recorder_staging_state_path()
        self._out.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._out, "w+b")
        self._file.truncate(_PREALLOC_BYTES)
        self._mmap = _mmap.mmap(self._file.fileno(), _PREALLOC_BYTES, access=_mmap.ACCESS_WRITE)
        self._file_size = _PREALLOC_BYTES
        self._file_off = 0
        self._frames_written = 0
        self._batch_write_count = 0
        self._write_state_file()

    @property
    def frames_written(self) -> int:
        return int(self._frames_written)

    @property
    def bytes_written(self) -> int:
        return int(self._file_off)

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
            "bytes_written": int(self._file_off),
            "batch_write_count": int(self._batch_write_count),
        }
        try:
            state_path_tmp.write_text(json.dumps(payload), encoding="utf-8")
            state_path_tmp.replace(self._state_path)
        except Exception:
            LOG.exception("[RECORDER][MMAP] failed to update staging state")


__all__ = [
	"MmapBatchWriter",
	"recorder_staging_bytes_written",
	"recorder_staging_path",
	"recorder_staging_state_path",
]