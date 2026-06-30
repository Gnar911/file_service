from __future__ import annotations

import json
from pathlib import Path
from typing import List

from lw.logger_setup import LOG
from file_service.module.fs_core import ParsedEntry, ParsedMmapInterface
from file_service.repository.file_handler.ring_handler import CanLogRingPayload


class MmapBatchWriter:
    def __init__(self, base_path: str | Path):
        self._out = Path(base_path)
        self._state_path = self._out.parent / f"{self._out.name}.state.json"
        self._out.parent.mkdir(parents=True, exist_ok=True)

        self._token_id = Path(base_path)
        
        # Initialise segment writers
        self._frames_written = 0
        self._batch_write_count = 0
        self._opened = False
        self._closed = False

        # Open and initialise mmap writers through pybind ParsedMmapInterface.
        self._handler = ParsedMmapInterface(str(self._token_id))
        self._handler.open_mmap()
        self._opened = True

        self._write_state_file()

    @property
    def frames_written(self) -> int:
        return int(self._frames_written)

    @property
    def bytes_written(self) -> int:
        # Note: For segmented mmap, we don't track bytes directly
        # Return frames_written * approximate entry size (107 bytes)
        return int(self._frames_written * 107)

    @property
    def total_bytes_written(self) -> int:
        # Same as bytes_written for compatibility
        return self.bytes_written

    # def write(self, batch: List[CanLogRingPayload]) -> None:
    #     """
    #     Write a batch of ring payloads to segmented mmap.
    #     """
    #     if not batch:
    #         return

    #     entries: List[ParsedEntry] = []
    #     for i, payload in enumerate(batch):
    #         entry = ParsedEntry()
    #         entry.line_number = int(self._frames_written + i + 1)
    #         entry.timestamp = float(payload.timestamp)
    #         entry.last_timestamp = float(payload.timestamp)
    #         entry.can_id = int(payload.can_id)
    #         entry.direction = int(payload.direction) & 0xFF
    #         entry.data_len = max(0, min(int(payload.data_len), 64))
    #         entry.changed = 0

    #         # #BUG: pybind11 ParsedEntry.data property ONLY accepts whole-vector assignment.
    #         # Trying to index individual bytes like entry.data[j] = ... silently fails.
    #         # Must assign the entire 64-byte array at once via property setter.
    #         data_list = list(payload.data) + [0] * (64 - len(payload.data))
    #         entry.data = data_list[:64]

    #         entry.channel = str(payload.channel)[:16]
    #         entries.append(entry)

    #     self.write_parsed_entries(entries)

    def write(self, entries: List[ParsedEntry]) -> None:
        """
        Write parsed entries to segmented mmap using C++ API.
        This is the proper method to use instead of write().
        """
        if not entries:
            return

        if not self._opened or self._closed:
            raise RuntimeError("Segment writers not opened or already closed")

        # write_entries() returns int32_t error code from C++
        write_error_code = int(self._handler.write_entries(entries))
        if write_error_code != 0:
            error_msg = f"ParsedMmapInterface::write_entries failed with error code {write_error_code}"
            LOG.error("[RECORDER][MMAP] %s", error_msg)
            raise RuntimeError(error_msg)

        self._frames_written += len(entries)
        self._batch_write_count += 1
        
        # LOG.info(
        #     "[RECORDER][MMAP] batch_written batch_no=%d frame_count=%d frames_written=%d",
        #     int(self._batch_write_count),
        #     len(entries),
        #     int(self._frames_written),
        # )
        self._write_state_file()

    def close(self) -> None:
        if self._closed:
            return
            
        if self._opened:
            self._handler.close_mmap()
            self._closed = True
            self._opened = False

    def _write_state_file(self) -> None:
        state_path_tmp = self._state_path.with_suffix(".tmp")
        payload = {
            "frames_written": int(self._frames_written),
            "bytes_written": int(self.bytes_written),
            "batch_write_count": int(self._batch_write_count),
        }
        try:
            state_path_tmp.write_text(json.dumps(payload), encoding="utf-8")
            state_path_tmp.replace(self._state_path)
        except Exception:
            LOG.exception("[RECORDER][MMAP] failed to update staging state")