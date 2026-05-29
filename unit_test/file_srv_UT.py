from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from file_service.define import MMAP_LOCAL_STORAGE_DIR
from file_service.dispatcher.application_events import (
    DBCLoadedEvent,
    DecodeCompletedEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
)
from file_service.record_id import RecordId
from file_service.srv_if import FileService, get_file_service
from lw.base_service import ServiceState
from native_sdk.can_parser_api import DATA_STATUS_DONE

TIMEOUT = 0.8
PARSE_TIMEOUT = 15.0
POLL_INTERVAL = 0.1


@pytest.mark.dependency(name="service_started")
def test_01_start_service() -> None:
    """Start FileService and assert STOPPED -> RUNNING transition."""
    running_event = threading.Event()
    file_srv = get_file_service()

    assert file_srv.get_service_state() == ServiceState.STOPPED

    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.RUNNING and running_event.set(),
    )

    file_srv.start()
    assert running_event.wait(timeout=TIMEOUT)
    assert file_srv.get_service_state() == ServiceState.RUNNING

@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x10.asc",
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x100.asc",
    ],
)
@pytest.mark.dependency(depends=["service_started"])
def test_04_save_record(file_path: str) -> None:
    parse_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    parsed_record_id: RecordId | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == DATA_STATUS_DONE and event.record_id is not None:
            parsed_record_id = event.record_id
            parse_event.set()

    file_srv.subscribe(ParserStatusEvent, _on_parser_status)

    started = file_srv.parse_log_file(str(file_path))
    assert started

    deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    record_id = parsed_record_id

    saved_count = file_srv.save_record(record_id)
    assert saved_count > 0

    record = file_srv.get_record(record_id)
    assert record is not None
    assert record.record_id == record_id

    target_dir = MMAP_LOCAL_STORAGE_DIR / record_id.path_token()
    assert target_dir.exists()

    runtime_paths = record.get_runtime_mmap_paths()
    assert runtime_paths["data"]
    assert runtime_paths["index"]
    assert runtime_paths["channel_index"]
    assert runtime_paths["direction_index"]
    assert all(path.parent == target_dir for path in runtime_paths["data"])
    assert all(path.parent == target_dir for path in runtime_paths["index"])
    assert all(path.parent == target_dir for path in runtime_paths["channel_index"])
    assert all(path.parent == target_dir for path in runtime_paths["direction_index"])


@pytest.mark.dependency(name="load_record", depends=["service_started"])
@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
    ],
)
def test_05_parse_log_with_record_id(file_path: str) -> None:
    parse_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    record_id = file_srv.create_record()
    record_count_before = len(file_srv.list_log_records())
    parsed_record_id: RecordId | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == DATA_STATUS_DONE and event.record_id is not None:
            parsed_record_id = event.record_id
            parse_event.set()

    file_srv.subscribe(ParserStatusEvent, _on_parser_status)

    started = file_srv.parse_log(file_path, record_id)
    assert started

    deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    assert parsed_record_id == record_id
    assert len(file_srv.list_log_records()) == record_count_before
    record = file_srv.get_record(record_id)
    assert record is not None
    print("runtime_mmap_paths:", record.raw.data_segment_paths(), record.raw.index_segment_paths())


@pytest.mark.dependency(depends=["service_started"])
@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
    ],
)
def test_06_parse_log_without_record_id(file_path: str) -> None:
    parse_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    record_ids_before = set(file_srv.list_log_records())
    parsed_record_id: RecordId | None = None
    done_event_count = 0

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        nonlocal done_event_count
        if (
            event.status == DATA_STATUS_DONE
            and event.record_id is not None
            and event.record_id not in record_ids_before
        ):
            parsed_record_id = event.record_id
            done_event_count += 1
            parse_event.set()

    file_srv.subscribe(ParserStatusEvent, _on_parser_status)

    started = file_srv.parse_log(file_path)
    assert started

    deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    assert parsed_record_id in file_srv.list_log_records()
    assert done_event_count == 1
    assert len(file_srv.list_log_records()) == len(record_ids_before) + 1
    record = file_srv.get_record(parsed_record_id)
    assert record is not None
    print("runtime_mmap_paths:", record.raw.data_segment_paths(), record.raw.index_segment_paths())


@pytest.mark.parametrize(
    "db_file_path",
    [
        "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
        "EEA10_CANFD_R00c_withADAS_Main.dbc",
    ],
)
@pytest.mark.dependency(name="load_dbc", depends=["service_started"])
def test_07_parse_dbc_without_record(db_file_path: str) -> None:
    callback_event = threading.Event()
    callback_data: DBCLoadedEvent | None = None
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    def _on_dbc_loaded(event: DBCLoadedEvent) -> None:
        nonlocal callback_data
        callback_data = event
        if event.db_file_path == db_file_path and event.record_id is not None:
            callback_event.set()

    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)

    parsed = file_srv.parse_dbc(db_file_path)
    assert parsed

    deadline = time.monotonic() + PARSE_TIMEOUT
    while not callback_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        callback_event.wait(timeout=POLL_INTERVAL)

    assert callback_event.is_set()
    assert callback_data is not None
    assert callback_data.record_id is not None
    assert callback_data.db_file_path == db_file_path
    assert callback_data.candb_info is not None
    assert callback_data.candb_info.file_path == db_file_path
    assert callback_data.candb_info.db is not None

    record = file_srv.get_record(callback_data.record_id)
    assert record is not None
    pkl_path = record.get_dbc_pkl_path()
    print("dbc_pkl_path(no-record):", pkl_path)
    assert pkl_path.exists()

@pytest.mark.parametrize(
    "db_file_path",
    [
        "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
        "EEA10_CANFD_R00c_withADAS_Main.dbc",
    ],
)
@pytest.mark.dependency(depends=["service_started"])
def test_08_parse_dbc_for_record(db_file_path: str) -> None:
    callback_event = threading.Event()
    callback_data: DBCLoadedEvent | None = None
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    record_id = file_srv.create_record()

    def _on_dbc_loaded(event: DBCLoadedEvent) -> None:
        nonlocal callback_data
        callback_data = event
        if event.record_id == record_id and event.db_file_path == db_file_path:
            callback_event.set()

    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)

    parsed = file_srv.parse_dbc(db_file_path, record_id)
    assert parsed

    deadline = time.monotonic() + PARSE_TIMEOUT
    while not callback_event.is_set() and time.monotonic() < deadline:
        app.processEvents()
        callback_event.wait(timeout=POLL_INTERVAL)

    assert callback_event.is_set()
    assert callback_data is not None
    assert callback_data.record_id == record_id
    assert callback_data.db_file_path == db_file_path
    assert callback_data.candb_info is not None
    assert callback_data.candb_info.file_path == db_file_path
    assert callback_data.candb_info.db is not None

    record = file_srv.get_record(record_id)
    assert record is not None
    pkl_path = record.get_dbc_pkl_path()
    print("dbc_pkl_path:", pkl_path)
    assert pkl_path.exists()


@pytest.mark.parametrize(
    "file_path, db_file_path",
    [
        (
            "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
            "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
            "EEA10_CANFD_R00c_withADAS_Main.dbc",
        ),
    ],
)
@pytest.mark.dependency(depends=["service_started"])
def test_09_parse_log_then_dbc_same_record(file_path: str, db_file_path: str) -> None:
    """parse_log (no pre-created record) → use returned record_id → parse_dbc for that record."""
    parse_event = threading.Event()
    dbc_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    parsed_record_id: RecordId | None = None
    dbc_callback_data: DBCLoadedEvent | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == DATA_STATUS_DONE and event.record_id is not None:
            parsed_record_id = event.record_id
            parse_event.set()

    def _on_dbc_loaded(event: DBCLoadedEvent) -> None:
        nonlocal dbc_callback_data
        if parsed_record_id is not None and event.record_id == parsed_record_id:
            dbc_callback_data = event
            dbc_event.set()

    file_srv.subscribe(ParserStatusEvent, _on_parser_status)
    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)

    # Step 1: parse log without pre-created record — record_id comes from DONE event
    started = file_srv.parse_log(file_path)
    assert started

    parse_deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < parse_deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None

    # Step 2: parse dbc for that same record
    dbc_parsed = file_srv.parse_dbc(db_file_path, parsed_record_id)
    assert dbc_parsed

    dbc_deadline = time.monotonic() + PARSE_TIMEOUT
    while not dbc_event.is_set() and time.monotonic() < dbc_deadline:
        app.processEvents()
        dbc_event.wait(timeout=POLL_INTERVAL)

    assert dbc_event.is_set()
    assert dbc_callback_data is not None
    assert dbc_callback_data.record_id == parsed_record_id
    assert dbc_callback_data.candb_info is not None

    record = file_srv.get_record(parsed_record_id)
    assert record is not None
    pkl_path = record.get_dbc_pkl_path()
    print("test_09 record_id:", parsed_record_id)
    print("test_09 dbc_pkl_path:", pkl_path)
    assert pkl_path.exists()


@pytest.mark.parametrize(
    "file_path, db_file_path",
    [
        (
            "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
            "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
            "EEA10_CANFD_R00c_withADAS_Main.dbc",
        ),
    ],
)
@pytest.mark.dependency(depends=["service_started"])
def test_10_parse_dbc_then_log_same_record(file_path: str, db_file_path: str) -> None:
    """parse_dbc (no pre-created record) → use returned record_id → parse_log for that record."""
    parse_event = threading.Event()
    dbc_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    dbc_record_id: RecordId | None = None
    parsed_record_id: RecordId | None = None

    def _on_dbc_loaded(event: DBCLoadedEvent) -> None:
        nonlocal dbc_record_id
        if event.db_file_path == db_file_path and event.record_id is not None:
            dbc_record_id = event.record_id
            dbc_event.set()

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if (
            event.status == DATA_STATUS_DONE
            and event.record_id is not None
            and event.record_id == dbc_record_id
        ):
            parsed_record_id = event.record_id
            parse_event.set()

    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)
    file_srv.subscribe(ParserStatusEvent, _on_parser_status)

    # Step 1: parse dbc without pre-created record — record_id comes from DBCLoadedEvent
    dbc_parsed = file_srv.parse_dbc(db_file_path)
    assert dbc_parsed

    dbc_deadline = time.monotonic() + PARSE_TIMEOUT
    while not dbc_event.is_set() and time.monotonic() < dbc_deadline:
        app.processEvents()
        dbc_event.wait(timeout=POLL_INTERVAL)

    assert dbc_event.is_set()
    assert dbc_record_id is not None

    # Step 2: parse log with that same record
    started = file_srv.parse_log(file_path, dbc_record_id)
    assert started

    parse_deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < parse_deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    assert parsed_record_id == dbc_record_id

    record = file_srv.get_record(dbc_record_id)
    assert record is not None
    pkl_path = record.get_dbc_pkl_path()
    print("test_10 record_id:", dbc_record_id)
    print("test_10 runtime_mmap_paths:", record.raw.data_segment_paths(), record.raw.index_segment_paths())
    print("test_10 dbc_pkl_path:", pkl_path)
    assert pkl_path.exists()
    assert record.has_runtime_mmaps()


@pytest.mark.dependency(name="decode", depends=["service_started"])
@pytest.mark.parametrize(
    "file_path, db_file_path",
    [
        (
            "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
            "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
            "EEA10_CANFD_R00c_withADAS_Main.dbc",
        ),
    ],
)
def test_15_decode_parse_then_dbc(file_path: str, db_file_path: str) -> None:
    parse_event = threading.Event()
    dbc_event = threading.Event()
    decode_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    parsed_record_id: RecordId | None = None
    dbc_loaded_record_id: RecordId | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == DATA_STATUS_DONE and event.record_id is not None:
            parsed_record_id = event.record_id
            parse_event.set()

    def _on_dbc_loaded(event: DBCLoadedEvent) -> None:
        nonlocal dbc_loaded_record_id
        if (
            parsed_record_id is not None
            and event.record_id == parsed_record_id
            and event.db_file_path == db_file_path
        ):
            dbc_loaded_record_id = event.record_id
            dbc_event.set()

    def _on_decode_complete(event: DecodeCompletedEvent) -> None:
        if parsed_record_id is not None and event.record_id == parsed_record_id:
            decode_event.set()

    file_srv.subscribe(ParserStatusEvent, _on_parser_status)
    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)
    file_srv.subscribe(DecodeCompletedEvent, _on_decode_complete)

    parse_started = file_srv.parse_log(file_path)
    assert parse_started

    parse_deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < parse_deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    record_id = parsed_record_id

    dbc_parsed = file_srv.parse_dbc(db_file_path, record_id)
    assert dbc_parsed

    dbc_deadline = time.monotonic() + PARSE_TIMEOUT
    while not dbc_event.is_set() and time.monotonic() < dbc_deadline:
        app.processEvents()
        dbc_event.wait(timeout=POLL_INTERVAL)

    assert dbc_event.is_set()
    assert dbc_loaded_record_id == record_id

    decode_started = file_srv.decode(record_id, db_file_path)
    assert decode_started

    decode_deadline = time.monotonic() + PARSE_TIMEOUT
    while not decode_event.is_set() and time.monotonic() < decode_deadline:
        app.processEvents()
        decode_event.wait(timeout=POLL_INTERVAL)

    assert decode_event.is_set()

    record = file_srv.get_record(record_id)
    assert record is not None
    print("parse_mmap:", record.get_runtime_mmap_paths())
    print("decode_mmap:", record.get_decode_mmap_paths())
    print("dbc_pkl_path:", record.get_dbc_pkl_path())


@pytest.mark.parametrize(
    "file_path, db_file_path",
    [
        (
            "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
            "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
            "EEA10_CANFD_R00c_withADAS_Main.dbc",
        ),
    ],
)
@pytest.mark.dependency(name="dbc_then_parse_16", depends=["decode"])
def test_16_parse_dbc_then_parse_log(file_path: str, db_file_path: str) -> None:
    dbc_event = threading.Event()
    parse_event = threading.Event()
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])

    file_srv = get_file_service()

    dbc_record_id: RecordId | None = None
    parsed_record_id: RecordId | None = None

    def _on_dbc_loaded(event: DBCLoadedEvent) -> None:
        nonlocal dbc_record_id
        if event.db_file_path == db_file_path and event.record_id is not None:
            dbc_record_id = event.record_id
            dbc_event.set()

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if (
            event.status == DATA_STATUS_DONE
            and event.record_id is not None
            and event.record_id == dbc_record_id
        ):
            parsed_record_id = event.record_id
            parse_event.set()

    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)
    file_srv.subscribe(ParserStatusEvent, _on_parser_status)

    dbc_started = file_srv.parse_dbc(db_file_path)
    assert dbc_started

    dbc_deadline = time.monotonic() + PARSE_TIMEOUT
    while not dbc_event.is_set() and time.monotonic() < dbc_deadline:
        app.processEvents()
        dbc_event.wait(timeout=POLL_INTERVAL)

    assert dbc_event.is_set()
    assert dbc_record_id is not None

    parse_started = file_srv.parse_log(file_path, dbc_record_id)
    assert parse_started

    parse_deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < parse_deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id == dbc_record_id

    record = file_srv.get_record(dbc_record_id)
    assert record is not None
    print("test_16 record_id:", dbc_record_id)
    print("test_16 parse_mmap:", record.get_runtime_mmap_paths())
    print("test_16 dbc_pkl_path:", record.get_dbc_pkl_path())


@pytest.mark.dependency(name="service_stopped", depends=["decode", "dbc_then_parse_16"])
def test_14_stop_service() -> None:
    """Stop FileService and wait until it publishes STOPPED."""
    stop_event = threading.Event()
    file_srv = get_file_service()

    assert file_srv.get_service_state() == ServiceState.RUNNING

    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.STOPPED and stop_event.set(),
    )
    file_srv.stop()
    assert stop_event.wait(timeout=TIMEOUT)
    assert file_srv.get_service_state() == ServiceState.STOPPED


@pytest.mark.dependency(depends=["service_stopped"])
def test_17_start_service_twice_only_start_once() -> None:
    running_count = 0
    running_event = threading.Event()
    stop_event = threading.Event()
    file_srv = get_file_service()

    assert file_srv.get_service_state() == ServiceState.STOPPED

    def _on_service_state(event: FileServiceStateEvent) -> None:
        nonlocal running_count
        if event.state == ServiceState.RUNNING:
            running_count += 1
            running_event.set()

    file_srv.subscribe(FileServiceStateEvent, _on_service_state)

    file_srv.start()
    assert running_event.wait(timeout=TIMEOUT)
    assert file_srv.get_service_state() == ServiceState.RUNNING

    file_srv.start()
    time.sleep(POLL_INTERVAL)
    assert file_srv.get_service_state() == ServiceState.RUNNING
    assert running_count == 1

    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.STOPPED and stop_event.set(),
    )
    file_srv.stop()
    assert stop_event.wait(timeout=TIMEOUT)
    assert file_srv.get_service_state() == ServiceState.STOPPED
