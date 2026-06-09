from __future__ import annotations

import multiprocessing as mp
import threading
from os.path import isfile
from pathlib import Path
from typing import Any

from file_service.api.application_events import (
    DBCLoadedEvent,
    DecodeStatusEvent,
    DecodeStartedEvent,
    DecoderStatusEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
    RecorderStatusEvent,
)
from file_service.decode.decode_process import run_decode_async
from file_service.decode.dbc_manager import CANDBManager
from file_service.dispatcher.event_dispatcher import FileServiceDispatcher
from file_service.exporter.can_log_export import CANLogExport
from file_service.parser.parser_process import run_parser_async
from file_service.module.fs_core import ParsedEntry, parse_line as fs_core_parse_line
from file_service.parser.python.py_parser import LogParser
from file_service.api.status import RecorderStatus, ParserStatus, DecodeStatus
from file_service.repository.record import Record
from file_service.record_id import RecordId
from file_service.recorder.buses_traffic_recorder import writer_process
from file_service.repository.record_repository import RecordRepository
from file_service.verification.can_log_verification import CANLogVerification
from lw.base_service import BaseService, ServiceState
from lw.define import CAN_SHARED_RING_SHM_NAME
from lw.logger_setup import LOG
from can_sdk.data_object import CANDBInfo


class FileService(BaseService):
    def __init__(self):
        super().__init__(service_name="FileService")

        self._recorder_stop = mp.Event()
        self._recorder_proc: mp.Process | None = None
        self._recorder_last_status: int | None = None
        self._active_recording_record_id: RecordId | None = None
        self._active_recording_mmap_path: str | None = None
        self._worker_alive: dict[str, bool] = {}
        self._recorder_state = mp.Value("i", int(RecorderStatus.IDLE))
        self._parser_state = mp.Value("i", int(ParserStatus.IDLE))
        self._decoder_state = mp.Value("i", int(DecodeStatus.IDLE))
        self._active_parse_record_id: RecordId | None = None
        self._active_decode_record_id: RecordId | None = None
        self._active_decode_file_path: str | None = None
        self._active_decode_db_file_path: str | None = None
        self._pending_dbc_record_ids: dict[str, RecordId] = {}

        self._log_repository = RecordRepository()
        self._dbc_manager = CANDBManager()
        self._dbc_manager.event_on_db_loaded.subscribe(self._on_dbc_manager_loaded)
        self._log_verification = CANLogVerification()
        self._log_export = CANLogExport(self._log_repository)
        self._dispatcher = FileServiceDispatcher()

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

    def subscribe_any(self, callback) -> None:
        self._dispatcher.subscribe_any(callback)

    def _require_running(self) -> None:
        if self.state != ServiceState.RUNNING:
            raise RuntimeError("FileService must be started before invoking worker operations")

    def decode(self, record_id: RecordId, db_file_path: str) -> bool:
        self._require_running()

        normalized_db_file_path = str(db_file_path)
        if not normalized_db_file_path:
            return False

        record = self._log_repository.get_record(record_id)
        if record is None:
            return False

        dbc_pkl_path = str(record.get_dbc_pkl_path())
        record_path_token = self._log_repository.get_mmap_path(record_id)

        return self._start_decode_job(
            record_id,
            record_path_token,
            normalized_db_file_path,
            dbc_pkl_path,
        )

        
    def request_parse_job(self, file_path: str) -> bool:
        self._require_running()
        return self.parse_log_file(file_path)

    def request_stop_parse_log_async(self, _file_path: str) -> None:
        LOG.info("Stop parse request received")

    def start_recording(self) -> bool:
        self._require_running()
        if self._recorder_proc is not None and self._recorder_proc.is_alive():
            return False

        record_id = self._log_repository.create_record()
        record = self._log_repository.get_record(record_id)
        if record is None:
            return False
        recorder_mmap_path = str(record.get_data_mmap_path())

        wakeup = self._dispatcher.create_worker_wakeup()
        self._dispatcher.register_decoder_worker(
            file_path="recorder",
            wakeup=wakeup,
            callback=lambda: self.on_recorder_callback(),
        )

        self._recorder_last_status = None
        self._active_recording_record_id = record_id
        self._active_recording_mmap_path = recorder_mmap_path
        self._recorder_stop.clear()
        self._recorder_proc = self._spawn_process(
            "recorder",
            writer_process,
            args=(
                str(CAN_SHARED_RING_SHM_NAME),
                recorder_mmap_path,
                self._recorder_stop,
                wakeup,
                self._recorder_state,
            ),
        )
        try:
            data_base = Path(recorder_mmap_path).with_suffix("")
            first_segment = data_base.parent / f"{data_base.name}.000.mmap"
            first_segment.parent.mkdir(parents=True, exist_ok=True)
            first_segment.touch(exist_ok=True)
        except Exception:
            LOG.debug("Failed to prime recorder data mmap placeholder", exc_info=True)
        if self._recorder_proc is not None:
            self.on_recorder_callback()
        return True

    def stop_recording(self) -> None:
        self._recorder_stop.set()
        if self._recorder_proc is not None:
            self._recorder_proc.join(timeout=1.0)
            alive = bool(self._recorder_proc.is_alive())
            self._worker_alive["recorder"] = alive
            if not alive:
                self._recorder_proc = None

    def save_record(self, record_id: RecordId) -> bool:
        return self._log_repository.save_record(record_id)

    def create_record(self) -> RecordId:
        self._require_running()
        return self._log_repository.create_record()

    def create_record_from_mmap(self) -> RecordId:
        self._require_running()
        return self._log_repository.create_record()

    def attach_runtime_storage(
        self,
        record_id: RecordId,
        data_mmap_path: str,
        _runtime_mmap_path: str,
        file_path: str | None = None,
    ) -> bool:
        _ = (record_id, data_mmap_path, _runtime_mmap_path, file_path)
        LOG.warning("attach_runtime_storage is deprecated: RecordRepository owns mmap path allocation")
        return False

    def is_supported_log_file(self, file_path: str) -> bool:
        return self._log_verification.is_supported_log_file(file_path)

    def parse_log_file(self, file_path: str, record_id: RecordId | None = None) -> bool:
        self._require_running()
        normalized = str(file_path)
        if self._log_verification.verify_log_file(normalized):
            return self._start_parse_job(normalized, record_id)
        return False

    def parse_line(self, text_l: str) -> ParsedEntry:
        parsed = fs_core_parse_line(str(text_l or ""), 1)
        if parsed is None:
            raise ValueError("Failed to parse CAN log line")
        return parsed

    def parse_lines(self, text_l: str) -> list[ParsedEntry]:
        parsed_lines: list[ParsedEntry] = []
        for line_number, line in enumerate(str(text_l or "").splitlines(), start=1):
            if not line.strip():
                continue
            parsed = fs_core_parse_line(line, line_number)
            if parsed is not None:
                parsed_lines.append(parsed)
        return parsed_lines

    def parse_dbc_file(self, db_file_path: str, record_id: RecordId | None = None) -> bool:
        self._require_running()

        normalized = str(db_file_path)
        if not isfile(normalized):
            LOG.info("DBC file path is invalid: %s", normalized)
            return False

        record = self._log_repository.get_record(record_id) if record_id is not None else None
        if record is None:
            record_id = self.create_record()
            record = self._log_repository.get_record(record_id)
            if record is None:
                LOG.error("Failed to create/resolve record for parse_dbc_file")
                return False

        pkl_path = record.get_dbc_pkl_path()

        self._pending_dbc_record_ids[normalized] = record_id

        worker = threading.Thread(
            target=self._dbc_manager.load_database,
            args=(normalized, pkl_path),
            daemon=True,
            name="FileService-dbc-loader",
        )
        worker.start()
        worker.join()

        loaded = self._dbc_manager.candb_dict.get(normalized)
        return loaded is not None

    def _on_dbc_manager_loaded(self, candb_info: CANDBInfo) -> None:
        db_file_path = str(getattr(candb_info, "file_path", "") or "")
        record_id = self._pending_dbc_record_ids.pop(db_file_path, None)
        self._dispatcher.dispatch_event(
            DBCLoadedEvent(
                record_id=record_id,
                db_file_path=db_file_path,
                candb_info=candb_info,
            )
        )

    def get_record(self, record_id: RecordId) -> Record | None:
        return self._log_repository.get_record(record_id)

    def get_log_record_id(self, file_path: str) -> RecordId | None:
        return self._log_repository.get_record_id(file_path)

    def list_log_records(self) -> list[RecordId]:
        return self._log_repository.list_record_ids()

    def export_log_csv(self, record_id: RecordId, lines: list[Any], save_filepath: str | None = None) -> str | None:
        return self._log_export.write_log_csv(record_id, lines, save_filepath)

    def _start_parse_job(self, file_path: str, record_id: RecordId | None = None) -> bool:
        wakeup = self._dispatcher.create_worker_wakeup()

        if record_id is None:
            record_id = self._log_repository.create_record()
        elif self._log_repository.get_record(record_id) is None:
            LOG.error("Record id not found for parse job: %s", record_id)
            return False

        self._active_parse_record_id = record_id

        self._dispatcher.register_parser_worker(
            file_path=file_path,
            wakeup=wakeup,
            callback=lambda: self.on_parser_callback(),
        )

        record = self._log_repository.get_record(record_id)
        if record is None:
            LOG.error("Failed to resolve record for id: %s", record_id)
            self._log_repository.mark_failed(record_id)
            self._active_parse_record_id = None
            return False

        proc = run_parser_async(
            file_path,
            str(record.get_base_path()),
            wakeup,
            self._parser_state,
        )
        self._worker_alive["parser"] = bool(proc.is_alive())
        return self._worker_alive["parser"]

    def _stop_parser_worker(self) -> None:
        active_record = self._active_parse_record_id
        if active_record is not None:
            self._log_repository.mark_failed(active_record)
            self._active_parse_record_id = None

    def _start_decode_job(
        self,
        record_id: RecordId,
        record_path_token: Path,
        db_file_path: str,
        dbc_pkl_path: str,
    ) -> bool:
        wakeup = self._dispatcher.create_worker_wakeup()
        self._active_decode_record_id = record_id
        self._active_decode_db_file_path = db_file_path
        self._active_decode_file_path = str(record_path_token)
        proc = run_decode_async(record_path_token, db_file_path, wakeup, dbc_pkl_path, self._decoder_state)


        self._dispatcher.register_decoder_worker(
            file_path=self._active_decode_file_path,
            wakeup=wakeup,
            callback=lambda: self.on_decode_callback(),
        )
        self._worker_alive["decoder"] = bool(proc.is_alive())
        return self._worker_alive["decoder"]

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

    def on_recorder_callback(self) -> bool:
        recorder_mmap_path = self._active_recording_mmap_path
        recorder_state = RecorderStatus(int(self._recorder_state.value))
        alive = bool(self._recorder_proc is not None and self._recorder_proc.is_alive())

        status = int(recorder_state)
        if status != self._recorder_last_status:
            self._recorder_last_status = status
            self._dispatcher.dispatch_event(
                RecorderStatusEvent(
                    status=recorder_state,
                    payload={
                        "status": int(status),
                        "record_id": self._active_recording_record_id,
                        "mmap_path": recorder_mmap_path,
                    },
                )
            )

        if not alive:
            self._worker_alive["recorder"] = False
            self._recorder_proc = None
            self._active_recording_record_id = None
            self._active_recording_mmap_path = None
            return True
        return False

    def on_parser_callback(self) -> int:
        record_id = self._active_parse_record_id
        record = self._log_repository.get_record(record_id) if record_id is not None else None
        worker_state = ParserStatus(int(self._parser_state.value))
        status = int(worker_state)
        if worker_state == ParserStatus.DONE and record_id is not None:
            LOG.info("Parser finished with DONE for record_id=%s", record_id)

        if worker_state == ParserStatus.FAILED and record_id is not None:
            self._log_repository.mark_failed(record_id)
            LOG.error("Parser finished with FAILED for record_id=%s", record_id)

        if worker_state in (ParserStatus.DONE, ParserStatus.FAILED):
            self._active_parse_record_id = None
        message = "parser worker completed" if worker_state == ParserStatus.DONE else "parser worker failed" if worker_state == ParserStatus.FAILED else "parser worker status"
        self._dispatcher.dispatch_event(
            ParserStatusEvent(
                record_id=record_id,
                status=worker_state,
                kind="status",
                payload={
                    "record_id": record_id,
                    "status": status,
                    "message": message,
                },
            )
        )
        return status

    def on_decode_callback(self) -> bool:
        record_id = self._active_decode_record_id
        file_path = self._active_decode_file_path
        db_file_path = self._active_decode_db_file_path

        assert record_id is not None
        assert file_path is not None
        assert db_file_path is not None

        def _reset_decode_state() -> None:
            self._active_decode_record_id = None
            self._active_decode_file_path = None
            self._active_decode_db_file_path = None
            self._worker_alive["decoder"] = False


        worker_state = DecodeStatus(int(self._decoder_state.value))
        if worker_state in (DecodeStatus.IDLE, DecodeStatus.RUNNING):
            self._dispatcher.dispatch_event(
                DecodeStatusEvent(
                    record_id=record_id,
                    status=DecodeStatus.RUNNING,
                    kind="status",
                    payload={
                        "record_id": record_id,
                        "status": int(DecodeStatus.RUNNING),
                        "file_path": file_path,
                        "db_file_path": db_file_path,
                        "worker_state": int(self._decoder_state.value),
                    },
                )
            )
            return False

        if worker_state == DecodeStatus.FAILED:
            self._log_repository.mark_failed(record_id)
            self._dispatcher.dispatch_event(
                DecodeStatusEvent(
                    record_id=record_id,
                    status=DecodeStatus.FAILED,
                    kind="status",
                    payload={
                        "record_id": record_id,
                        "status": int(DecodeStatus.FAILED),
                        "file_path": file_path,
                        "db_file_path": db_file_path,
                        "reason": "decode_worker_failed",
                        "worker_state": int(self._decoder_state.value),
                    },
                )
            )
            self._dispatcher.dispatch_event(
                DecoderStatusEvent(
                    kind="error",
                    payload={
                        "record_id": record_id,
                        "file_path": file_path,
                        "db_file_path": db_file_path,
                        "reason": "decode_worker_failed",
                        "worker_state": int(self._decoder_state.value),
                    },
                )
            )
            _reset_decode_state()
            return True

        if worker_state != DecodeStatus.DONE:
            self._dispatcher.dispatch_event(
                DecodeStatusEvent(
                    record_id=record_id,
                    status=DecodeStatus.FAILED,
                    kind="status",
                    payload={
                        "record_id": record_id,
                        "status": int(DecodeStatus.FAILED),
                        "file_path": file_path,
                        "db_file_path": db_file_path,
                        "reason": "decode_worker_unknown_state",
                        "worker_state": int(self._decoder_state.value),
                    },
                )
            )
            _reset_decode_state()
            return True

        self._dispatcher.dispatch_event(
            DecodeStatusEvent(
                record_id=record_id,
                status=DecodeStatus.DONE,
                kind="status",
                payload={
                    "record_id": record_id,
                    "status": int(DecodeStatus.DONE),
                    "file_path": file_path,
                    "db_file_path": db_file_path,
                    "worker_state": int(self._decoder_state.value),
                },
            )
        )
        self._dispatcher.dispatch_event(
            DecoderStatusEvent(
                kind="completed",
                payload={
                    "record_id": record_id,
                    "file_path": file_path,
                    "db_file_path": db_file_path,
                },
            )
        )

        _reset_decode_state()
        return True
