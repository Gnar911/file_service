from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
import threading
from pathlib import Path
from typing import Any, Callable
from collections.abc import Sequence
from abc import ABCMeta

#from can_sdk.data_object import CANDBInfo
from file_service.application_events import (
    DBCLoadedEvent,
    DecodeCompletedEvent,
    DecodeStartedEvent,
    DecodeStatusEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
    RecorderStatusEvent,
)
# from file_service.canlog_verification import CANLogVerification
from file_service.decode.dbc_manager import CANDBManager, CANDBInfo
from file_service.decode.decode_process import run_decode_async
from lw.define import DATA_DIR, MMAP_TEMP_STORAGE_DIR, MMAP_LOCAL_STORAGE_DIR
from file_service.event_dispatcher import FileServiceDispatcher
from file_service.metadata_id import DBCId, DecodeId, LogId
from file_service.module.fs_core import *
from file_service.parser.parser_process import run_parser_async
from file_service.recorder.buses_traffic_recorder import writer_process
from file_service.srv_error import WorkerDiedError, WorkerSpawnError
from file_service.status import DecodeStatus, ParserStatus, RecorderStatus
from file_service.task_manager import Future, ProcessPoolExecutor, partial
from lw.define import CAN_SHARED_RING_SHM_NAME
from lw.logger_setup import LOG
from lw.service.base_service import BaseService, ServiceState
from lw.status_channel import StatusChannel
from lw.qt.qt_dispatcher import QtEventLoopDispatcher
from lw.singleton import SingletonMeta
from lw.MainThreadDispatcher import MainThreadDispatcher
from canapp.data_object import CANLogLine


@dataclass(frozen=True)
class LogfileMetadata:
    file_path: str
    entry_count: int
    first_timestamp: float | None
    last_timestamp: float | None


class FileService(BaseService):
    def __init__(self):
        super().__init__(service_name="FileService")

        self._recorder_stop = mp.Event()
        self._recorder_proc: mp.Process | None = None
        self._recorder_last_status: int | None = None
        self._active_recording_log_id: LogId | None = None
        self._active_recording_mmap_path: str | None = None

        #self._worker_alive: dict[str, bool] = {}
        # self._recorder_state = StatusChannel(int(RecorderStatus.STOPPED))
        # self.parser_channel = StatusChannel(int(-1))
        # self._decoder_state = StatusChannel(int(-1))

        self._metadata_root = DATA_DIR / "metadata"
        self._log_root = self._metadata_root / "log"
        self._dbc_root = self._metadata_root / "dbc"
        self._decode_root = self._metadata_root / "decode"
        self._log_root.mkdir(parents=True, exist_ok=True)
        self._dbc_root.mkdir(parents=True, exist_ok=True)
        self._decode_root.mkdir(parents=True, exist_ok=True)

        self._dbc_manager = CANDBManager()
        self._dbc_manager.event_on_db_loaded.subscribe(self._on_dbc_manager_loaded)
        #self._log_verification = CANLogVerification()
        """ NOTE: keep the service framework independent here"""
        #self._qt_dispatcher = QtEventLoopDispatcher()
        self._main_dispatcher = MainThreadDispatcher()
        self._dispatcher = FileServiceDispatcher()

        self._executor = ProcessPoolExecutor(max_workers=1)
        # Thread executor for lightweight tasks (DBC parsing)
        from concurrent.futures import ThreadPoolExecutor
        self._dbc_executor = ThreadPoolExecutor(max_workers=1)
        

    def _do_start(self) -> None:
        self._recorder_stop.clear()
        self._dispatcher.dispatch_event(FileServiceStateEvent(state="STARTING"))
        self._dispatcher.dispatch_event(FileServiceStateEvent(state="RUNNING"))

    def _do_stop(self) -> None:
        self._dispatcher.dispatch_event(FileServiceStateEvent(state="STOPPING"))
        self._stop_parser_worker()
        self.stop_recording()
        self._dispatcher.dispatch_event(FileServiceStateEvent(state="STOPPED"))

    def subscribe(self, event_type: type[Any], callback) -> None:
        self._dispatcher.subscribe(event_type, callback)

    def unsubscribe_all(self) -> None:
        self._dispatcher.unsubscribe_all()

    def _require_running(self) -> None:
        if self.state != ServiceState.RUNNING:
            raise RuntimeError("FileService must be started before invoking worker operations")


    def parse_line(self, text_l: str) -> LogRecord | None:
        return fs_core_parse_line(text_l)

    def parse_lines(self, text_l: str) -> list[LogRecord]:
        return fs_core_parse_lines(str(text_l or ""))

    # def parse_log_file(self, file_path: str) -> LogId | None:
    #     self._require_running()
    #     log_id = LogId.new()

    #     self._qt_dispatcher.attach(self.parser_channel, self._on_parser_event)

    #     """ TODO: worker track thread system: 20270707 Spawn process/thread worker, the process may be died while doing work, in that case it never emit
    #             any status so the service never know it is done the work or not and the state is hanged at running.
    #             -> Need the heart beat thread here to tracking the pid of worker
    #     """
    #     proc = run_parser_async(
    #         file_path,
    #         log_id,
    #         self.parser_channel,
    #     )
    #     return log_id
    
    def parse_log_file(self, file_path: str) -> None:
        self._require_running()

        log_id = LogId.new()

        try:
            future = self._executor.submit(
                run_worker_segmented,
                file_path,
                log_id.path_token(),
            )

            future.add_done_callback(
                partial(
                    self._on_parse_completed,
                    log_id=log_id,
                )
            )

            evt = ParserStatusEvent(status=int(ParserStatus.STARTED), log_id=log_id)
            self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))
        except Exception:
                evt = ParserStatusEvent(status=int(ParserStatus.FAILED), log_id=log_id)
                self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))
    
    """ NOTE: Dispatcher on thread, not UI thread
            So the app must handle the marshal to merge it into their GUI thread for update UI.
            The service here kept GUI framework independent.
    """
    def _on_parse_completed(
        self,
        future: Future,
        log_id: LogId,
    ) -> None:

        """ NOTE: detect the unexpected process died"""
        try:

            future.result()

        except Exception:

                evt = ParserStatusEvent(status=int(ParserStatus.FAILED), log_id=log_id)
                self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))

        else:

                evt = ParserStatusEvent(status=int(ParserStatus.DONE), log_id=log_id)
                self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))

    """ NOTE: Make the parse_dbc_file return the result immediately here will make other viewmodels listeners unknown about the status
        The exception terminates the thread.
        Python prints a traceback to stderr (unless you've overridden threading.excepthook).
        worker.join() returns normally.
        The exception is not propagated to the thread that called join() -> can not handle the failed case.
    """
    # def parse_dbc_file(self, db_file_path: str):
    #     self._require_running()

    #     dbc_id = DBCId.new()

    #     worker = threading.Thread(
    #         target=self._dbc_manager.parse_database,
    #         args=(db_file_path, dbc_id.path_token()),
    #         daemon=True,
    #         name="FileService-dbc-loader",
    #     )
    #     worker.start()
    #     worker.join()
    #     candb_info = self._dbc_manager.load_database()
    #     evt = DBCLoadedEvent(dbc_id=dbc_id, candb_info=candb_info)
    #     self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))
    
    def parse_dbc_file(self, db_file_path: str):
        self._require_running()

        dbc_id = DBCId.new()
        future = self._dbc_executor.submit(
            self._dbc_manager.parse_database,
            db_file_path,
            dbc_id,
        )

        def _on_done(fut):
            try:
                # will raise if parse failed
                fut.result()
                # NOTE: No need here
                #candb_info = self._dbc_manager.load_database(dbc_id)
                evt = DBCLoadedEvent(dbc_id=dbc_id, candb_info=None)
                self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))
            except Exception:
                # NOTE: trace for all exception error, handle as failed case
                LOG.exception("DBC loading failed")
                evt = DBCLoadedEvent(dbc_id=None, candb_info=None)
                self._main_dispatcher.post(partial(self._dispatcher.dispatch_event, evt))

        future.add_done_callback(_on_done)
        return dbc_id

    def get_candb_data(self, id: DBCId) -> CANDBInfo:
        return self._dbc_manager.load_database(id)

    def decode(self, log_id: LogId, dbc_id: DBCId) -> DecodeId | None:
        self._require_running()

        decode_id = DecodeId.new(log_id=log_id, dbc_id=dbc_id)
        # Use QtStatusChannel (status + wakeup) for decode worker.
        status = self._decoder_state
        log_mmap_path = (self._log_root / log_id.path_token()).with_suffix(".mmap")
        dbc_pkl_path = str(self._dbc_root / f"{dbc_id.path_token()}.pkl")
        db_file_path = dbc_id.path_token()
        decode_file_path = str(log_mmap_path)

        self._dispatcher.dispatch_event(
            DecodeStartedEvent(
                decode_id=decode_id,
                log_id=log_id,
                dbc_id=dbc_id,
                file_path=decode_file_path,
                db_file_path=db_file_path,
            )
        )

        status.attach(self.on_decode_callback)

        proc = run_decode_async(decode_id.log_id, status, dbc_pkl_path)

        self._worker_alive["decoder"] = bool(proc.is_alive())
        if not self._worker_alive["decoder"]:
            raise WorkerSpawnError("Decode worker failed to spawn")
        return decode_id

    def request_parse_job(self, file_path: str) -> LogId | None:
        self._require_running()
        return self.parse_log_file(file_path)

    def request_stop_parse_log_async(self, _file_path: str) -> None:
        LOG.info("Stop parse request received")

    def start_recording(self) -> LogId | None:
        self._require_running()
        if self._recorder_proc is not None and self._recorder_proc.is_alive():
            return None

        log_id = LogId.new()
        recorder_mmap_path = str(self._log_mmap_path(log_id))

        # Use QtStatusChannel (status + wakeup) for recorder worker.
        status = self._recorder_state
        status.attach(self.on_recorder_callback)

        self._recorder_last_status = None
        self._active_recording_log_id = log_id
        self._active_recording_mmap_path = recorder_mmap_path
        self._recorder_stop.clear()
        self._recorder_proc = self._spawn_process(
            "recorder",
            writer_process,
            args=(
                str(CAN_SHARED_RING_SHM_NAME),
                log_id,
                self._recorder_stop,
                self._recorder_state,
            ),
        )

        data_base = Path(recorder_mmap_path).with_suffix("")
        first_segment = data_base.parent / f"{data_base.name}.000.mmap"
        first_segment.parent.mkdir(parents=True, exist_ok=True)
        first_segment.touch(exist_ok=True)

        return log_id

    def stop_recording(self) -> None:
        self._recorder_stop.set()
        if self._recorder_proc is not None:
            self._recorder_proc.join(timeout=1.0)
            alive = bool(self._recorder_proc.is_alive())
            self._worker_alive["recorder"] = alive
            if not alive:
                self._recorder_proc = None
        if self._recorder_proc is None:
            self._qt_dispatcher.detach()
            self._active_recording_log_id = None
            self._active_recording_mmap_path = None

    def _query_log_entries(
        self,
        log_id: LogId,
        query: Callable[[MetaDataStorageInterface], list[ParsedEntry]],
    ) -> list[ParsedEntry]:
        token = log_id.path_token()
        parser = MetaDataStorageInterface(token)
        return query(parser)

    def save_record(self, record_id: LogId) -> bool:
        """Copy record files from temp storage into local storage.

        Returns True if files were copied or already present in local storage.
        Returns False if nothing found to copy.
        """
        # token = record_id.path_token()
        # local_root = Path(MMAP_LOCAL_STORAGE_DIR)
        # temp_root = Path(MMAP_TEMP_STORAGE_DIR)

        # local_root.mkdir(parents=True, exist_ok=True)

        # # If local already contains any matching files, consider it saved.
        # local_matches = list(local_root.rglob(f"{token}*"))
        # if local_matches:
        #     return True

        # # Find files in temp storage matching token prefix.
        # temp_matches = list(temp_root.rglob(f"{token}*"))
        # if not temp_matches:
        #     return False

        # for src in temp_matches:
        #     try:
        #         rel = src.relative_to(temp_root)
        #     except Exception:
        #         # if relative_to fails, just use the name
        #         rel = Path(src.name)
        #     dest = local_root / rel
        #     dest.parent.mkdir(parents=True, exist_ok=True)
        #     if src.is_dir():
        #         if not dest.exists():
        #             shutil.copytree(src, dest)
        #     else:
        #         shutil.copy2(src, dest)

        return True

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
        return self._query_log_entries(log_id, lambda p: p.read_page_multi(query, first, last))
    
    def read_page(self, log_id: LogId, first: int, last: int) -> list[ParsedEntry]:
        return self._query_log_entries(log_id, lambda p: p.read_page(first, last))

    def read_page_multi(self, log_id: LogId, query: LogQuery, first: int, last: int) -> list[ParsedEntry]:
        return self._query_log_entries(log_id, lambda p: p.read_page_multi(query, first, last))

    def update_entries(self, log_id: LogId, entries: list[EntryUpdate]) -> bool:
        if not entries:
            return False
        
        self._query_log_entries(log_id, lambda p: [p.update_entries(entries)])[0]
        return True

    def _stop_parser_worker(self) -> None:
        pass

    def _on_dbc_manager_loaded(self, candb_info: Any) -> None:
        db_file_path = str(getattr(candb_info, "file_path", "") or "")
        self._dispatcher.dispatch_event(
            DBCLoadedEvent(
                dbc_id=None,
                db_file_path=db_file_path,
                candb_info=candb_info,
            )
        )

    def _spawn_process(self, name: str, target, args: tuple[Any, ...], kwargs: dict[str, Any] | None = None) -> mp.Process:
        proc = mp.Process(
            target=target,
            args=args,
            kwargs=dict(kwargs or {}),
            daemon=True,
            name=f"FileService-{name}",
        )
        proc.start()
        self._worker_alive[name] = bool(proc.is_alive())
        return proc

    def on_recorder_callback(self, status: int) -> None:
        self._dispatcher.dispatch_event(RecorderStatusEvent(status=int(status)))
        recorder_state = RecorderStatus(status)
        self._recorder_last_status = int(recorder_state)
        if recorder_state == RecorderStatus.STOPPED:
            self._qt_dispatcher.detach()
            self._worker_alive["recorder"] = False
            self._recorder_proc = None
            self._active_recording_log_id = None
            self._active_recording_mmap_path = None

    # def _on_parser_event(self, status: int) -> None:
    #     self._dispatcher.dispatch_event(ParserStatusEvent(status=int(status)))
    #     self._worker_alive["parser"] = False
    #     self._qt_dispatcher.detach()

    def on_decode_callback(self, status: int) -> None:
        self._dispatcher.dispatch_event(DecodeStatusEvent(status=int(status)))
        self._worker_alive["decoder"] = False
        if status == int(DecodeStatus.DONE):
            self._dispatcher.dispatch_event(DecodeCompletedEvent())
        self._qt_dispatcher.detach()

class SingletonABCMeta(SingletonMeta, ABCMeta):
    pass


class _SingletonFileServiceImpl(FileService, metaclass=SingletonABCMeta):
    pass


class _SingletonFileServiceFacade(FileService, metaclass=SingletonABCMeta):
    def __init__(self):
        super().__init__()


def get_file_service() -> FileService:
    return _SingletonFileServiceFacade()


# def get_file_service_impl() -> FileService:
#     return _SingletonFileServiceImpl()


FileServiceClient = FileService


def get_file_service_client() -> FileService:
    return get_file_service()