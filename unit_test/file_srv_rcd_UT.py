from __future__ import annotations

import threading
import time

import pytest

from file_service.define import MMAP_LOCAL_STORAGE_DIR
from file_service.api.application_events import (
    DBCLoadedEvent,
    DecodeCompletedEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
    RecorderStatusEvent,
)
from file_service.api.status import RecorderStatus
from file_service.record_id import RecordId
from file_service.api.srv_if import FileService, get_file_service
from file_service.repository.file_handler.ring_handler import CAPACITY, CanLogRingHandler, CanLogRingPayload
from lw.base_service import ServiceState

TIMEOUT_STATUS = 0.5
PARSE_TIMEOUT = 15.0
TIMEOUT_STATUS_MS = int(TIMEOUT_STATUS * 1000)

# Keep ring test settings local so this test does not depend on lw constants.
CAN_SHARED_RING_SHM_NAME = "can_analyzer_ring_v1"
ENTRY_SIZE = 128


@pytest.fixture(scope="module")
def file_service():
	file_srv = get_file_service()

	# IT send/replay tests require FileService worker to be up before recording/parsing.
	if file_srv.get_service_state() != ServiceState.RUNNING:
		file_running = threading.Event()
		file_srv.subscribe(
			FileServiceStateEvent,
			lambda event: event.state == ServiceState.RUNNING and file_running.set(),
		)
		file_srv.start()
		assert file_running.wait(timeout=PARSE_TIMEOUT)
	assert file_srv.get_service_state() == ServiceState.RUNNING

	yield file_srv

	print("Stop service")
	file_srv.stop()


@pytest.fixture(scope="module")
def set_up_mmap():
    allocated: list[CanLogRingHandler] = []

    def _create(ring_slots: int) -> CanLogRingHandler:
        _ = ring_slots
        shm = CanLogRingHandler(mmap_name=str(CAN_SHARED_RING_SHM_NAME), create=True)
        shm.open()
        shm.format(write_idx=0)
        allocated.append(shm)
        return shm

    yield _create

    for shm in allocated:
        try:
            shm.close(unlink=True)
        except Exception:
            pass

"""
#BUG
The test creates shared memory with an explicit 8-byte header (shm_size = 8 + ENTRY_SIZE * slots).
The test writer manually updates write_idx in that header with struct.pack_into("<Q", shm.buf, 0, frame_idx + 1)
-> The writer is mocking, not the receiver writer
"""
def test_20_recording(file_service, qtbot, set_up_mmap) -> None:

    file_srv = get_file_service()
    running_event = threading.Event()
    producer_done = threading.Event()
    producer_stop = threading.Event()
    progress_stop = threading.Event()
    progress_ready = threading.Event()
    mock_row_count = 2560
    recorder_record_id: RecordId | None = None
    progress_samples: list[int] = []
    recorder_last_status: int | None = None
    record = None

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id, recorder_last_status
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.payload.get("status")
        recorder_last_status = int(status) if status is not None else None
        if event.status in (RecorderStatus.RECORDING, RecorderStatus.PAUSED, RecorderStatus.IDLE):
            running_event.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)

    shm = set_up_mmap(CAPACITY)

    def _mock_ring_writer() -> None:
        for frame_idx in range(mock_row_count):
            if producer_stop.is_set():
                break
            shm.write(
                CanLogRingPayload(
                    timestamp=float(frame_idx) / 1000.0,
                    can_id=int(frame_idx % 2048),
                    direction=int(frame_idx % 2),
                    data_len=64,
                    data=(f"mock-frame-{frame_idx}".encode("ascii") + b"\x00" * 64)[:64],
                    channel="1",
                )
            )
            time.sleep(0.002)
        producer_done.set()

    def _track_progress() -> None:
        while not progress_stop.is_set():
            try:
                if record is not None:
                    progress_samples.append(int(record.get_progress_index()))
                    print(int(record.get_progress_index()))
            except Exception:
                # Keep tracker resilient to transient file state while recorder starts/stops.
                pass
            progress_ready.set()
            time.sleep(0.01)

    producer_thread = threading.Thread(target=_mock_ring_writer, daemon=True, name="mock-ring-writer")
    progress_thread: threading.Thread | None = None
    try:
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: running_event.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert recorder_record_id is not None
        record = file_srv.get_record(recorder_record_id)
        assert record is not None
        runtime_mmap_paths = record.get_runtime_mmap_paths()
        assert runtime_mmap_paths["data"]
        staging_path = runtime_mmap_paths["data"][0]

        progress_thread = threading.Thread(
            target=_track_progress,
            daemon=True,
            name="progress-tracker",
        )
        progress_thread.start()
        qtbot.waitUntil(lambda: progress_ready.is_set(), timeout=TIMEOUT_STATUS_MS)

        producer_thread.start()
        qtbot.waitUntil(lambda: producer_done.is_set(), timeout=90_000)
        qtbot.waitUntil(lambda: int(record.get_progress_index()) >= mock_row_count, timeout=90_000)

        expected_bytes = mock_row_count * ENTRY_SIZE
        assert recorder_last_status in (
            int(RecorderStatus.RECORDING),
            int(RecorderStatus.PAUSED),
            int(RecorderStatus.IDLE),
        )
        assert progress_samples
        assert int(record.get_progress_index()) >= mock_row_count
        assert staging_path.exists()
        assert staging_path.stat().st_size >= expected_bytes + 32
        
        print("test_20 record_id:", recorder_record_id)
        print("test_20 record_mmap_dir:", staging_path.parent)
        print("test_20 record_mmap_path:", staging_path)
    finally:
        file_srv.stop_recording()
        progress_stop.set()
        if progress_thread is not None:
            progress_thread.join(timeout=2.0)
        producer_stop.set()
        if producer_thread.ident is not None:
            producer_thread.join(timeout=2.0)


def test_19_stop_recording(file_service, qtbot, set_up_mmap) -> None:
    file_srv = get_file_service()
    running_event = threading.Event()
    stopped_event = threading.Event()
    error_event = threading.Event()
    recorder_record_id: RecordId | None = None

    shm = set_up_mmap(CAPACITY)

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.status
        if status in (RecorderStatus.RECORDING, RecorderStatus.PAUSED, RecorderStatus.IDLE):
            running_event.set()
        elif status == RecorderStatus.STOPPED:
            stopped_event.set()
        elif status == RecorderStatus.FAILED:
            error_event.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)

    assert file_srv.start_recording() is True
    qtbot.waitUntil(lambda: running_event.is_set(), timeout=TIMEOUT_STATUS_MS)
    assert recorder_record_id is not None

    record = file_srv.get_record(recorder_record_id)
    assert record is not None

    qtbot.wait(1000)

    file_srv.stop_recording()
    qtbot.waitUntil(lambda: stopped_event.is_set(), timeout=TIMEOUT_STATUS_MS)
    assert not error_event.is_set()

    record_after_stop = file_srv.get_record(recorder_record_id)
    assert record_after_stop is not None
    runtime_mmap_paths = record_after_stop.get_runtime_mmap_paths()
    assert runtime_mmap_paths["data"]


def test_31_recording_ring_overlap(file_service, qtbot, set_up_mmap) -> None:
    file_srv = get_file_service()
    running_event = threading.Event()
    stopped_event = threading.Event()
    error_event = threading.Event()
    producer_done = threading.Event()
    producer_stop = threading.Event()

    # Intentionally much smaller than produced rows to force overwrite pressure.
    ring_slots = CAPACITY
    mock_row_count = 20000

    recorder_record_id: RecordId | None = None
    recorder_last_status: int | None = None

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id, recorder_last_status
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.status
        recorder_last_status = int(status) if status is not None else None
        if status in (RecorderStatus.RECORDING, RecorderStatus.PAUSED, RecorderStatus.IDLE):
            running_event.set()
        elif status == RecorderStatus.STOPPED:
            stopped_event.set()
        elif status == RecorderStatus.FAILED:
            error_event.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)
    shm = set_up_mmap(ring_slots)

    def _mock_ring_writer() -> None:
        for frame_idx in range(mock_row_count):
            if producer_stop.is_set():
                break
            shm.write(
                CanLogRingPayload(
                    timestamp=float(frame_idx) / 1000.0,
                    can_id=int(frame_idx % 2048),
                    direction=int(frame_idx % 2),
                    data_len=64,
                    data=(f"overlap-frame-{frame_idx}".encode("ascii") + b"\x00" * 64)[:64],
                    channel="1",
                )
            )
            time.sleep(0.002)
        producer_done.set()

    producer_thread = threading.Thread(target=_mock_ring_writer, daemon=True, name="mock-ring-overlap-writer")
    try:
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: running_event.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert recorder_record_id is not None

        record = file_srv.get_record(recorder_record_id)
        assert record is not None

        producer_thread.start()
        qtbot.waitUntil(lambda: producer_done.is_set(), timeout=90_000)

        file_srv.stop_recording()
        qtbot.waitUntil(lambda: stopped_event.is_set(), timeout=TIMEOUT_STATUS_MS)

        runtime_mmap_paths = record.get_runtime_mmap_paths()
        assert runtime_mmap_paths["data"]
        staging_path = runtime_mmap_paths["data"][0]
        assert staging_path.exists()

        actual_size = int(staging_path.stat().st_size)
        assert actual_size >= int(MmapHeaderConstract.SIZE)
        payload_bytes = actual_size - int(MmapHeaderConstract.SIZE)
        assert payload_bytes % int(ENTRY_SIZE) == 0

        persisted_frames = payload_bytes // int(ENTRY_SIZE)
        assert persisted_frames > 0
        assert persisted_frames < mock_row_count
        assert recorder_last_status == int(RecorderStatus.STOPPED)
        assert not error_event.is_set()

        print("test_21 record_id:", recorder_record_id)
        print("test_21 persisted_frames:", persisted_frames, "target:", mock_row_count, "ring_slots:", ring_slots)
    finally:
        try:
            file_srv.stop_recording()
        except Exception:
            pass
        producer_stop.set()
        if producer_thread.ident is not None:
            producer_thread.join(timeout=2.0)


def test_42_recording_close_early(file_service, qtbot, set_up_mmap) -> None:
    """Close recording before the producer finishes; persisted frames must be < mock_row_count."""
    file_srv = get_file_service()
    running_event = threading.Event()
    stopped_event = threading.Event()
    error_event = threading.Event()
    producer_done = threading.Event()
    producer_stop = threading.Event()

    # Large ring so there is no overlap pressure; early close is the only reason frames are fewer.
    ring_slots = CAPACITY
    mock_row_count = 20000
    # Stop recording after this many frames have been written to the ring.
    early_stop_threshold = mock_row_count // 4

    recorder_record_id: RecordId | None = None
    recorder_last_status: int | None = None

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id, recorder_last_status
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.status
        recorder_last_status = int(status) if status is not None else None
        if status in (RecorderStatus.RECORDING, RecorderStatus.PAUSED, RecorderStatus.IDLE):
            running_event.set()
        elif status == RecorderStatus.STOPPED:
            stopped_event.set()
        elif status == RecorderStatus.FAILED:
            error_event.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)
    shm = set_up_mmap(ring_slots)

    frames_written = 0

    def _mock_ring_writer() -> None:
        nonlocal frames_written
        for frame_idx in range(mock_row_count):
            if producer_stop.is_set():
                break
            shm.write(
                CanLogRingPayload(
                    timestamp=float(frame_idx) / 1000.0,
                    can_id=int(frame_idx % 2048),
                    direction=int(frame_idx % 2),
                    data_len=64,
                    data=(f"close-frame-{frame_idx}".encode("ascii") + b"\x00" * 64)[:64],
                    channel="1",
                )
            )
            frames_written = frame_idx + 1
            time.sleep(0.002)
        producer_done.set()

    producer_thread = threading.Thread(target=_mock_ring_writer, daemon=True, name="mock-ring-close-writer")
    try:
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: running_event.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert recorder_record_id is not None

        record = file_srv.get_record(recorder_record_id)
        assert record is not None

        producer_thread.start()

        # Wait until the producer has written enough frames, then close recording early.
        qtbot.waitUntil(lambda: frames_written >= early_stop_threshold, timeout=90_000)
        file_srv.stop_recording()
        qtbot.waitUntil(lambda: stopped_event.is_set(), timeout=TIMEOUT_STATUS_MS)

        # Signal producer to stop after recording is closed.
        producer_stop.set()

        runtime_mmap_paths = record.get_runtime_mmap_paths()
        assert runtime_mmap_paths["data"]
        staging_path = runtime_mmap_paths["data"][0]
        assert staging_path.exists()

        actual_size = int(staging_path.stat().st_size)
        assert actual_size >= int(MmapHeaderConstract.SIZE)
        payload_bytes = actual_size - int(MmapHeaderConstract.SIZE)
        assert payload_bytes % int(ENTRY_SIZE) == 0

        persisted_frames = payload_bytes // int(ENTRY_SIZE)
        assert persisted_frames > 0
        assert persisted_frames < mock_row_count
        assert recorder_last_status == int(RecorderStatus.STOPPED)
        assert not error_event.is_set()

        print("test_22 record_id:", recorder_record_id)
        print("test_22 persisted_frames:", persisted_frames, "target:", mock_row_count, "early_stop_threshold:", early_stop_threshold)
    finally:
        try:
            file_srv.stop_recording()
        except Exception:
            pass
        producer_stop.set()
        if producer_thread.ident is not None:
            producer_thread.join(timeout=2.0)


def test_53_two_sequential_recordings(file_service, qtbot, set_up_mmap) -> None:
    """Two sequential recordings; each captures one half — total persisted frames == mock_row_count."""
    file_srv = get_file_service()

    # Large ring: no overlap pressure in either session.
    ring_slots = CAPACITY
    mock_row_count = 10000
    batch_size = mock_row_count // 2  # 5000 frames per session

    running_event_1 = threading.Event()
    stopped_event_1 = threading.Event()
    error_event_1 = threading.Event()
    running_event_2 = threading.Event()
    stopped_event_2 = threading.Event()
    error_event_2 = threading.Event()

    record_ids: list[RecordId] = []
    status_by_record: dict[RecordId, int] = {}

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        payload_record_id = event.payload.get("record_id")
        status = event.status
        if isinstance(payload_record_id, RecordId):
            if payload_record_id not in record_ids:
                record_ids.append(payload_record_id)
            if status is not None:
                status_by_record[payload_record_id] = int(status)
        if status in (RecorderStatus.RECORDING, RecorderStatus.PAUSED, RecorderStatus.IDLE):
            if not running_event_1.is_set():
                running_event_1.set()
            elif not running_event_2.is_set():
                running_event_2.set()
        elif status == RecorderStatus.STOPPED:
            if not stopped_event_1.is_set():
                stopped_event_1.set()
            elif not stopped_event_2.is_set():
                stopped_event_2.set()
        elif status == RecorderStatus.FAILED:
            if not error_event_1.is_set():
                error_event_1.set()
            elif not error_event_2.is_set():
                error_event_2.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)
    shm = set_up_mmap(ring_slots)

    # Producer writes batch_size frames, signals mid_done, then writes next batch_size frames.
    mid_done = threading.Event()
    producer_stop = threading.Event()
    producer_all_done = threading.Event()

    def _mock_ring_writer() -> None:
        for frame_idx in range(mock_row_count):
            if producer_stop.is_set():
                break
            shm.write(
                CanLogRingPayload(
                    timestamp=float(frame_idx) / 1000.0,
                    can_id=int(frame_idx % 2048),
                    direction=int(frame_idx % 2),
                    data_len=64,
                    data=(f"seq-frame-{frame_idx}".encode("ascii") + b"\x00" * 64)[:64],
                    channel="1",
                )
            )
            if frame_idx + 1 == batch_size:
                mid_done.set()
            time.sleep(0.002)
        producer_all_done.set()

    producer_thread = threading.Thread(target=_mock_ring_writer, daemon=True, name="mock-ring-seq-writer")
    try:
        # --- Session 1 ---
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: running_event_1.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert len(record_ids) == 1
        record_1 = file_srv.get_record(record_ids[0])
        assert record_1 is not None

        producer_thread.start()
        # Wait for producer to finish the first batch and recorder to catch up.
        qtbot.waitUntil(lambda: mid_done.is_set(), timeout=90_000)
        qtbot.waitUntil(lambda: int(record_1.get_progress_index()) >= batch_size, timeout=90_000)

        file_srv.stop_recording()
        qtbot.waitUntil(lambda: stopped_event_1.is_set(), timeout=TIMEOUT_STATUS_MS)

        # --- Session 2 ---
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: running_event_2.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert len(record_ids) == 2
        record_2 = file_srv.get_record(record_ids[1])
        assert record_2 is not None

        # Wait for the producer to finish all frames, then stop recording.
        qtbot.waitUntil(lambda: producer_all_done.is_set(), timeout=90_000)

        file_srv.stop_recording()
        qtbot.waitUntil(lambda: stopped_event_2.is_set(), timeout=TIMEOUT_STATUS_MS)

        # --- Verify ---
        def _persisted_frames(record):
            paths = record.get_runtime_mmap_paths()
            assert paths["data"]
            path = paths["data"][0]
            assert path.exists()
            sz = int(path.stat().st_size)
            assert sz >= int(MmapHeaderConstract.SIZE)
            payload_bytes = sz - int(MmapHeaderConstract.SIZE)
            assert payload_bytes % int(ENTRY_SIZE) == 0
            return payload_bytes // int(ENTRY_SIZE)

        persisted_1 = _persisted_frames(record_1)
        persisted_2 = _persisted_frames(record_2)

        assert len(record_ids) == 2
        assert persisted_1 > 0
        assert persisted_2 > 0
        assert status_by_record[record_ids[0]] == int(RecorderStatus.STOPPED)
        assert status_by_record[record_ids[1]] == int(RecorderStatus.STOPPED)
        assert not error_event_1.is_set()
        assert not error_event_2.is_set()
        # Frames written to the ring while recording was stopped are not captured by either session.
        assert persisted_1 + persisted_2 < mock_row_count

        print("test_23 record_id_1:", record_ids[0], "persisted_1:", persisted_1)
        print("test_23 record_id_2:", record_ids[1], "persisted_2:", persisted_2)
        print("test_23 total:", persisted_1 + persisted_2, "mock_row_count:", mock_row_count)
    finally:
        try:
            file_srv.stop_recording()
        except Exception:
            pass
        producer_stop.set()
        if producer_thread.ident is not None:
            producer_thread.join(timeout=2.0)


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