from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, List, Optional, Tuple

from can_sdk.data_object import CANLogLine
from file_service.define import MMAP_LOCAL_STORAGE_DIR, MMAP_TEMP_STORAGE_DIR
from file_service.repository.file_handler.data_mmap_handler import CANLogRawDiskFile
from file_service.repository.file_handler.decode_mmap_handler import CANLogDecodedDiskFile
from file_service.repository.file_handler.dbc_pkl_handler import DBCPklHandler
from lw.logger_setup import LOG
from file_service.parser.native.can_parser_api import MmapHeaderConstract

from file_service.record_id import RecordId


class Record:
    _MAIN_STATUS_OFFSET = MmapHeaderConstract.STATUS_OFFSET
    _MAIN_PROGRESS_OFFSET = MmapHeaderConstract.WRITE_COUNT_OFFSET
    _DATA_STATUS_OFFSET = MmapHeaderConstract.STATUS_OFFSET

    def __init__(self, record_id: RecordId, base_dir: str | Path, base_name: str):
        if not str(base_dir):
            raise ValueError("base_dir is required")
        if not str(base_name):
            raise ValueError("base_name is required")

        token_base_name = record_id.path_token()
        if str(base_name) != token_base_name:
            raise ValueError("base_name must equal record_id.path_token()")

        self.record_id: RecordId = record_id
        self.data_handler: CANLogRawDiskFile = CANLogRawDiskFile(
            mmap_dir=str(base_dir),
            mmap_name=token_base_name,
        )
        base_path = Path(base_dir) / token_base_name
        self.decode_hander: CANLogDecodedDiskFile = CANLogDecodedDiskFile(path=base_path)
        self.pkl_handler: DBCPklHandler = DBCPklHandler(pkl_dir=Path(base_dir))

    def is_decoded(self) -> bool:
        return self.has_decode_mmaps()

    def get_record_id(self) -> RecordId:
        return self.record_id

    def get_base_path(self) -> Path:
        return Path(self.data_handler.mmap_dir) / self.data_handler.mmap_name

    @classmethod
    def open_runtime_record(cls, record_id: RecordId) -> Record:
        token = record_id.path_token()
        for base_dir in (MMAP_LOCAL_STORAGE_DIR / token, MMAP_TEMP_STORAGE_DIR):
            record = cls(record_id=record_id, base_dir=base_dir, base_name=token)
            record.refresh_runtime()
            if record.has_runtime_mmaps():
                return record
        raise ValueError(f"Record has no runtime mmaps: {record_id}")

    def get_dbc_pkl_path(self) -> Path:
        base_path = self.get_base_path()
        return base_path.parent / f"{base_path.name}.pkl"

    # Replay-facing accessors so callers do not touch data_handler directly.
    def get_total_lines(self) -> int:
        self.refresh_runtime()
        return int(self.data_handler.total_lines)

    def get_page_from_row_indices(self, first_line: int, page_size: int) -> List[CANLogLine]:
        return self.data_handler.get_page_from_row_indices(first_line, page_size)

    def get_page_from_can_id_row_indices(self, can_id: int, first_line: int, page_size: int) -> List[CANLogLine]:
        return self.data_handler.get_page_from_can_id_row_indices(int(can_id), first_line, page_size)

    def get_page_from_can_ids_row_indices(self, can_ids: List[int], first_line: int, page_size: int) -> List[CANLogLine]:
        return self.data_handler.get_page_from_can_ids_row_indices([int(cid) for cid in can_ids], first_line, page_size)

    def get_total_count_by_can_ids(self, can_ids: List[int]) -> int:
        return int(self.data_handler.get_total_count_by_can_ids([int(cid) for cid in can_ids]))

    def get_first_last_timestamp(self) -> Tuple[Optional[float], Optional[float]]:
        return self.data_handler.get_first_last_timestamp()

    def get_first_last_timestamp_by_can_ids(self, can_ids: List[int]) -> Tuple[Optional[float], Optional[float]]:
        return self.data_handler.get_first_last_timestamp_by_can_ids([int(cid) for cid in can_ids])

    def get_start_row_by_timestamp(self, timestamp: float) -> int:
        return int(self.data_handler.get_start_row_by_timestamp(float(timestamp)))

    def get_end_row_by_timestamp(self, timestamp: float) -> int:
        return int(self.data_handler.get_end_row_by_timestamp(float(timestamp)))

    def get_start_row_by_can_id_timestamp(self, can_id: int, timestamp: float) -> int:
        return int(self.data_handler.get_start_row_by_can_id_timestamp(int(can_id), float(timestamp)))

    def get_end_row_by_can_id_timestamp(self, can_id: int, timestamp: float) -> int:
        return int(self.data_handler.get_end_row_by_can_id_timestamp(int(can_id), float(timestamp)))

    @staticmethod
    def _normalize_path_token(value: str) -> str:
        filtered = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value))
        return filtered.strip("_") or "default"


    @staticmethod
    def _as_str(value: Any) -> str:
        return str(value or "")

    @staticmethod
    def channel_index_path_from_index(index_path: str) -> str:
        base = index_path[:-5] if index_path.endswith(".mmap") else index_path
        return base + ".channel.mmap"

    @staticmethod
    def direction_index_path_from_index(index_path: str) -> str:
        base = index_path[:-5] if index_path.endswith(".mmap") else index_path
        return base + ".direction.mmap"

    @staticmethod
    def _copy_segment_paths(source_paths: list[Path], source_base_path: str, destination_base_path: str) -> int:
        source_base = Path(source_base_path)
        destination_base = Path(destination_base_path)
        source_stem = source_base.name[:-5] if source_base.name.endswith(".mmap") else source_base.name
        destination_stem = destination_base.name[:-5] if destination_base.name.endswith(".mmap") else destination_base.name

        copied = 0
        destination_base.parent.mkdir(parents=True, exist_ok=True)
        for path in source_paths:
            if path.name == source_base.name:
                target_path = destination_base
            else:
                suffix = path.name[len(source_stem):]
                target_path = destination_base.parent / f"{destination_stem}{suffix}"

            # Save can be invoked repeatedly; treat already-copied segments as success.
            if path.resolve() == target_path.resolve():
                copied += 1
                continue

            shutil.copy2(path, target_path)
            copied += 1
        return copied

    def _delete_segment_paths(self, paths: list[Path]) -> int:
        removed = 0
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except Exception as exc:
                LOG.debug("Failed to delete mmap file %s: %s", path, exc)
        return removed

    def delete_mmap_family(self, base_path: str | Path) -> int:
        base = Path(base_path)
        paths: list[Path] = []
        if base.exists():
            paths.append(base)

        stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
        paths.extend(sorted(base.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap")))

        # Deduplicate while preserving order.
        seen: set[Path] = set()
        ordered_paths: list[Path] = []
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            ordered_paths.append(path)

        return self._delete_segment_paths(ordered_paths)

    def has_runtime_mmaps(self) -> bool:
        return bool(self.data_handler.data_segment_paths()) and bool(self.data_handler.index_segment_paths())

    def get_runtime_mmap_paths(self) -> dict[str, list[Path]]:
        return {
            "data": self.data_handler.data_segment_paths(),
            "index": self.data_handler.index_segment_paths(),
            "channel_index": self.data_handler.channel_index_segment_paths(),
            "direction_index": self.data_handler.direction_index_segment_paths(),
        }

    def _status_path(self) -> Path:
        data_paths = self.data_handler.data_segment_paths()
        return data_paths[0] if data_paths else Path(self.data_handler.data_mmap_path)

    def _decode_status_path(self) -> Path | None:
        row_index_paths = self.get_decode_mmap_paths().get("row_index", [])
        return row_index_paths[0] if row_index_paths else None

    @staticmethod
    def _read_u32(path: Path, offset: int, default: int = -1) -> int:
        if not path.exists():
            return int(default)
        try:
            with open(path, "rb") as file_obj:
                file_obj.seek(int(offset))
                raw = file_obj.read(MmapHeaderConstract.STATUS_STRUCT.size)
                if len(raw) < MmapHeaderConstract.STATUS_STRUCT.size:
                    return int(default)
                return int(MmapHeaderConstract.STATUS_STRUCT.unpack(raw)[0])
        except Exception:
            return int(default)

    @staticmethod
    def _read_u64(path: Path, offset: int, default: int = 0) -> int:
        if not path.exists():
            return int(default)
        try:
            with open(path, "rb") as file_obj:
                file_obj.seek(int(offset))
                raw = file_obj.read(MmapHeaderConstract.WRITE_COUNT_STRUCT.size)
                if len(raw) < MmapHeaderConstract.WRITE_COUNT_STRUCT.size:
                    return int(default)
                return int(MmapHeaderConstract.WRITE_COUNT_STRUCT.unpack(raw)[0])
        except Exception:
            return int(default)

    def get_status(self) -> int:
        self.refresh_runtime()
        return self._read_u32(self._status_path(), self._MAIN_STATUS_OFFSET, default=-1)

    def get_data_status(self) -> int:
        self.refresh_runtime()
        return self._read_u32(self._status_path(), self._DATA_STATUS_OFFSET, default=-1)

    def get_decode_status(self) -> int:
        self.refresh_runtime()
        decode_path = self._decode_status_path()
        if decode_path is None:
            return -1
        return self._read_u32(decode_path, self._DATA_STATUS_OFFSET, default=-1)

    def get_progress_index(self) -> int:
        self.refresh_runtime()
        progress = self._read_u64(self._status_path(), self._MAIN_PROGRESS_OFFSET, default=0)
        return max(0, int(progress))

    def get_decode_mmap_paths(self) -> dict[str, list[Path]]:
        if not self.has_decode_mmaps():
            return {
                "signal_dir": [],
                "row_index_changed": [],
                "row_index": [],
                "value": [],
                "rawvalue": [],
            }

        return {
            "signal_dir": self.decode_hander.decode_signal_dir_segment_paths(),
            "row_index_changed": self.decode_hander.decode_row_index_changed_segment_paths(),
            "row_index": self.decode_hander.decode_row_index_segment_paths(),
            "value": self.decode_hander.decode_value_segment_paths(),
            "rawvalue": self.decode_hander.decode_rawvalue_segment_paths(),
        }

    def has_decode_mmaps(self) -> bool:
        decode = self.decode_hander
        seg_methods = (
            "decode_signal_dir_segment_paths",
            "decode_row_index_changed_segment_paths",
            "decode_row_index_segment_paths",
            "decode_value_segment_paths",
            "decode_rawvalue_segment_paths",
        )
        for method_name in seg_methods:
            method = getattr(decode, method_name, None)
            if callable(method) and method():
                return True

        return any(
            self._as_str(getattr(decode, attr, ""))
            for attr in (
                "decode_signal_dir_mmap_path",
                "decode_row_index_changed_mmap_path",
                "decode_row_index_mmap_path",
                "decode_value_mmap_path",
                "decode_rawvalue_mmap_path",
            )
        )

    def refresh_runtime(self) -> None:
        if hasattr(self.data_handler, "refresh_mmap_runtime"):
            self.data_handler.refresh_mmap_runtime()
        if hasattr(self.data_handler, "refresh_can_ids_runtime"):
            self.data_handler.refresh_can_ids_runtime()
        if self.has_decode_mmaps() and hasattr(self.decode_hander, "refresh_decode_mmap_runtime"):
            self.decode_hander.refresh_decode_mmap_runtime()

    def save_record(self) -> int:
        data_paths = self.data_handler.data_segment_paths()
        index_paths = self.data_handler.index_segment_paths()
        if not data_paths or not index_paths:
            return 0

        target_dir = MMAP_LOCAL_STORAGE_DIR / self.record_id.path_token()
        target_dir.mkdir(parents=True, exist_ok=True)

        base_name = self.data_handler.mmap_name
        saved_data_path = str(target_dir / f"{base_name}.data.mmap")
        saved_index_path = str(target_dir / f"{base_name}.index.mmap")
        saved_channel_index_path = self.channel_index_path_from_index(saved_index_path)
        saved_direction_index_path = self.direction_index_path_from_index(saved_index_path)

        copied = 0
        copied += self._copy_segment_paths(data_paths, self.data_handler.data_mmap_path, saved_data_path)
        copied += self._copy_segment_paths(index_paths, self.data_handler.index_mmap_path, saved_index_path)

        channel_paths = self.data_handler.channel_index_segment_paths()
        direction_paths = self.data_handler.direction_index_segment_paths()

        if channel_paths:
            copied += self._copy_segment_paths(channel_paths, self.data_handler.channel_index_mmap_path, saved_channel_index_path)
        if direction_paths:
            copied += self._copy_segment_paths(direction_paths, self.data_handler.direction_index_mmap_path, saved_direction_index_path)

        if copied > 0:
            self.data_handler.data_mmap_path = saved_data_path
            self.refresh_runtime()

        return copied

    def delete_runtime_mmaps(self) -> int:
        removed = 0
        removed += self._delete_segment_paths(self.data_handler.data_segment_paths())
        removed += self._delete_segment_paths(self.data_handler.index_segment_paths())
        removed += self._delete_segment_paths(self.data_handler.channel_index_segment_paths())
        removed += self._delete_segment_paths(self.data_handler.direction_index_segment_paths())

        decode_signal_dir_path = self._as_str(getattr(self.decode_hander, "decode_signal_dir_mmap_path", ""))
        decode_changed_index_path = self._as_str(getattr(self.decode_hander, "decode_row_index_changed_mmap_path", ""))
        decode_row_index_path = self._as_str(getattr(self.decode_hander, "decode_row_index_mmap_path", ""))
        decode_value_path = self._as_str(getattr(self.decode_hander, "decode_value_mmap_path", ""))
        decode_rawvalue_path = self._as_str(getattr(self.decode_hander, "decode_rawvalue_mmap_path", ""))

        if decode_signal_dir_path:
            removed += self.delete_mmap_family(decode_signal_dir_path)
        if decode_changed_index_path:
            removed += self.delete_mmap_family(decode_changed_index_path)
        if decode_row_index_path:
            removed += self.delete_mmap_family(decode_row_index_path)
        if decode_value_path:
            removed += self.delete_mmap_family(decode_value_path)
        if decode_rawvalue_path:
            removed += self.delete_mmap_family(decode_rawvalue_path)

        return removed

    @property
    def raw(self) -> CANLogRawDiskFile:
        return self.data_handler

    def get_metadata(self, db_file_path: str | None = None) -> dict[str, Any]:
        self.refresh_runtime()
        raw = self.data_handler
        metadata: dict[str, Any] = {
            "record_id": self.record_id,
            "raw": raw,
            "raw_state": getattr(raw, "state", None),
            "raw_is_loading": bool(getattr(raw, "is_loading", False)),
            "total_lines": int(raw.total_lines),
            "row_size": self.get_total_lines(),
            "can_ids": [int(cid) for cid in self.data_handler.get_all_can_ids()],
            "channels": [str(ch) for ch in self.data_handler.get_all_channels()],
            "time_range": self.data_handler.get_first_last_timestamp(),
            "verified_size": int(raw.verified_size),
            "mmap_file_count": int(raw.mmap_file_count),
            "decoded_db_file_paths": [],
        }

        if db_file_path is not None:
            metadata["decoded"] = self.decode_hander if self.has_decode_mmaps() else None

        return metadata
