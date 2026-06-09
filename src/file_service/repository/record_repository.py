from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Optional

from file_service.define import MMAP_TEMP_STORAGE_DIR
from file_service.record_id import RecordId
from file_service.repository.record import Record


class RecordRepository:
    def __init__(self):
        self._records_by_id: Dict[RecordId, Record] = {}
        self._record_id_by_mmap_path: Dict[str, RecordId] = {}
        self.data_lock = threading.Lock()

    @staticmethod
    def _normalize_mmap_base(path_value: str | Path) -> str:
        path = Path(path_value)
        return str(path)

    @staticmethod
    def _token_from_mmap_like_path(path_value: str) -> tuple[str, str]:
        path = Path(path_value)
        stem = path.name[:-5] if path.name.endswith(".mmap") else path.name
        stem_parts = stem.rsplit(".", 1)
        if len(stem_parts) == 2 and stem_parts[1].isdigit() and len(stem_parts[1]) == 3:
            stem = stem_parts[0]

        for suffix in (".index.channel", ".index.direction", ".data", ".index"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break

        return str(path.parent), stem

    def is_record_exist(self, record_id: RecordId) -> bool:
        with self.data_lock:
            return record_id in self._records_by_id

    def create_record(self) -> RecordId:
        record_id = RecordId.new()
        base_name = record_id.path_token()
        record = Record(
            record_id=record_id,
            base_dir=MMAP_TEMP_STORAGE_DIR,
            base_name=base_name,
        )
        mmap_path_key = self._normalize_mmap_base(record.get_base_path())

        with self.data_lock:
            self._records_by_id[record_id] = record
            self._record_id_by_mmap_path[mmap_path_key] = record_id
        return record_id

    def generate_mmap_path(self) -> Path:
        record_id = self.create_record()
        return self.get_mmap_path(record_id)

    def get_mmap_path(self, record_id: RecordId) -> Path:
        record = self.get_record(record_id)
        if record is None:
            raise KeyError(f"record_id not found: {record_id}")
        return record.get_base_path()

    def get_record_id_by_mmap_path(self, mmap_path: str | Path) -> Optional[RecordId]:
        key = self._normalize_mmap_base(mmap_path)
        with self.data_lock:
            return self._record_id_by_mmap_path.get(key)

    def get_record(self, record_id: RecordId) -> Optional[Record]:
        with self.data_lock:
            return self._records_by_id.get(record_id)

    def list_record_ids(self) -> list[RecordId]:
        with self.data_lock:
            return list(self._records_by_id.keys())

    def get_record_id(self, file_path: str) -> RecordId | None:
        folder, token = self._token_from_mmap_like_path(file_path)
        key = self._normalize_mmap_base(Path(folder) / token)
        with self.data_lock:
            return self._record_id_by_mmap_path.get(key)

    def resolve_record_id(self, record_id: RecordId) -> RecordId | None:
        return record_id if self.is_record_exist(record_id) else None

    def resolve_file_path(self, record_id: RecordId) -> str:
        record = self.get_record(record_id)
        return str(record.get_base_path()) if record is not None else ""

    def has_runtime_mmaps(self, record_id: RecordId) -> bool:
        record = self.get_record(record_id)
        return bool(record and record.has_runtime_mmaps())

    def save_record(self, record_id: RecordId) -> bool:
        record = self.get_record(record_id)
        if record is None:
            return False
        copied = record.save_record()
        return copied > 0

    def remove_record(self, record_id: RecordId) -> bool:
        with self.data_lock:
            record = self._records_by_id.pop(record_id, None)
            if record is None:
                return False
            mmap_key = self._normalize_mmap_base(record.get_base_path())
            self._record_id_by_mmap_path.pop(mmap_key, None)
        return True

    def mark_failed(self, file_key: RecordId) -> None:
        self.remove_record(file_key)


# DEPRECATED names
CanLogRepository = RecordRepository
CANLogRepository = RecordRepository
