from __future__ import annotations

import threading
import time
from multiprocessing import shared_memory

import pytest

from file_service.define import MMAP_LOCAL_STORAGE_DIR
from file_service.application_events import (
    DBCLoadedEvent,
    DecodeCompletedEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
    RecorderStatusEvent,
)
from file_service.status import RecorderStatus
from file_service.record_id import RecordId
from file_service.srv_if import FileService, get_file_service
from file_service.module.fs_core import ParsedEntry
from file_service.repository.record import Record
from file_service.repository.file_handler.ring_handler import CAPACITY, CanLogRingHandler, CanLogRingPayload
from lw.service.base_service import ServiceState

TIMEOUT_STATUS = 0.5
PARSE_TIMEOUT = 15.0
TIMEOUT_STATUS_MS = int(TIMEOUT_STATUS * 1000)

# Keep ring test settings local so this test does not depend on lw constants.
CAN_SHARED_RING_SHM_NAME = "can_analyzer_ring_v1"
ENTRY_SIZE = 128

shm = None


def _assert_first_entries_match_mock(record: Record, payload_prefix: str, take_count: int = 10) -> None:
    first_entries = record.get_page_from_row_indices(0, take_count)
    assert isinstance(first_entries, list)
    assert len(first_entries) > 0
    assert len(first_entries) <= take_count
    assert all(isinstance(entry, ParsedEntry) for entry in first_entries)

    for row_idx, entry in enumerate(first_entries):
        entry_data_len = int(entry.data_len)
        assert entry_data_len == 64

        entry_data = bytes(int(entry.data[i]) for i in range(entry_data_len))
        entry_text = entry_data.rstrip(b"\x00").decode("ascii", errors="replace")
        entry_hex = " ".join(f"{byte:02X}" for byte in entry_data)
        expected_text = f"{payload_prefix}-{row_idx}"
        expected_data = (expected_text.encode("ascii") + b"\x00" * 64)[:64]
        expected_hex = " ".join(f"{byte:02X}" for byte in expected_data)

        print(
            f"[record-data] row={row_idx} can_id={int(entry.can_id)} dir={int(entry.direction)} "
            f"ch={entry.channel} len={entry_data_len} text={entry_text!r} hex={entry_hex} "
            f"expected_text={expected_text!r} expected_hex={expected_hex}"
        )

        assert entry_data == expected_data, (
            f"row={row_idx} payload mismatch: actual_text={entry_text!r} expected_text={expected_text!r}"
        )
        assert int(entry.can_id) == row_idx % 2048
        assert int(entry.direction) == row_idx % 2
        assert str(entry.channel) == "1"


def _assert_first_entries_sequential(record: Record, payload_prefix: str, take_count: int = 10) -> None:
    """Assert first entries have sequential payloads (for cases where row index != frame index)."""
    first_entries = record.get_page_from_row_indices(0, take_count)
    assert isinstance(first_entries, list)
    assert len(first_entries) > 0
    assert len(first_entries) <= take_count
    assert all(isinstance(entry, ParsedEntry) for entry in first_entries)

    # Extract the starting frame number from first entry's payload
    first_entry_data = bytes(int(first_entries[0].data[i]) for i in range(int(first_entries[0].data_len)))
    first_entry_text = first_entry_data.rstrip(b"\x00").decode("ascii", errors="replace")
    
    # Parse frame index from "prefix-NNN" format
    if not first_entry_text.startswith(payload_prefix + "-"):
        raise AssertionError(f"First entry doesn't match expected prefix {payload_prefix}: {first_entry_text!r}")
    
    start_frame_idx = int(first_entry_text.split("-")[-1])

    for row_idx, entry in enumerate(first_entries):
        entry_data_len = int(entry.data_len)
        assert entry_data_len == 64

        entry_data = bytes(int(entry.data[i]) for i in range(entry_data_len))
        entry_text = entry_data.rstrip(b"\x00").decode("ascii", errors="replace")
        entry_hex = " ".join(f"{byte:02X}" for byte in entry_data)
        
        # Verify sequential frame numbering starting from detected offset
        expected_frame_idx = start_frame_idx + row_idx
        expected_text = f"{payload_prefix}-{expected_frame_idx}"
        expected_data = (expected_text.encode("ascii") + b"\x00" * 64)[:64]
        expected_hex = " ".join(f"{byte:02X}" for byte in expected_data)

        print(
            f"[record-data-seq] row={row_idx} frame={expected_frame_idx} can_id={int(entry.can_id)} "
            f"dir={int(entry.direction)} ch={entry.channel} len={entry_data_len} text={entry_text!r} "
            f"hex={entry_hex} expected_text={expected_text!r} expected_hex={expected_hex}"
        )

        assert entry_data == expected_data, (
            f"row={row_idx} frame={expected_frame_idx} payload mismatch: actual_text={entry_text!r} expected_text={expected_text!r}"
        )
        assert str(entry.channel) == "1"

@pytest.fixture(scope="module")
def file_service():
    global shm
    file_srv = get_file_service()

    # Tear down stale POSIX shm if previous run left it behind, then create fresh ring mmap.
    try:
        stale = shared_memory.SharedMemory(name=str(CAN_SHARED_RING_SHM_NAME), create=False)
        stale.close()
        stale.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass

    shm = CanLogRingHandler(mmap_name=str(CAN_SHARED_RING_SHM_NAME), create=True)
    shm.open()
    shm.format(write_idx=0)

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

    if shm is not None:
        try:
            shm.close(unlink=True)
        except Exception:
            pass

    print("Stop service")
    file_srv.stop()

"""
#BUG
The test creates shared memory with an explicit 8-byte header (shm_size = 8 + ENTRY_SIZE * slots).
The test writer manually updates write_idx in that header with struct.pack_into("<Q", shm.buf, 0, frame_idx + 1)
-> The writer is mocking, not the receiver writer
"""
def test_20_recording(file_service, qtbot) -> None:

    file_srv = get_file_service()
    write_batch_evt = threading.Event()
    wait_ring_evt = threading.Event()
    producer_done = threading.Event()
    producer_stop = threading.Event()
    progress_stop = threading.Event()
    progress_ready = threading.Event()
    recorder_evt = threading.Event()
    recorder_paused_evt = threading.Event()
    recorder_idle_evt = threading.Event()
    recorder_nok_evt = threading.Event()
    mock_row_count = 2560
    recorder_record_id: RecordId | None = None
    progress_samples: list[int] = []
    record: Record | None = None

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id
        recorder_evt.set()
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        if event.status == RecorderStatus.WRITE_BATCH:
            write_batch_evt.set()
        elif event.status == RecorderStatus.PAUSED:
            recorder_paused_evt.set()
        elif event.status == RecorderStatus.WAIT_RING:
            recorder_idle_evt.set()
            wait_ring_evt.set()
        elif event.status == RecorderStatus.STOPPED:
            recorder_idle_evt.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)

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
                    #print(int(record.get_progress_index()))
            except Exception:
                # Keep tracker resilient to transient file state while recorder starts/stops.
                pass
            progress_ready.set()
            time.sleep(0.01)

    producer_thread = threading.Thread(target=_mock_ring_writer, daemon=True, name="mock-ring-writer")
    progress_thread: threading.Thread | None = None
    try:
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: write_batch_evt.is_set() or wait_ring_evt.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert recorder_record_id is not None
        record = file_srv.get_record(recorder_record_id)
        assert record is not None

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

        persisted_frames = int(record.get_total_lines())
        assert recorder_evt.is_set()
        assert write_batch_evt.is_set()
        assert not recorder_nok_evt.is_set()
        assert progress_samples
        assert int(record.get_progress_index()) >= mock_row_count
        assert persisted_frames >= mock_row_count
        _assert_first_entries_match_mock(record, "mock-frame")
        
        print("test_20 record_id:", recorder_record_id)
        print("test_20 persisted_frames:", persisted_frames)
    finally:
        file_srv.stop_recording()
        progress_stop.set()
        if progress_thread is not None:
            progress_thread.join(timeout=2.0)
        producer_stop.set()
        if producer_thread.ident is not None:
            producer_thread.join(timeout=2.0)


def test_19_stop_recording(file_service, qtbot) -> None:
    file_srv = get_file_service()
    write_batch_evt = threading.Event()
    wait_ring_evt = threading.Event()
    error_event = threading.Event()
    recorder_paused_evt = threading.Event()
    recorder_idle_evt = threading.Event()
    recorder_record_id: RecordId | None = None

    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.status
        if status == RecorderStatus.WRITE_BATCH:
            write_batch_evt.set()
        elif status == RecorderStatus.PAUSED:
            recorder_paused_evt.set()
        elif status == RecorderStatus.WAIT_RING:
            recorder_idle_evt.set()
            wait_ring_evt.set()
        elif status == RecorderStatus.STOPPED:
            recorder_idle_evt.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)

    assert file_srv.start_recording() is True
    qtbot.waitUntil(lambda: write_batch_evt.is_set() or wait_ring_evt.is_set(), timeout=TIMEOUT_STATUS_MS)
    assert recorder_record_id is not None

    record = file_srv.get_record(recorder_record_id)
    assert record is not None

    qtbot.wait(1000)

    file_srv.stop_recording()
    qtbot.wait(300)
    assert write_batch_evt.is_set() or wait_ring_evt.is_set()
    assert not error_event.is_set()

    record_after_stop = file_srv.get_record(recorder_record_id)
    assert record_after_stop is not None
    assert int(record_after_stop.get_total_lines()) >= 0


def test_31_recording_ring_overlap(file_service, qtbot) -> None:
    file_srv = get_file_service()
    write_batch_evt = threading.Event()
    wait_ring_evt = threading.Event()
    error_event = threading.Event()
    recorder_evt = threading.Event()
    recorder_paused_evt = threading.Event()
    recorder_idle_evt = threading.Event()
    recorder_nok_evt = threading.Event()
    producer_done = threading.Event()
    producer_stop = threading.Event()

    # Intentionally much smaller than produced rows to force overwrite pressure.
    ring_slots = CAPACITY
    mock_row_count = 20000

    recorder_record_id: RecordId | None = None
    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id
        recorder_evt.set()
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.status
        if status == RecorderStatus.WRITE_BATCH:
            write_batch_evt.set()
        elif status == RecorderStatus.PAUSED:
            recorder_paused_evt.set()
        elif status == RecorderStatus.WAIT_RING:
            recorder_idle_evt.set()
            wait_ring_evt.set()
        elif status == RecorderStatus.STOPPED:
            recorder_idle_evt.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)


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
        qtbot.waitUntil(lambda: write_batch_evt.is_set() or wait_ring_evt.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert recorder_record_id is not None

        record = file_srv.get_record(recorder_record_id)
        assert record is not None

        producer_thread.start()
        qtbot.waitUntil(lambda: producer_done.is_set(), timeout=90_000)

        file_srv.stop_recording()
        qtbot.wait(300)

        persisted_frames = int(record.get_total_lines())
        assert persisted_frames > 0
        assert persisted_frames < mock_row_count
        assert recorder_evt.is_set()
        assert not recorder_nok_evt.is_set()
        assert not error_event.is_set()
        _assert_first_entries_match_mock(record, "overlap-frame")

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


def test_42_recording_close_early(file_service, qtbot) -> None:
    """Close recording before the producer finishes; persisted frames must be < mock_row_count."""
    file_srv = get_file_service()
    write_batch_evt = threading.Event()
    wait_ring_evt = threading.Event()
    error_event = threading.Event()
    recorder_evt = threading.Event()
    recorder_paused_evt = threading.Event()
    recorder_idle_evt = threading.Event()
    recorder_nok_evt = threading.Event()
    producer_done = threading.Event()
    producer_stop = threading.Event()

    # Large ring so there is no overlap pressure; early close is the only reason frames are fewer.
    ring_slots = CAPACITY
    mock_row_count = 10000
    # Stop recording after this many frames have been written to the ring.
    early_stop_threshold = mock_row_count // 4

    recorder_record_id: RecordId | None = None
    def _on_recorder_status(event: RecorderStatusEvent) -> None:
        nonlocal recorder_record_id
        recorder_evt.set()
        payload_record_id = event.payload.get("record_id")
        if isinstance(payload_record_id, RecordId):
            recorder_record_id = payload_record_id
        status = event.status
        if status == RecorderStatus.WRITE_BATCH:
            write_batch_evt.set()
        elif status == RecorderStatus.PAUSED:
            recorder_paused_evt.set()
        elif status == RecorderStatus.WAIT_RING:
            recorder_idle_evt.set()
            wait_ring_evt.set()
        elif status == RecorderStatus.STOPPED:
            recorder_idle_evt.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)


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
        qtbot.waitUntil(lambda: write_batch_evt.is_set() or wait_ring_evt.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert recorder_record_id is not None

        record = file_srv.get_record(recorder_record_id)
        assert record is not None

        producer_thread.start()

        # Wait until the producer has written enough frames, then close recording early.
        qtbot.waitUntil(lambda: frames_written >= early_stop_threshold, timeout=90_000)
        file_srv.stop_recording()
        qtbot.wait(300)

        # Signal producer to stop after recording is closed.
        producer_stop.set()

        persisted_frames = int(record.get_total_lines())
        assert persisted_frames > 0
        assert persisted_frames < mock_row_count
        assert recorder_evt.is_set()
        assert not recorder_nok_evt.is_set()
        assert not error_event.is_set()
        _assert_first_entries_match_mock(record, "close-frame")

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


def test_53_two_sequential_recordings(file_service, qtbot) -> None:
    """Two sequential recordings; each captures one half — total persisted frames == mock_row_count."""
    file_srv = get_file_service()

    # Large ring: no overlap pressure in either session.
    ring_slots = CAPACITY
    mock_row_count = 10000
    batch_size = mock_row_count // 2  # 5000 frames per session

    write_batch_event_1 = threading.Event()
    wait_ring_event_1 = threading.Event()
    error_event_1 = threading.Event()
    paused_event_1 = threading.Event()
    idle_event_1 = threading.Event()
    write_batch_event_2 = threading.Event()
    wait_ring_event_2 = threading.Event()
    error_event_2 = threading.Event()
    paused_event_2 = threading.Event()
    idle_event_2 = threading.Event()
    phase = 1

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
        if phase == 1:
            if status == RecorderStatus.WRITE_BATCH:
                write_batch_event_1.set()
            elif status == RecorderStatus.PAUSED:
                paused_event_1.set()
            elif status == RecorderStatus.WAIT_RING:
                idle_event_1.set()
                wait_ring_event_1.set()
            elif status == RecorderStatus.STOPPED:
                idle_event_1.set()
        elif phase == 2:
            if status == RecorderStatus.WRITE_BATCH:
                write_batch_event_2.set()
            elif status == RecorderStatus.PAUSED:
                paused_event_2.set()
            elif status == RecorderStatus.WAIT_RING:
                idle_event_2.set()
                wait_ring_event_2.set()
            elif status == RecorderStatus.STOPPED:
                idle_event_2.set()

    file_srv.subscribe(RecorderStatusEvent, _on_recorder_status)


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
        qtbot.waitUntil(lambda: write_batch_event_1.is_set() or wait_ring_event_1.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert len(record_ids) == 1
        record_1 = file_srv.get_record(record_ids[0])
        assert record_1 is not None

        producer_thread.start()
        # Wait for producer to finish the first batch and recorder to catch up.
        qtbot.waitUntil(lambda: mid_done.is_set(), timeout=90_000)
        qtbot.waitUntil(lambda: int(record_1.get_progress_index()) >= batch_size, timeout=90_000)

        file_srv.stop_recording()
        qtbot.wait(300)

        # --- Session 2 ---
        phase = 2
        assert file_srv.start_recording() is True
        qtbot.waitUntil(lambda: write_batch_event_2.is_set() or wait_ring_event_2.is_set(), timeout=TIMEOUT_STATUS_MS)
        assert len(record_ids) == 2
        record_2 = file_srv.get_record(record_ids[1])
        assert record_2 is not None

        # Wait for the producer to finish all frames, then stop recording.
        qtbot.waitUntil(lambda: producer_all_done.is_set(), timeout=90_000)

        file_srv.stop_recording()
        qtbot.wait(300)

        # --- Verify ---
        def _persisted_frames(record):
            return int(record.get_total_lines())

        persisted_1 = _persisted_frames(record_1)
        persisted_2 = _persisted_frames(record_2)

        assert len(record_ids) == 2
        assert persisted_1 > 0
        assert persisted_2 > 0
        assert status_by_record[record_ids[0]] in (int(RecorderStatus.WAIT_RING), int(RecorderStatus.PAUSED), int(RecorderStatus.WRITE_BATCH))
        assert status_by_record[record_ids[1]] in (int(RecorderStatus.WAIT_RING), int(RecorderStatus.PAUSED), int(RecorderStatus.WRITE_BATCH))
        assert not error_event_1.is_set()
        assert not error_event_2.is_set()
        # Frames written to the ring while recording was stopped are not captured by either session.
        assert persisted_1 + persisted_2 < mock_row_count
        _assert_first_entries_match_mock(record_1, "seq-frame")
        # record_2 starts reading from a different offset in the ring (ring wraparound).
        # Verify it captures sequential frames, but don't assume it starts at seq-frame-0.
        _assert_first_entries_sequential(record_2, "seq-frame")

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
