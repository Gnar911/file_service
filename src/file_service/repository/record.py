from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from file_service.repository.file_handler.data_mmap_handler import CANLogRawDiskFile
from file_service.repository.file_handler.decode_mmap_handler import CANLogDecodedDiskFile
from lw.logger_setup import LOG

from file_service.define import DATA_DIR
from file_service.record_id import RecordId


class Record:
    def __init__(self, record_id: RecordId, mmap_dir: str | Path, mmap_name: str):
        if not str(mmap_dir):
            raise ValueError("mmap_dir is required")
        if not str(mmap_name):
            raise ValueError("mmap_name is required")

        self.record_id: RecordId = record_id
        self.data_handler: CANLogRawDiskFile = CANLogRawDiskFile(
            mmap_dir=str(mmap_dir),
            mmap_name=str(mmap_name),
        )
        self.decode_hander: CANLogDecodedDiskFile = CANLogDecodedDiskFile()

    def get_record_id(self) -> RecordId:
        return self.record_id

    def get_mmap_path(self) -> Path:
        return Path(self.data_handler.mmap_dir) / self.data_handler.mmap_name

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
    def _copy_segment_paths(self, source_paths: list[Path], source_base_path: str, destination_base_path: str) -> int:
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

    def has_runtime_mmaps(self) -> bool:
        return bool(self.data_handler.data_segment_paths()) and bool(self.data_handler.index_segment_paths())

    def get_runtime_mmap_paths(self) -> dict[str, list[Path]]:
        return {
            "data": self.data_handler.data_segment_paths(),
            "index": self.data_handler.index_segment_paths(),
            "channel_index": self.data_handler.channel_index_segment_paths(),
            "direction_index": self.data_handler.direction_index_segment_paths(),
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

    def get_data_dir(self) -> Path:
        return DATA_DIR / self.record_id.path_token()

    def save_record(self) -> int:
        data_paths = self.data_handler.data_segment_paths()
        index_paths = self.data_handler.index_segment_paths()
        if not data_paths or not index_paths:
            return 0

        target_dir = self.get_data_dir()
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

    @property
    def decode_handler(self) -> CANLogDecodedDiskFile:
        return self.decode_hander

    def get_decoded(self, db_file_path: str) -> CANLogDecodedDiskFile | None:
        _ = db_file_path
        return self.decode_hander if self.has_decode_mmaps() else None

    def get_metadata(self, db_file_path: str | None = None) -> dict[str, Any]:
        raw = self.data_handler
        metadata: dict[str, Any] = {
            "record_id": self.record_id,
            "raw": raw,
            "raw_state": getattr(raw, "state", None),
            "raw_is_loading": bool(getattr(raw, "is_loading", False)),
            "total_lines": int(raw.total_lines),
            "verified_size": int(raw.verified_size),
            "mmap_file_count": int(raw.mmap_file_count),
            "decoded_db_file_paths": [],
        }

        if db_file_path is not None:
            metadata["decoded"] = self.get_decoded(db_file_path)

        return metadata
