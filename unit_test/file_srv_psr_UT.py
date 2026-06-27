from __future__ import annotations

import threading
import time
import subprocess
from pathlib import Path
from typing import Generator

import pytest
from PySide6.QtCore import QCoreApplication

from file_service.application_events import (
    DBCLoadedEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
)
from file_service.record_id import RecordId
from file_service.srv_if import FileService, get_file_service
from file_service.status import ParserStatus
from lw.service.base_service import ServiceState
from lw.logger_setup import setup_logger
from lw.logger_setup import LOG
from file_service.module.fs_core import ParsedEntry

TIMEOUT = 0.8
PARSE_TIMEOUT = 15.0
POLL_INTERVAL = 0.1
_SERVICE_START_TIMEOUT = 5.0

def _run_segment_discovery(token_path: str) -> None:
    tests_bin = (
        Path(__file__).resolve().parents[2]
        / "file_srv_core"
        / "src"
        / "build"
        / "file_service_core_tests"
    )
    subprocess.check_call(
        [
            str(tests_bin),
            "--gtest_filter=ParsedMmapInterfaceApi.SegmentDiscovery",
            f"--token_path={token_path}",
        ]
    )

@pytest.fixture(scope="session")
def qt_app():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app

@pytest.fixture(scope="session")
def file_service(qt_app) -> "Generator[FileService, None, None]":
    setup_logger(env="DEV", backup_count=30)
    file_srv = get_file_service()
    if file_srv.get_service_state() == ServiceState.RUNNING:
        yield file_srv
        return
    running_event = threading.Event()
    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.RUNNING and running_event.set(),
    )
    file_srv.start()
    assert running_event.wait(timeout=_SERVICE_START_TIMEOUT), "FileService did not reach RUNNING state"
    yield file_srv

    stopped_event = threading.Event()
    file_srv.subscribe(
        FileServiceStateEvent,
        lambda event: event.state == ServiceState.STOPPED and stopped_event.set(),
    )
    file_srv.stop()
    assert stopped_event.wait(timeout=_SERVICE_START_TIMEOUT), "FileService did not reach STOPPED state"


@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
    ],
)
def test_05_parse_log_with_record_id(file_service: FileService, qt_app, file_path: str) -> None:
    parse_event = threading.Event()
    app = qt_app
    file_srv = file_service

    record_id = file_srv.create_record()
    record_count_before = len(file_srv.list_log_records())
    parsed_record_id: RecordId | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        LOG.info(
            "parser_status_event status=%s record_id=%s payload=%s",
            event.status.name,
            event.record_id,
            event.payload,
        )
        if event.status == ParserStatus.FAILED:
            LOG.error(
                "parser_status_failed record_id=%s payload=%s",
                event.record_id,
                event.payload,
            )
        if event.status == ParserStatus.DONE and event.record_id is not None:
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

    record = file_srv.get_record(record_id)
    assert record is not None

    assert len(file_srv.list_log_records()) == record_count_before
    first_entries = record.get_page_from_row_indices(0, 10)
    assert len(first_entries) > 0
    assert len(first_entries) <= 10
    assert all(isinstance(entry, ParsedEntry) for entry in first_entries)
    print("record_data_size:", record.get_total_lines())
    #_run_segment_discovery(str(record.get_base_path()))
    print("first_entries_fields:")
    for entry in first_entries:
        print(
            {
                "line_number": int(entry.line_number),
                "timestamp": float(entry.timestamp),
                "last_timestamp": float(entry.last_timestamp),
                "can_id": int(entry.can_id),
                "direction": int(entry.direction),
                "data_len": int(entry.data_len),
                "changed": int(entry.changed),
            }
        )

    """ Adding the test for load all entries performance"""
    t1 = time.perf_counter()
    all_entries = record.get_all_entries()
    t2 = time.perf_counter()
    LOG.debug("get_all_entries: %s", t2 - t1)
    assert len(all_entries) == record.get_total_lines()
    # for entry in all_entries:
    #     print(
    #         {
    #             "line_number": int(entry.line_number),
    #             "timestamp": float(entry.timestamp),
    #             "last_timestamp": float(entry.last_timestamp),
    #             "can_id": int(entry.can_id),
    #             "direction": int(entry.direction),
    #             "data_len": int(entry.data_len),
    #             "changed": int(entry.changed),
    #         }
    #     )


@pytest.mark.parametrize(
    "text_line, expected_can_id, expected_channel, expected_data_len, expected_direction, expected_hex_data",
    [
        ("0.000001 1 123 Tx d 8 01 02 03 04 05 06 07 08", 0x123, "1", 8, "Tx", "01 02 03 04 05 06 07 08"),
        ("1.250000 7 1A5 Rx d 4 AA BB CC DD", 0x1A5, "7", 4, "Rx", "AA BB CC DD"),
    ],
)
def test_40_parse_line(
    file_service: FileService,
    text_line: str,
    expected_can_id: int,
    expected_channel: str,
    expected_data_len: int,
    expected_direction: str,
    expected_hex_data: str,
) -> None:
    parsed = file_service.parse_line(text_line)
    parsed_hex_data = " ".join(f"{int(parsed.data[i]):02X}" for i in range(int(parsed.data_len)))
    parsed_direction = "Tx" if int(parsed.direction) == 1 else "Rx"

    assert isinstance(parsed, ParsedEntry)
    assert int(parsed.line_number) == 1
    assert int(parsed.can_id) == expected_can_id
    assert str(parsed.channel) == expected_channel
    assert int(parsed.data_len) == expected_data_len
    assert parsed_direction == expected_direction
    assert parsed_hex_data == expected_hex_data


@pytest.mark.parametrize(
    "text_lines, expected_can_ids, expected_channels, expected_data_lens, expected_directions, expected_hex_data",
    [
        (
            "0.000001 1 123 Tx d 8 01 02 03 04 05 06 07 08\n1.250000 7 1A5 Rx d 4 AA BB CC DD",
            [0x123, 0x1A5],
            ["1", "7"],
            [8, 4],
            ["Tx", "Rx"],
            ["01 02 03 04 05 06 07 08", "AA BB CC DD"],
        ),
    ],
)
def test_41_parse_lines(
    file_service: FileService,
    text_lines: str,
    expected_can_ids: list[int],
    expected_channels: list[str],
    expected_data_lens: list[int],
    expected_directions: list[str],
    expected_hex_data: list[str],
) -> None:
    parsed_lines = file_service.parse_lines(text_lines)
    parsed_channels = [str(item.channel) for item in parsed_lines]
    parsed_directions = ["Tx" if int(item.direction) == 1 else "Rx" for item in parsed_lines]
    parsed_hex_data_values = [
        " ".join(f"{int(item.data[i]):02X}" for i in range(int(item.data_len)))
        for item in parsed_lines
    ]

    assert len(parsed_lines) == len(expected_can_ids)
    assert [int(item.line_number) for item in parsed_lines] == [1, 2]
    assert [int(item.can_id) for item in parsed_lines] == expected_can_ids
    assert parsed_channels == expected_channels
    assert [int(item.data_len) for item in parsed_lines] == expected_data_lens
    assert parsed_directions == expected_directions
    assert parsed_hex_data_values == expected_hex_data


@pytest.mark.parametrize(
    "file_path",
    [
        "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc",
    ],
)
def test_06_parse_log_without_record_id(file_service: FileService, qt_app, file_path: str) -> None:
    parse_event = threading.Event()
    app = qt_app
    file_srv = file_service

    record_ids_before = set(file_srv.list_log_records())
    parsed_record_id: RecordId | None = None
    done_event_count = 0

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        nonlocal done_event_count
        if (
            event.status == ParserStatus.DONE
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
    token_path = str(record.get_base_path())
    _run_segment_discovery(token_path)


@pytest.mark.parametrize(
    "db_file_path",
    [
        "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/"
        "EEA10_CANFD_R00c_withADAS_Main.dbc",
    ],
)
def test_07_parse_dbc_without_record(file_service: FileService, qt_app, db_file_path: str) -> None:
    callback_event = threading.Event()
    callback_data: DBCLoadedEvent | None = None
    app = qt_app
    file_srv = file_service

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
def test_08_parse_dbc_for_record(file_service: FileService, qt_app, db_file_path: str) -> None:
    callback_event = threading.Event()
    callback_data: DBCLoadedEvent | None = None
    app = qt_app
    file_srv = file_service

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
def test_09_parse_log_then_dbc_same_record(file_service: FileService, qt_app, file_path: str, db_file_path: str) -> None:
    """parse_log (no pre-created record) → use returned record_id → parse_dbc for that record."""
    parse_event = threading.Event()
    dbc_event = threading.Event()
    app = qt_app
    file_srv = file_service

    parsed_record_id: RecordId | None = None
    dbc_callback_data: DBCLoadedEvent | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == ParserStatus.DONE and event.record_id is not None:
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
    _run_segment_discovery(str(record.get_base_path()))
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
def test_10_parse_dbc_then_log_same_record(file_service: FileService, qt_app, file_path: str, db_file_path: str) -> None:
    """parse_dbc (no pre-created record) → use returned record_id → parse_log for that record."""
    parse_event = threading.Event()
    dbc_event = threading.Event()
    app = qt_app
    file_srv = file_service

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
            event.status == ParserStatus.DONE
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
    print("test_10 dbc_pkl_path:", pkl_path)
    _run_segment_discovery(str(record.get_base_path()))
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
def test_16_parse_dbc_then_parse_log(file_service: FileService, qt_app, file_path: str, db_file_path: str) -> None:
    dbc_event = threading.Event()
    parse_event = threading.Event()
    app = qt_app
    file_srv = file_service

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
            event.status == ParserStatus.DONE
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
    _run_segment_discovery(str(record.get_base_path()))
    print("test_16 record_id:", dbc_record_id)
    print("test_16 dbc_pkl_path:", record.get_dbc_pkl_path())
