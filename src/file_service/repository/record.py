from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from can_sdk.data_object import CANLogDecodedDiskFile, CANLogRawDiskFile
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
    def mmap_family_paths(mmap_path: str) -> list[Path]:
        base = Path(str(mmap_path))
        stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
        stem_parts = stem.rsplit(".", 1)
        if len(stem_parts) == 2 and stem_parts[1].isdigit() and len(stem_parts[1]) == 3:
            stem = stem_parts[0]

        segments = sorted(base.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))
        if segments:
            return segments
        return [base] if base.exists() else []

    @staticmethod
    def copy_mmap_family(source_path: str, destination_path: str) -> int:
        source = Path(source_path)
        destination = Path(destination_path)
        source_stem = source.name[:-5] if source.name.endswith(".mmap") else source.name
        destination_stem = destination.name[:-5] if destination.name.endswith(".mmap") else destination.name

        copied = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        for path in Record.mmap_family_paths(source_path):
            if path.name == source.name:
                target_path = destination
            else:
                suffix = path.name[len(source_stem):]
                target_path = destination.parent / f"{destination_stem}{suffix}"
            shutil.copy2(path, target_path)
            copied += 1
        return copied

    @staticmethod
    def delete_mmap_family(mmap_path: str) -> int:
        removed = 0
        for path in Record.mmap_family_paths(mmap_path):
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except Exception as exc:
                LOG.debug("Failed to delete mmap file %s: %s", path, exc)
        return removed

    def has_runtime_mmaps(self) -> bool:
        return bool(self.data_handler.data_segment_paths()) and bool(self.data_handler.index_segment_paths())

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
        data_path = self.data_handler.data_mmap_path
        index_path = self.data_handler.index_mmap_path
        if not data_path or not index_path:
            return 0

        target_dir = self.get_data_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        base_name = self.data_handler.mmap_name
        saved_data_path = str(target_dir / f"{base_name}.data.mmap")
        saved_index_path = str(target_dir / f"{base_name}.index.mmap")
        saved_channel_index_path = self.channel_index_path_from_index(saved_index_path)
        saved_direction_index_path = self.direction_index_path_from_index(saved_index_path)

        copied = 0
        copied += self.copy_mmap_family(data_path, saved_data_path)
        copied += self.copy_mmap_family(index_path, saved_index_path)

        channel_index_path = self.data_handler.channel_index_mmap_path
        direction_index_path = self.data_handler.direction_index_mmap_path

        if channel_index_path:
            copied += self.copy_mmap_family(channel_index_path, saved_channel_index_path)
        if direction_index_path:
            copied += self.copy_mmap_family(direction_index_path, saved_direction_index_path)

        if copied > 0:
            self.data_handler.data_mmap_path = saved_data_path
            self.refresh_runtime()

        return copied

    def delete_runtime_mmaps(self) -> int:
        removed = 0
        removed += self.delete_mmap_family(self.data_handler.data_mmap_path)
        removed += self.delete_mmap_family(self.data_handler.index_mmap_path)
        removed += self.delete_mmap_family(self.data_handler.channel_index_mmap_path)
        removed += self.delete_mmap_family(self.data_handler.direction_index_mmap_path)

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

    @property
    def file_path(self) -> str:
        return self.data_handler.data_mmap_path

    def get_decoded(self, db_file_path: str) -> CANLogDecodedDiskFile | None:
        _ = db_file_path
        return self.decode_hander if self.has_decode_mmaps() else None

    def get_metadata(self, db_file_path: str | None = None) -> dict[str, Any]:
        raw = self.data_handler
        metadata: dict[str, Any] = {
            "record_id": self.record_id,
            "dataset_path": self.file_path,
            "raw": raw,
            "raw_state": getattr(raw, "state", None),
            "raw_is_loading": bool(getattr(raw, "is_loading", False)),
            "raw_data_mmap_path": raw.data_mmap_path,
            "raw_index_mmap_path": raw.index_mmap_path,
            "raw_channel_index_mmap_path": raw.channel_index_mmap_path,
            "raw_direction_index_mmap_path": raw.direction_index_mmap_path,
            "total_lines": int(raw.total_lines),
            "verified_size": int(raw.verified_size),
            "mmap_file_count": int(raw.mmap_file_count),
            "decoded_db_file_paths": [],
        }

        if db_file_path is not None:
            metadata["decoded"] = self.get_decoded(db_file_path)

        return metadata
