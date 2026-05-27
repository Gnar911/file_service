from __future__ import annotations

import multiprocessing as mp
from os.path import isfile
from typing import Any, Iterable

from file_service.dispatcher.application_events import FileServiceStateEvent, ParserStatusEvent
from file_service.dispatcher.event_dispatcher import FileServiceDispatcher
from file_service.exporter.can_log_export import CANLogExport
from file_service.parser.native.native_parser import NativeParser
from file_service.parser.parser_process import run_parser_proc
from file_service.repository.record import Record
from file_service.record_id import RecordId
from file_service.recorder.mmap_recorder import writer_process
from file_service.repository.record_repository import RecordRepository
from file_service.verification.can_log_verification import CANLogVerification
from lw.base_service import BaseService, ServiceState
from lw.define import CAN_SHARED_RING_SHM_NAME
from lw.logger_setup import LOG
from native_sdk.can_parser_api import (  # type: ignore[import-not-found]
    DATA_STATUS_DONE,
    DATA_STATUS_ERROR,
)


class FileService(BaseService):
    def __init__(self):
        super().__init__(service_name="FileService")

        self.decode_status_queue = mp.Queue()
        self._recorder_stop = mp.Event()
        self._worker_alive: dict[str, bool] = {}
        self._active_parse_record_id: RecordId | None = None

        self._log_repository = RecordRepository()
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

    def request_decode_jobs(self, decode_jobs: Iterable[tuple[str, str]]) -> None:
        self._require_running()
        jobs = [(str(db_file_path), str(file_path)) for db_file_path, file_path in list(decode_jobs)]
        if not jobs:
            return
        self._start_decode_job(jobs)

    def decode(self, record_id: RecordId, db_file_path: str) -> RecordId | None:
        self._require_running()

        normalized_db_file_path = str(db_file_path)
        if not normalized_db_file_path:
            return None

        record = self._log_repository.get_record(record_id)
        if record is None:
            return None
        if not record.has_runtime_mmaps():
            return None

        self.request_decode_jobs([(normalized_db_file_path, record.file_path)])
        return record_id

    def request_parse_job(self, file_path: str) -> bool:
        self._require_running()
        return self.parse_log_file(file_path)

    def request_stop_parse_log_async(self, _file_path: str) -> None:
        LOG.info("Stop parse request received")

    def start_recording(self) -> bool:
        self._require_running()
        self._recorder_stop.clear()
        self._spawn_process(
            "recorder",
            writer_process,
            args=(
                str(CAN_SHARED_RING_SHM_NAME),
                self._recorder_stop,
            ),
        )
        return True

    def stop_recording(self) -> None:
        self._recorder_stop.set()

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
        index_mmap_path: str,
        file_path: str | None = None,
    ) -> bool:
        _ = (record_id, data_mmap_path, index_mmap_path, file_path)
        LOG.warning("attach_runtime_storage is deprecated: RecordRepository owns mmap path allocation")
        return False

    def is_supported_log_file(self, file_path: str) -> bool:
        return self._log_verification.is_supported_log_file(file_path)

    def verify_log_file(self, file_path: str) -> str | None:
        normalized_file_path = str(file_path)
        if not self._log_verification.verify_log_file(normalized_file_path):
            return None
        return normalized_file_path

    def parse_log_file(self, file_path: str) -> bool:
        self._require_running()
        verified_file_path = self.verify_log_file(file_path)
        if verified_file_path is None:
            return False
        if not isfile(verified_file_path):
            return False
        return self._start_parse_job(verified_file_path)

    def get_record(self, record_id: RecordId) -> Record | None:
        return self._log_repository.get_record(record_id)

    def get_log_record_id(self, file_path: str) -> RecordId | None:
        return self._log_repository.get_record_id(file_path)

    def list_log_records(self) -> list[RecordId]:
        return self._log_repository.list_record_ids()

    def export_log_csv(self, record_id: RecordId, lines: list[Any], save_filepath: str | None = None) -> str | None:
        return self._log_export.write_log_csv(record_id, lines, save_filepath)

    def _start_parse_job(self, file_path: str) -> bool:
        normalized = str(file_path)
        if not isfile(normalized):
            LOG.info("Input file path is invalid: %s", normalized)
            return False

        self.call_parse_worker(normalized)
        return True

    def call_parse_worker(self, file_path: str) -> None:
        wakeup = self._dispatcher.create_worker_wakeup()

        storage_path = self._log_repository.generate_mmap_path()
        record_id = self._log_repository.get_record_id_by_mmap_path(storage_path)
        if record_id is None:
            LOG.error("Failed to resolve record id for generated mmap path: %s", storage_path)
            return

        self._active_parse_record_id = record_id

        self._dispatcher.register_parser_worker(
            file_path=file_path,
            wakeup=wakeup,
            callback=lambda: self.on_parser_callback(),
        )

        proc = run_parser_proc(file_path, str(storage_path), wakeup)
        self._worker_alive["parser"] = bool(proc.is_alive())

    def _stop_parser_worker(self) -> None:
        active_record = self._active_parse_record_id
        if active_record is not None:
            self._log_repository.mark_failed(active_record)
            self._active_parse_record_id = None

    def _start_decode_job(self, decode_jobs: list[tuple[str, str]]) -> None:
        try:
            from file_service.decode.decoder_manager import decode_process
        except Exception as error:
            LOG.error("Decode worker unavailable: %s", error)
            return

        self._spawn_process(
            "decoder",
            decode_process,
            args=(self.decode_status_queue, decode_jobs),
        )

    def _spawn_process(self, name: str, target, args: tuple[Any, ...], kwargs: dict[str, Any] | None = None) -> None:
        proc = mp.Process(
            target=target,
            args=args,
            kwargs=dict(kwargs or {}),
            daemon=True,
            name=f"FileService-{name}",
        )
        proc.start()
        self._worker_alive[name] = bool(proc.is_alive())

    def on_parser_callback(self) -> int:
        record_id = self._active_parse_record_id
        status = int(NativeParser.get_status())

        if status == DATA_STATUS_DONE and record_id is not None:
            record = self._log_repository.get_record(record_id)
            if record is not None:
                record.refresh_runtime()

        if status == DATA_STATUS_ERROR and record_id is not None:
            self._log_repository.mark_failed(record_id)

        if status in (DATA_STATUS_DONE, DATA_STATUS_ERROR):
            self._active_parse_record_id = None

        self._dispatcher.dispatch_event(
            ParserStatusEvent(
                record_id=record_id,
                status=status,
                kind="status",
                payload={
                    "record_id": record_id,
                    "status": status,
                },
            )
        )
        return status
