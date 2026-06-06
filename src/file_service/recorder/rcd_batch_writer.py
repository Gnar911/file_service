from __future__ import annotations

import json
from pathlib import Path
from typing import List

from lw.logger_setup import LOG
from file_service.parser.native.can_parser_api import CanParserLib, ParsedEntry
from file_service.repository.file_handler.ring_handler import ENTRY_SIZE, ENTRY_STRUCT
class MmapBatchWriter:
    def __init__(self, output_mmap_path: str | Path):
        self._out = Path(output_mmap_path)
        self._state_path = self._out.parent / f"{self._out.name}.state.json"
        self._out.parent.mkdir(parents=True, exist_ok=True)
        
        # Get the C++ library instance
        self._lib = CanParserLib.get()
        
        # Initialise segment writers
        self._frames_written = 0
        self._batch_write_count = 0
        self._opened = False
        self._closed = False
        
        # Open and initialise segment writers
        base_path = str(self._out.with_suffix(""))  # Remove .mmap suffix if present
        rc = self._lib.segmented_open_and_init(base_path, base_path)
        if rc != 0:
            raise RuntimeError(f"Failed to open segment writers: error code {rc}")
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

    def write(self, batch: bytearray, frame_count: int) -> None:
        """
        Write a batch of CAN frames to segmented mmap.
        """
        count = int(frame_count)
        if count <= 0:
            return

        raw = bytes(batch)
        available = len(raw) // ENTRY_SIZE
        use_count = min(count, available)
        if use_count <= 0:
            LOG.warning(
                "[RECORDER][MMAP] empty/invalid batch: frame_count=%d bytes=%d",
                count,
                len(raw),
            )
            return

        if available < count:
            LOG.warning(
                "[RECORDER][MMAP] truncated batch: requested=%d available=%d",
                count,
                available,
            )

        entries: List[ParsedEntry] = []
        for i in range(use_count):
            src_off = i * ENTRY_SIZE
            ts, can_id, direction, data_len, data_64, channel_16 = ENTRY_STRUCT.unpack_from(raw, src_off)

            entry = ParsedEntry()
            entry.line_number = int(self._frames_written + i + 1)
            entry.timestamp = float(ts)
            entry.last_timestamp = float(ts)
            entry.can_id = int(can_id)
            entry.direction = int(direction) & 0xFF
            entry.data_len = max(0, min(int(data_len), 64))
            entry.changed = 0

            for j in range(64):
                entry.data[j] = data_64[j]

            entry.channel = bytes(channel_16)
            entries.append(entry)

        self.write_parsed_entries(entries)

    def write_parsed_entries(self, entries: List[ParsedEntry]) -> None:
        """
        Write parsed entries to segmented mmap using C++ API.
        This is the proper method to use instead of write().
        """
        if not entries:
            return

        if not self._opened or self._closed:
            raise RuntimeError("Segment writers not opened or already closed")

        rc = self._lib.segmented_perform_all(entries)
        if rc != 0:
            raise RuntimeError(f"Failed to write entries to segmented mmap: error code {rc}")

        self._frames_written += len(entries)
        self._batch_write_count += 1
        
        LOG.info(
            "[RECORDER][MMAP] batch_written batch_no=%d frame_count=%d frames_written=%d",
            int(self._batch_write_count),
            len(entries),
            int(self._frames_written),
        )
        self._write_state_file()

    def close(self) -> None:
        if self._closed:
            return
            
        if self._opened:
            self._lib.segmented_close_and_finalize()
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

    # No-op kept for compatibility with RecorderProcess._set_status().
    # Recorder status now uses multiprocessing.Value in parent/child shared state.
    def set_status(self, _status: int) -> None:
        return
