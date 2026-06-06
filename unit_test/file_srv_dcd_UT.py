




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
def test_15_decode_parse_then_dbc(file_service: FileService, qt_app, file_path: str, db_file_path: str) -> None:
    parse_event = threading.Event()
    dbc_event = threading.Event()
    decode_event = threading.Event()
    app = qt_app
    file_srv = file_service

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

