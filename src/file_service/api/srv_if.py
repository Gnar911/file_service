from __future__ import annotations

from abc import ABCMeta
from typing import Any, Callable, Type

from lw.base_service import ServiceState
from lw.singleton import SingletonMeta

from ..file_service import FileService as _FileServiceImpl
from ..repository.record import Record
from ..record_id import RecordId
from file_service.module.parsed_mmap import ParsedEntry

class FileService:
    def __init__(self, service: _FileServiceImpl):
        self._service = service

###################### Service API ###########################
    def start(self) -> None:
        self._service.start()

    def stop(self) -> None:
        self._service.stop()

    def get_service_state(self) -> ServiceState:
        return self._service.state

###################### Log file parse, decode API ###########################
    def parse_line(self, text_l: str) -> ParsedEntry:
        return self._service.parse_line(text_l)

    def parse_lines(self, text_l: str) -> list[ParsedEntry]:
        return self._service.parse_lines(text_l)

    def parse_log(self, file_path: str, record_id: RecordId | None = None,) -> bool:
        return self._service.parse_log_file(file_path, record_id=record_id,)

    def parse_dbc(self, db_file_path: str, record_id: RecordId | None = None,) -> bool:
        return self._service.parse_dbc_file(db_file_path, record_id=record_id,)

    def decode(self, record_id: RecordId, dbc_file_path: str,) -> bool:
        return self._service.decode(record_id, dbc_file_path,)

    # Backward-compatible aliases.
    def parse_log_file(self, file_path: str, record_id: RecordId | None = None) -> bool:
        return self.parse_log(file_path, record_id=record_id)

    def parse_dbc_file(self, db_file_path: str, record_id: RecordId | None = None) -> bool:
        return self.parse_dbc(db_file_path, record_id=record_id)

    def decode_by_DBC(self, record_id: RecordId, db_file_path: str) -> bool:
        return self.decode(record_id, db_file_path)

    def decode_by_dbc(self, record_id: RecordId, db_file_path: str) -> bool:
        return self.decode(record_id, db_file_path)
    
##################### Record buses API ##########################
    def start_recording(self) -> bool:
        return self._service.start_recording()

    def stop_recording(self) -> None:
        self._service.stop_recording()

    def create_record(self) -> RecordId:
        return self._service.create_record()

    def create_record_from_mmap(self) -> RecordId:
        return self._service.create_record_from_mmap()

#################### Get result API ############################
    def save_record(self, record_id: RecordId) -> bool:
        return self._service.save_record(record_id)

    def is_log_file(self, file_path: str) -> bool:
        return self._service.is_supported_log_file(file_path)

    def get_record(self, record_id: RecordId) -> Record | None:
        return self._service.get_record(record_id)

    def list_log_records(self) -> list[RecordId]:
        return self._service.list_log_records()

################### Export #######################
    def export_log_csv(self, record_id: RecordId, lines: list[Any], save_filepath: str | None = None) -> str | None:
        return self._service.export_log_csv(record_id, lines, save_filepath)

############### Event ###################3
    def subscribe(self, event_type: Type[Any], callback: Callable[[Any], None]) -> None:
        self._service.subscribe(event_type, callback)

    def subscribe_any(self, callback: Callable[[Any], None]) -> None:
        self._service.subscribe_any(callback)


class SingletonABCMeta(SingletonMeta, ABCMeta):
    pass


class _SingletonFileServiceImpl(_FileServiceImpl, metaclass=SingletonABCMeta):
    pass


class _SingletonFileServiceFacade(FileService, metaclass=SingletonABCMeta):
    def __init__(self):
        super().__init__(get_file_service_impl())


def get_file_service() -> FileService:
    return _SingletonFileServiceFacade()


def get_file_service_impl() -> _FileServiceImpl:
    return _SingletonFileServiceImpl()


FileServiceClient = FileService


def get_file_service_client() -> FileService:
    return get_file_service()
