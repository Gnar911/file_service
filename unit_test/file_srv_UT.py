
from __future__ import annotations

import threading
import time
from pathlib import Path

from file_service.dispatcher.application_events import DecodeCompletedEvent, FileServiceStateEvent, ParserStatusEvent
from file_service.repository.record import Record
from file_service.record_id import RecordId
from file_service.srv_if import FileService, get_file_service
from lw.base_service import ServiceState
from file_service.define import MMAP_LOCAL_STORAGE_DIR
from native_sdk.can_parser_api import DATA_STATUS_DONE
import pytest
from PySide6.QtCore import (
    QCoreApplication,
)

TIMEOUT = 0.8
PARSE_TIMEOUT = 15.0
POLL_INTERVAL = 0.1

def _start_service() -> FileService:
    running_event = threading.Event()
    file_srv = get_file_service()

    if file_srv.get_service_state() == ServiceState.RUNNING:
        return file_srv

    assert file_srv.get_service_state() == ServiceState.STOPPED

    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.RUNNING and running_event.set(),
    )

    file_srv.start()
    assert running_event.wait(timeout=TIMEOUT)
    return file_srv

def test_01_start_service() -> FileService:
    """Start FileService and wait until it publishes RUNNING."""
    return _start_service()


def test_02_stop_service() -> None:
    """Stop FileService and wait until it publishes STOPPED."""
    file_srv = _start_service()
    stop_event = threading.Event()

    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.STOPPED and stop_event.set(),
    )

    file_srv.stop()
    assert stop_event.wait(timeout=TIMEOUT)


@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x10.asc",
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x100.asc",
    ],
)

def test_03_parse_log_file(file_path: str) -> RecordId:
    parse_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
        
    file_srv = test_01_start_service()
    normalized_file_path = str(file_path)
    parsed_record_id: RecordId | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == DATA_STATUS_DONE and event.record_id is not None:
            parsed_record_id = event.record_id
            parse_event.set()

    file_srv.subscribe(
        ParserStatusEvent,
        _on_parser_status,
    )

    file_srv.parse_log_file(normalized_file_path)

    deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    record_id = parsed_record_id
    assert record_id in file_srv.list_log_records()

    record = file_srv.get_record(record_id)
    assert record is not None
    assert record.record_id == record_id
    assert record.has_runtime_mmaps()
    print("runtime_mmap_paths:", record.raw.data_segment_paths(), record.raw.index_segment_paths())

    return record_id


@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x10.asc",
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x100.asc",
    ],
)
def test_04_save_record(file_path: str) -> None:
    file_srv = _start_service()
    record_id = test_03_parse_log_file(file_path)

    saved_count = file_srv.save_record(record_id)
    assert saved_count > 0

    record = file_srv.get_record(record_id)
    assert record is not None
    assert record.record_id == record_id

    target_dir = MMAP_LOCAL_STORAGE_DIR / record_id.path_token()
    assert target_dir.exists()
    assert record.get_mmap_path().parent == target_dir

    runtime_paths = record.get_runtime_mmap_paths()
    assert runtime_paths["data"]
    assert runtime_paths["index"]
    assert runtime_paths["channel_index"]
    assert runtime_paths["direction_index"]
    assert all(path.parent == target_dir for path in runtime_paths["data"])
    assert all(path.parent == target_dir for path in runtime_paths["index"])
    assert all(path.parent == target_dir for path in runtime_paths["channel_index"])
    assert all(path.parent == target_dir for path in runtime_paths["direction_index"])

    # initial_metadata = record.get_metadata()
    # assert initial_metadata["record_id"] == record_id
    # assert initial_metadata["file_path"] == normalized_file_path
    # assert initial_metadata["decoded_db_file_paths"] == []
    # assert initial_metadata["raw"] is record.raw
    # assert "decoded" not in initial_metadata
    # assert record.record_id == record_id
    # assert record.file_path == normalized_file_path
    # assert record.is_loading is False
    # assert record.raw.state == DataLogState.AVAILABLE
    # assert record.raw.is_loading is False
    # assert int(record.raw.total_lines) > 0
    # assert int(record.raw.verified_size) == int(record.raw.total_lines)
    # assert int(record.raw.mmap_file_count) > 0
    # assert len(record.raw.can_ids) > 0

    # metadata = record.get_metadata()
    # assert metadata["record_id"] == record_id
    # assert metadata["file_path"] == normalized_file_path
    # assert metadata["raw"] is record.raw
    # assert metadata["raw_state"] == DataLogState.AVAILABLE
    # assert metadata["raw_is_loading"] is False
    # assert metadata["is_loading"] is False
    # assert metadata["total_lines"] == int(record.raw.total_lines)
    # assert metadata["verified_size"] == int(record.raw.verified_size)
    # assert metadata["verified_size"] == metadata["total_lines"]
    # assert metadata["mmap_file_count"] == int(record.raw.mmap_file_count)
    # assert metadata["mmap_file_count"] > 0
    # assert metadata["decoded_db_file_paths"] == []
    # assert "decoded" not in metadata