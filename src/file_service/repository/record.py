from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, List, Optional, Tuple

from file_service.define import MMAP_LOCAL_STORAGE_DIR, MMAP_TEMP_STORAGE_DIR
from file_service.module.fs_core import ParsedEntry, ParsedMmapInterface
from file_service.repository.file_handler.decode_mmap_handler import CANLogDecodedDiskFile
from file_service.repository.file_handler.dbc_pkl_handler import DBCPklHandler
from lw.logger_setup import LOG

from file_service.record_id import RecordId


class Record:
    def __init__(self, record_id: RecordId, base_dir: str | Path, base_name: str):
        if not str(base_dir):
            raise ValueError("base_dir is required")
        if not str(base_name):
            raise ValueError("base_name is required")

        token_base_name = record_id.path_token()
        if str(base_name) != token_base_name:
            raise ValueError("base_name must equal record_id.path_token()")

        self.record_id: RecordId = record_id
        self.__base_path = Path(base_dir) / token_base_name
        self.__prs_data: ParsedMmapInterface = ParsedMmapInterface(str(self.__base_path))
        self.__decode_handler: CANLogDecodedDiskFile = CANLogDecodedDiskFile(path=self.__base_path)
        self.__pkl_handler: DBCPklHandler = DBCPklHandler(pkl_dir=Path(base_dir))

    def is_decoded(self) -> bool:
        return self.has_decode_mmaps()

    def get_record_id(self) -> RecordId:
        return self.record_id

    def get_base_path(self) -> Path:
        return Path(self.__base_path)

    def get_data_mmap_path(self) -> Path:
        return Path(self.__base_path)

    def get_dbc_pkl_path(self) -> Path:
        base_path = self.get_base_path()
        return base_path.parent / f"{base_path.name}.pkl"

    # Replay-facing accessors so callers do not touch data_handler directly.
    def get_total_lines(self) -> int:
        return int(self.__prs_data.fetch_count())

    def get_page_from_row_indices(self, first_line: int, page_size: int) -> List[ParsedEntry]:
        if page_size <= 0:
            return []
        last_line = first_line + page_size - 1
        return self.__prs_data.read_page(first_line, last_line)

    def get_page_from_can_id_row_indices(self, can_id: int, first_line: int, page_size: int) -> List[ParsedEntry]:
        if page_size <= 0:
            return []
        last_line = first_line + page_size - 1
        return self.__prs_data.read_page_from_can_id(can_id, first_line, last_line)

    def get_page_from_can_ids_row_indices(self, can_ids: List[int], first_line: int, page_size: int) -> List[ParsedEntry]:
        if page_size <= 0:
            return []
        last_line = first_line + page_size - 1
        return self.__prs_data.read_page_from_can_ids(can_ids, first_line, last_line)

    def get_total_count_by_can_ids(self, can_ids: List[int]) -> int:
        # Not adapted yet for ParsedMmapInterface.
        raise NotImplementedError("get_total_count_by_can_ids is not adapted to ParsedMmapInterface yet")

    def get_first_last_timestamp(self) -> Tuple[Optional[float], Optional[float]]:
        first_ts, last_ts = self.__prs_data.get_first_last_timestamp()
        if first_ts is None or last_ts is None:
            return None, None
        return float(first_ts), float(last_ts)

    def get_first_last_timestamp_by_can_ids(self, can_ids: List[int]) -> Tuple[Optional[float], Optional[float]]:
        # Not adapted yet for ParsedMmapInterface.
        raise NotImplementedError("get_first_last_timestamp_by_can_ids is not adapted to ParsedMmapInterface yet")

    def get_start_row_by_timestamp(self, timestamp: float) -> int:
        # Not adapted yet for ParsedMmapInterface.
        raise NotImplementedError("get_start_row_by_timestamp is not adapted to ParsedMmapInterface yet")

    def get_end_row_by_timestamp(self, timestamp: float) -> int:
        # Not adapted yet for ParsedMmapInterface.
        raise NotImplementedError("get_end_row_by_timestamp is not adapted to ParsedMmapInterface yet")

    def get_start_row_by_can_id_timestamp(self, can_id: int, timestamp: float) -> int:
        # Not adapted yet for ParsedMmapInterface.
        raise NotImplementedError("get_start_row_by_can_id_timestamp is not adapted to ParsedMmapInterface yet")

    def get_end_row_by_can_id_timestamp(self, can_id: int, timestamp: float) -> int:
        # Not adapted yet for ParsedMmapInterface.
        raise NotImplementedError("get_end_row_by_can_id_timestamp is not adapted to ParsedMmapInterface yet")

    def get_progress_index(self) -> int:
        return max(0, int(self.__prs_data.fetch_count()))

    def get_metadata(self, db_file_path: str | None = None) -> dict[str, Any]:
        raw = self.__prs_data
        metadata: dict[str, Any] = {
            "record_id": self.record_id,
            "raw": raw,
            "raw_state": getattr(raw, "state", None),
            "raw_is_loading": bool(getattr(raw, "is_loading", False)),
            "total_lines": 0,
            "row_size": 0,
            "can_ids": [],
            "channels": [],
            "time_range": (None, None),
            "verified_size": 0,
            "mmap_file_count": 0,
            "decoded_db_file_paths": [],
        }

        if db_file_path is not None:
            metadata["decoded"] = self.__decode_handler if self.has_decode_mmaps() else None

        return metadata

    def get_all_entries(self) -> List[ParsedEntry]:
        return self.__prs_data.read_all_entries()
