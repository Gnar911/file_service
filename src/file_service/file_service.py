from __future__ import annotations

import multiprocessing as mp
import struct
from os.path import isfile
from pathlib import Path
from typing import Any

from file_service.dispatcher.application_events import (
    DecodeCompletedEvent,
    DecodeStartedEvent,
    DecoderStatusEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
)
from file_service.decode.decode_process import run_decode_async
from file_service.dispatcher.event_dispatcher import FileServiceDispatcher
from file_service.exporter.can_log_export import CANLogExport
from file_service.parser.parser_process import run_parser_async
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
    DATA_STATUS_RUNNING,
)


class FileService(BaseService):
    def __init__(self):
        super().__init__(service_name="FileService")

        self._recorder_stop = mp.Event()
        self._worker_alive: dict[str, bool] = {}
        self._active_parse_record_id: RecordId | None = None
        self._active_decode_file_path: str | None = None
        self._active_decode_db_file_path: str | None = None

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

    def decode(self, record_id: RecordId, db_file_path: str) -> bool:
        self._require_running()

        normalized_db_file_path = str(db_file_path)
        if not normalized_db_file_path:
            return False

        record = self._log_repository.get_record(record_id)
        if record is None:
            return False
        
        if not record.has_runtime_mmaps():
            return False

        mmap_name = str(record.raw.mmap_name)
        if not mmap_name:
            return False

        record_mmap_path = self._log_repository.get_mmap_path(record_id)
        return self._start_decode_job(record_mmap_path, normalized_db_file_path)

        
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

    def parse_log_file(self, file_path: str) -> bool:
        self._require_running()

        normalized = str(file_path)
        if not isfile(normalized):
            LOG.info("Input file path is invalid: %s", normalized)
            return False

        verified_file_path = self._log_verification.verify_log_file(normalized)
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
        wakeup = self._dispatcher.create_worker_wakeup()

        storage_path = self._log_repository.generate_mmap_path()
        record_id = self._log_repository.get_record_id_by_mmap_path(storage_path)
        if record_id is None:
            LOG.error("Failed to resolve record id for generated mmap path: %s", storage_path)
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
            str(record.raw.data_mmap_path),
            str(record.raw.index_mmap_path),
            wakeup,
        )
        self._worker_alive["parser"] = bool(proc.is_alive())
        return self._worker_alive["parser"]

    def _stop_parser_worker(self) -> None:
        active_record = self._active_parse_record_id
        if active_record is not None:
            self._log_repository.mark_failed(active_record)
            self._active_parse_record_id = None

    def _start_decode_job(self, record_mmap_path: Path, db_file_path: str) -> bool:
        wakeup = self._dispatcher.create_worker_wakeup()
        self._active_decode_db_file_path = db_file_path
        self._active_decode_file_path = str(record_mmap_path)

        self._dispatcher.dispatch_event(
            DecodeStartedEvent(
                file_path=self._active_decode_file_path,
                db_file_path=self._active_decode_db_file_path,
                expected_samples=0,
            )
        )
        self._dispatcher.register_decoder_worker(
            file_path=self._active_decode_file_path,
            wakeup=wakeup,
            callback=lambda: self.on_decode_callback(),
        )

        proc = run_decode_async(record_mmap_path, db_file_path, wakeup)
        self._worker_alive["decoder"] = bool(proc.is_alive())
        return self._worker_alive["decoder"]

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
        status = DATA_STATUS_ERROR
        record = self._log_repository.get_record(record_id) if record_id is not None else None

        if record is not None:
            record.refresh_runtime()
            data_segments = list(record.raw.data_segment_paths())
            try:
                if not data_segments:
                    status = DATA_STATUS_ERROR
                else:
                    with open(str(data_segments[0]), "rb") as mmap_file:
                        header = mmap_file.read(16)
                    status = int(struct.unpack_from("<I", header, 12)[0]) if len(header) >= 16 else DATA_STATUS_ERROR
            except Exception as error:
                LOG.error("Failed to read parser mmap status for %s: %s", record_id, error)
                status = DATA_STATUS_ERROR

        if status == DATA_STATUS_DONE and record_id is not None:
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

    def on_decode_callback(self) -> bool:
        file_path = self._active_decode_file_path
        db_file_path = self._active_decode_db_file_path
        if not file_path or not db_file_path:
            self._worker_alive["decoder"] = False
            return False

        self._dispatcher.dispatch_event(
            DecodeCompletedEvent(
                file_path=file_path,
                db_file_path=db_file_path,
            )
        )
        self._dispatcher.dispatch_event(
            DecoderStatusEvent(
                kind="completed",
                payload={
                    "file_path": file_path,
                    "db_file_path": db_file_path,
                },
            )
        )

        self._active_decode_file_path = None
        self._active_decode_db_file_path = None
        self._worker_alive["decoder"] = False
        return True
