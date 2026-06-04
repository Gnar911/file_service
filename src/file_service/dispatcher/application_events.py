from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from can_sdk.data_object import CANDBInfo
from ..record_id import RecordId


@dataclass(slots=True)
class FileDomainEvent:
    ts: float = field(default_factory=time.time)


@dataclass(slots=True)
class FileServiceStateEvent(FileDomainEvent):
    state: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DecoderStatusEvent(FileDomainEvent):
    kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DecodeStartedEvent(FileDomainEvent):
    record_id: RecordId | None = None
    file_path: str = ""
    db_file_path: str = ""
    expected_samples: int = 0


@dataclass(slots=True)
class DecodeCompletedEvent(FileDomainEvent):
    record_id: RecordId | None = None
    file_path: str = ""
    db_file_path: str = ""


@dataclass(slots=True)
class DecodeFileNotFoundEvent(FileDomainEvent):
    file_path: str = ""
    db_file_path: str = ""


@dataclass(slots=True)
class DecodeProgressEvent(FileDomainEvent):
    file_path: str = ""
    current_size: int = 0
    verified_size: int = 0
    mmap_file_count: int = 0
    is_loading: bool = False
    percent: int = 0


@dataclass(slots=True)
class DecodeSignalListEvent(FileDomainEvent):
    file_path: str = ""
    db_file_path: str = ""
    signal_list: list[tuple[int, int]] = field(default_factory=list)


@dataclass(slots=True)
class ParserStatusEvent(FileDomainEvent):
    record_id: RecordId | None = None
    status: int = 0
    kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DecodeStatusEvent(FileDomainEvent):
    record_id: RecordId | None = None
    status: int = 0
    kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DBCLoadedEvent(FileDomainEvent):
    record_id: RecordId | None = None
    db_file_path: str = ""
    candb_info: CANDBInfo | None = None


@dataclass(slots=True)
class FileWorkerHealthEvent(FileDomainEvent):
    worker: str = ""
    alive: bool = False
    exit_code: int | None = None


@dataclass(slots=True)
class FileWorkerRawStatusEvent(FileDomainEvent):
    worker: str = ""
    raw: Any = None


@dataclass(slots=True)
class RecorderStatusEvent(FileDomainEvent):
    status: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
