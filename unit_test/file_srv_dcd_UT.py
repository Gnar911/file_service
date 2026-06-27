from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Generator

import pytest
from PySide6.QtCore import QCoreApplication

from file_service.application_events import (
    DBCLoadedEvent,
    DecodeStatusEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
)
from file_service.srv_if import FileService, get_file_service
from file_service.status import ParserStatus
from file_service.status import DecodeStatus
from file_service.record_id import RecordId
from lw.service.base_service import ServiceState
from lw.logger_setup import setup_logger

PARSE_TIMEOUT = 15.0
DECODE_TIMEOUT = 20.0
POLL_INTERVAL = 0.1
_SERVICE_START_TIMEOUT = 5.0


def _tests_bin() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "file_srv_core"
        / "src"
        / "build"
        / "file_service_core_tests"
    )


def _run_decode_mock_test() -> None:
    subprocess.check_call(
        [
            str(_tests_bin()),
            "--gtest_filter=CanDecoderApiMock.DecodeEntryUsesTextSignalName",
        ]
    )


def _run_decoded_db_discovery(token_path: str) -> None:
    subprocess.check_call(
        [
            str(_tests_bin()),
            "--gtest_filter=DecodedSqliteApi.DatabaseFileDiscoveryByTokenPath",
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
    "_case",
    ["decode_mock"],
)
def test_14_cpp_decode_mock(_case: str) -> None:
    _ = _case
    _run_decode_mock_test()


"""
20260609: At this level of test case we introduce a stricter test assert, we do 3 type of event
decode_evt, decode_ok_evt, decode_nok_evt
"""
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
def test_15_parse_then_dbc_then_decode(file_service: FileService, qt_app, file_path: str, db_file_path: str) -> None:
    parse_event = threading.Event()
    dbc_event = threading.Event()
    decode_evt = threading.Event()
    decode_ok_evt = threading.Event()
    decode_nok_evt = threading.Event()

    app = qt_app
    file_srv = file_service

    parsed_record_id: RecordId | None = None
    dbc_loaded_record_id: RecordId | None = None
    decode_final_payload: dict | None = None

    def _on_parser_status(event: ParserStatusEvent) -> None:
        nonlocal parsed_record_id
        if event.status == ParserStatus.DONE and event.record_id is not None:
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

    def _on_decode_status(event: DecodeStatusEvent) -> None:
        nonlocal decode_final_payload

        if parsed_record_id is None or event.record_id != parsed_record_id:
            return

        decode_evt.set()

        if event.status is DecodeStatus.DONE:
            decode_final_payload = dict(event.payload)
            decode_ok_evt.set()

        if event.status is DecodeStatus.FAILED:
            decode_nok_evt.set()

    file_srv.subscribe(ParserStatusEvent, _on_parser_status)
    file_srv.subscribe(DBCLoadedEvent, _on_dbc_loaded)
    file_srv.subscribe(DecodeStatusEvent, _on_decode_status)

    assert file_srv.parse_log(file_path)

    parse_deadline = time.monotonic() + PARSE_TIMEOUT
    while not parse_event.is_set() and time.monotonic() < parse_deadline:
        app.processEvents()
        parse_event.wait(timeout=POLL_INTERVAL)

    assert parse_event.is_set()
    assert parsed_record_id is not None
    record_id = parsed_record_id

    assert file_srv.parse_dbc(db_file_path, record_id)

    dbc_deadline = time.monotonic() + PARSE_TIMEOUT
    while not dbc_event.is_set() and time.monotonic() < dbc_deadline:
        app.processEvents()
        dbc_event.wait(timeout=POLL_INTERVAL)

    assert dbc_event.is_set()
    assert dbc_loaded_record_id == record_id

    record = file_srv.get_record(record_id)
    assert record is not None
    token_path = str(record.get_base_path())
    print(f"\n[test_15] record_id={record_id}")
    print(f"[test_15] token_path={token_path}")

    assert file_srv.decode(record_id, db_file_path)
    decode_deadline = time.monotonic() + DECODE_TIMEOUT
    while (
        not decode_ok_evt.is_set()
        and not decode_nok_evt.is_set()
        and time.monotonic() < decode_deadline
    ):
        app.processEvents()
        decode_evt.wait(timeout=POLL_INTERVAL)

    assert not decode_nok_evt.is_set(), "Decode failed"

    assert decode_ok_evt.is_set(), (
        f"Decode timed out after {DECODE_TIMEOUT}s"
    )

    record = file_srv.get_record(record_id)
    assert record is not None

    token_path = str(record.get_base_path())
    _run_decoded_db_discovery(token_path)
