from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from canapp.data_object import CANDBInfo
from .metadata_id import DBCId, DecodeId, LogId


@dataclass(slots=True)
class FileDomainEvent:
    ts: float = field(default_factory=time.time)


@dataclass(slots=True)
class FileServiceStateEvent(FileDomainEvent):
    state: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DecodeStartedEvent(FileDomainEvent):
    decode_id: DecodeId | None = None
    log_id: LogId | None = None
    dbc_id: DBCId | None = None
    file_path: str = ""
    db_file_path: str = ""
    expected_samples: int = 0


@dataclass(slots=True)
class DecodeCompletedEvent(FileDomainEvent):
    decode_id: DecodeId | None = None
    log_id: LogId | None = None
    dbc_id: DBCId | None = None
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
class DBCLoadedEvent(FileDomainEvent):
    dbc_id: DBCId | None = None
    candb_info: CANDBInfo

@dataclass(slots=True)
class ParserStatusEvent(FileDomainEvent):
    status: int = -1
    log_id: LogId | None = None


@dataclass(slots=True)
class DecodeStatusEvent(FileDomainEvent):
    status: int = -1


@dataclass(slots=True)
class RecorderStatusEvent(FileDomainEvent):
    status: int = -1


