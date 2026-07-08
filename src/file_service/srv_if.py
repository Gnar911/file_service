from __future__ import annotations

from abc import ABCMeta
from collections.abc import Sequence
from typing import Any, Callable, Type

from lw.service.base_service import ServiceState
from lw.singleton import SingletonMeta

from .file_service import FileService as _FileServiceImpl, LogfileMetadata
from .metadata_id import DBCId, DecodeId, LogId
from file_service.module import EntryUpdate, ParsedEntry, LogQuery, LogRecord


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

    """ NOTE: This is the minimun parse funcion, no database metadata storage -> No ParsedEntry
    """
    def parse_line(self, text_l: str) -> LogRecord | None:
        return self._service.parse_line(text_l)

    def parse_lines(self, text_l: str) -> list[LogRecord]:
        return self._service.parse_lines(text_l)

    # def detect_line_format(self, text_l: str) -> int | None:
    #     from file_service.parser.py_parser import LogParser
    #     from file_service.module.fs_core import FormatType
    #     parser = LogParser()
    #     pid = parser.detect_pattern(str(text_l or ""))
    #     return None if pid == FormatType.UNKNOWN else int(pid)
    
    """20260703  NOTE: 
    This method can not be called 2 times parallely, so the service need #TODO return None on the second time while the firts one still not finished yet. 
        by checking the worker alive because it has no ability to check the duplication
        Return None if one of following reason: 
        file type not supported.
        format not support. 
        parser worker fails to spawn.
         #TODO: Continue to take note
    """
    def parse_log(self, log_path: str) -> LogId | None:
        return self._service.parse_log_file(log_path)

    """ This method can not be called 2 times parallely, so the service need #TODO return None on the second time while the firts one still not finished yet. 
        Return None if one of following reason: 
        DBC file path is invalid / not found.
        DBC worker fails to spawn or load.
        #TODO: Continue to take note
    """
    def parse_dbc(self, db_path: str) -> DBCId | None:
        return self._service.parse_dbc_file(db_path)

    """ This method can not be called 2 times parallely, so the service need #TODO return None on the second time while the firts one still not finished yet. 
        Using the LogID and DBCId means that the metadata already existed, and we
        do decode on those existing data 
    """
    def decode(self, log_data: LogId, dbc_data: DBCId) -> DecodeId | None:
        return self._service.decode(log_data, dbc_data)
    
##################### Record buses API ##########################
    def start_recording(self) -> LogId | None:
        return self._service.start_recording()

    def stop_recording(self) -> None:
        self._service.stop_recording()

    def save_record(self, record_id: LogId) -> bool:
        #TODO: Add method save record -> it will copy the LogId path_token into 
        return self._service.save_record(record_id)

#################### Metadata query API ############################
    def is_log_file(self, file_path: str) -> bool:
        return self._service.is_supported_log_file(file_path)

    def get_logfile_metadata(self, id: LogId) -> LogfileMetadata | None:
        return self._service.get_logfile_metadata(id)

    def read_page(self, log_id: LogId, first: int, last: int) -> list[ParsedEntry]:
        """Sequential page over all rows (no filter)."""
        return self._service.read_page(log_id, first, last)

    # def update_entries(self, log_id: LogId, entry_updates: Sequence[EntryUpdate]) -> int:
    #     """Batch update existing rows by row_index (0-based) + LogRecord payload."""
    #     return self._service.update_entries(log_id, list(entry_updates))

    def save_log_edits(self, log_id: LogId, edited_lines: Sequence[EntryUpdate]) -> int:
        """App-facing alias used by ViewModel save flow."""
        return self._service.update_entries(log_id, list(edited_lines))

    # ---- Filtered paging (app-facing; native LogQuery stays hidden) ----------
    # The app passes plain ints/strings; the facade builds the query. Omitted
    # args mean "don't filter on that factor". These supersede the old
    # single-factor mmap methods and compose freely in one query.
    def read_page_filtered(
        self,
        log_id: LogId,
        first: int,
        last: int,
        *,
        can_ids: "Sequence[int] | None" = None,
        channels: "Sequence[str] | None" = None,
        directions: "Sequence[str | int] | None" = None,
        changed_only: bool = False,
        time_range: "tuple[float, float] | None" = None,
    ) -> list[ParsedEntry]:
        """Multi-factor page query from plain Python args.

        Combine any of ``can_ids`` / ``channels`` / ``directions`` /
        ``changed_only`` / ``time_range``. Directions accept "Rx"/"Tx"
        (case-insensitive) or the raw code (0=Rx, 1=Tx).
        """

        # Direction is stored as a small integer in the native layer (see prs_token.h:
        # 0 = Rx, 1 = Tx). The app speaks "Rx"/"Tx"; this keeps the mapping in one place
        # so callers never deal with the raw code.
        _DIRECTION_CODE = {"rx": 0, "tx": 1}
        def _encode_direction(value: "str | int") -> int:
            if isinstance(value, int):
                return int(value)
            code = _DIRECTION_CODE.get(str(value).strip().lower())
            if code is None:
                raise ValueError(f"Unknown direction {value!r}; expected 'Rx'/'Tx' or 0/1")
            return code

        def _build_log_query(
            *,
            can_ids: "Sequence[int] | None" = None,
            channels: "Sequence[str] | None" = None,
            directions: "Sequence[str | int] | None" = None,
            changed_only: bool = False,
            time_range: "tuple[float, float] | None" = None,
        ) -> LogQuery:
            query = LogQuery()
            if can_ids:
                query.can_ids = [int(c) for c in can_ids]
            if channels:
                query.channels = [str(c) for c in channels]
            if directions:
                query.directions = [_encode_direction(d) for d in directions]
            query.changed_only = bool(changed_only)
            if time_range is not None:
                first_ts, last_ts = time_range
                query.has_time_range = True
                query.first_ts = float(first_ts)
                query.last_ts = float(last_ts)
            return query

        query = _build_log_query(
            can_ids=can_ids,
            channels=channels,
            directions=directions,
            changed_only=changed_only,
            time_range=time_range,
        )
        return self._service.read_page_multi(log_id, query, first, last)

    def read_all_entries(self, log_id: LogId) -> list[ParsedEntry]:
        return self._service._query_log_entries(log_id, lambda p: p.read_all_entries())
    
    # def read_page_from_can_id(self, log_id: LogId, can_id: int, first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, can_ids=[can_id])

    # def read_page_from_can_ids(self, log_id: LogId, can_ids: Sequence[int], first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, can_ids=can_ids)

    # def read_page_from_can_id_changed(self, log_id: LogId, can_id: int, first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, can_ids=[can_id], changed_only=True)

    # def read_page_from_can_ids_changed(self, log_id: LogId, can_ids: Sequence[int], first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, can_ids=can_ids, changed_only=True)

    # def read_page_from_channel(self, log_id: LogId, channel: str, first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, channels=[channel])

    # def read_page_from_channels(self, log_id: LogId, channels: Sequence[str], first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, channels=channels)

    # def read_page_from_direction(self, log_id: LogId, direction: "str | int", first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, directions=[direction])

    # def read_page_from_directions(self, log_id: LogId, directions: "Sequence[str | int]", first: int, last: int) -> list[ParsedEntry]:
    #     return self.read_page_filtered(log_id, first, last, directions=directions)

################### Export #######################
    def export_log_csv(self, log_id: LogId, lines: list[Any], save_filepath: str | None = None) -> str | None:
        _ = (log_id, lines, save_filepath)
        return None

############### Event ###################
    def subscribe(self, event_type: Type[Any], callback: Callable[[Any], None]) -> None:
        self._service.subscribe(event_type, callback)

    def unsubscribe_all(self) -> None:
        self._service.unsubscribe_all()


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
