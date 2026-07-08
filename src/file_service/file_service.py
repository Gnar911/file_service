from __future__ import annotations

from dataclasses import dataclass
import multiprocessing as mp
import threading
from os.path import isfile
from pathlib import Path
from typing import Any, Callable
import shutil

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
from file_service.canlog_verification import CANLogVerification
from file_service.decode.dbc_manager import CANDBManager
from file_service.decode.decode_process import run_decode_async
from file_service.define import DATA_DIR, MMAP_TEMP_STORAGE_DIR, MMAP_LOCAL_STORAGE_DIR
from file_service.event_dispatcher import FileServiceDispatcher
from file_service.metadata_id import DBCId, DecodeId, LogId
from file_service.module.fs_core import EntryUpdate, ParsedEntry, LogRecord, LogQuery, MetaDataStorageInterface, parse_line as fs_core_parse_line, FormatType
from file_service.parser.py_parser import LogParser
from file_service.parser.parser_process import run_parser_async
from file_service.recorder.buses_traffic_recorder import writer_process
from file_service.srv_error import WorkerDiedError, WorkerSpawnError
from file_service.status import DecodeStatus, ParserStatus, RecorderStatus
from lw.define import CAN_SHARED_RING_SHM_NAME
from lw.logger_setup import LOG
from lw.service.base_service import BaseService, ServiceState
from lw.status_channel import StatusChannel
from lw.qt.qt_dispatcher import QtEventLoopDispatcher

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

        self._worker_alive: dict[str, bool] = {}
        self._recorder_state = StatusChannel(int(RecorderStatus.STOPPED))
        self.parser_channel = StatusChannel(int(-1))
        self._decoder_state = StatusChannel(int(-1))

        self._metadata_root = DATA_DIR / "metadata"
        self._log_root = self._metadata_root / "log"
        self._dbc_root = self._metadata_root / "dbc"
        self._decode_root = self._metadata_root / "decode"
        self._log_root.mkdir(parents=True, exist_ok=True)
        self._dbc_root.mkdir(parents=True, exist_ok=True)
        self._decode_root.mkdir(parents=True, exist_ok=True)

        self._dbc_manager = CANDBManager()
        self._dbc_manager.event_on_db_loaded.subscribe(self._on_dbc_manager_loaded)
        self._log_verification = CANLogVerification()
        self._dispatcher = FileServiceDispatcher()
        self._qt_dispatcher = QtEventLoopDispatcher()

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
        fmt = self._log_verification.verify_log_line(text_l)
        if fmt:
            return fs_core_parse_line(text_l, int(fmt))
        else:
            return None

    # def parse_lines(self, text_l: str) -> list[LogRecord]:
    #     parsed_lines: list[LogRecord] = []
    #     for line_number, line in enumerate(str(text_l or "").splitlines(), start=1):
    #         if not line.strip():
    #             continue
    #         parser = LogParser()
    #         fmt = parser.detect_pattern(line)
    #         if fmt == FormatType.UNKNOWN:
    #             continue
    #         if int(fmt) < int(FormatType.CANOE) or int(fmt) > int(FormatType.CANCMD_T3):
    #             continue
    #         parsed = fs_core_parse_line(line, int(fmt))
    #         if parsed is not None:
    #             parsed_lines.append(parsed)
    #     return parsed_lines

    def parse_log_file(self, file_path: str) -> LogId | None:
        self._require_running()
        normalized = str(file_path)
        if not self._log_verification.verify_log_file(normalized):
            return None

        log_id = LogId.new()

        self._qt_dispatcher.attach(self.parser_channel, self._on_parser_event)

        """ NOTE: 20270707 Spawn process/thread worker, the process may be died while doing work, in that case it never emit
                any status so the service never know it is done the work or not and the state is hanged at running.
                -> Need the heart beat thread here to tracking the pid of worker
        """
        proc = run_parser_async(
            file_path,
            log_id,
            self.parser_channel,
        )
        # self._worker_alive["parser"] = bool(proc.is_alive())
        # if not self._worker_alive["parser"]:
        #     raise WorkerSpawnError("Parser worker failed to spawn")

        # self._qt_dispatcher.detach()
        return log_id

    def parse_dbc_file(self, db_file_path: str) -> DBCId | None:
        self._require_running()

        normalized = str(db_file_path)
        if not isfile(normalized):
            return None

        dbc_id = DBCId.new()

        worker = threading.Thread(
            target=self._dbc_manager.load_database,
            args=(normalized, self._dbc_pkl_path(dbc_id)),
            daemon=True,
            name="FileService-dbc-loader",
        )
        worker.start()
        worker.join()

        loaded = self._dbc_manager.candb_dict.get(normalized)
        if loaded is None:
            raise WorkerSpawnError(f"DBC loader failed for file: {normalized}")
        return dbc_id

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

    def is_supported_log_file(self, file_path: str) -> bool:
        return self._log_verification.is_supported_log_file(file_path)

    def get_dbc_file_path(self, dbc_id: DBCId) -> str:
        return str(self._dbc_root / f"{dbc_id.path_token()}.pkl")

    def get_logfile_metadata(self, log_id: LogId) -> LogfileMetadata | None:
        parser = MetaDataStorageInterface(log_id.path_token())
        open_rc = int(parser.open_mmap())
        if open_rc != 0:
            raise WorkerDiedError(
                f"Failed to open parsed mmap for decode metadata: log_id={log_id}, rc={open_rc}"
            )

        try:
            entry_count = int(parser.fetch_count())
            file_path = str(getattr(parser, "get_file_path", lambda: "")() or "")
            first_ts, last_ts = parser.get_first_last_timestamp()
            return LogfileMetadata(
                file_path=file_path,
                entry_count=entry_count,
                first_timestamp=float(first_ts) if first_ts is not None else None,
                last_timestamp=float(last_ts) if last_ts is not None else None,
            )
        finally:
            parser.close_mmap()

    def _query_log_entries(
        self,
        log_id: LogId,
        query: Callable[[MetaDataStorageInterface], list[ParsedEntry]],
    ) -> list[ParsedEntry]:
        # Prefer local storage; fall back to temp storage.
        token = log_id.path_token()
        local_prefix = MMAP_LOCAL_STORAGE_DIR / token
        temp_prefix = MMAP_TEMP_STORAGE_DIR / token

        parser_path = None
        if local_prefix.exists():
            parser_path = str(local_prefix)
        elif temp_prefix.exists():
            parser_path = str(temp_prefix)
        else:
            # try to find any files/directories matching token under temp dir
            matches = list(Path(MMAP_TEMP_STORAGE_DIR).rglob(f"{token}*"))
            if matches:
                # use temp_prefix even if it's not a single directory
                parser_path = str(temp_prefix)

        if parser_path is None:
            raise FileNotFoundError(f"No mmap files found for token: {token}")

        parser = MetaDataStorageInterface(parser_path)
        return query(parser)

    def save_record(self, record_id: LogId) -> bool:
        """Copy record files from temp storage into local storage.

        Returns True if files were copied or already present in local storage.
        Returns False if nothing found to copy.
        """
        token = record_id.path_token()
        local_root = Path(MMAP_LOCAL_STORAGE_DIR)
        temp_root = Path(MMAP_TEMP_STORAGE_DIR)

        local_root.mkdir(parents=True, exist_ok=True)

        # If local already contains any matching files, consider it saved.
        local_matches = list(local_root.rglob(f"{token}*"))
        if local_matches:
            return True

        # Find files in temp storage matching token prefix.
        temp_matches = list(temp_root.rglob(f"{token}*"))
        if not temp_matches:
            return False

        for src in temp_matches:
            try:
                rel = src.relative_to(temp_root)
            except Exception:
                # if relative_to fails, just use the name
                rel = Path(src.name)
            dest = local_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if not dest.exists():
                    shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)

        return True

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
            self._recorder_state.detach()
            self._worker_alive["recorder"] = False
            self._recorder_proc = None
            self._active_recording_log_id = None
            self._active_recording_mmap_path = None

    def _on_parser_event(self, status: int) -> None:
        self._dispatcher.dispatch_event(ParserStatusEvent(status=int(status)))
        self._worker_alive["parser"] = False
        self._qt_dispatcher.detach()

    def on_decode_callback(self, status: int) -> None:
        self._dispatcher.dispatch_event(DecodeStatusEvent(status=int(status)))
        self._worker_alive["decoder"] = False
        if status == int(DecodeStatus.DONE):
            self._dispatcher.dispatch_event(DecodeCompletedEvent())
        self._qt_dispatcher.detach()

