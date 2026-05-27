"""Writer process entry point for mmap recording."""

from __future__ import annotations

from file_service.recorder.recorder_process import RecorderProcess
from file_service.recorder.record_snapshot import save_record_snapshot_async


def writer_process(
    shm_name: str,
    stop_event,
):
    RecorderProcess(
        shm_name=shm_name,
        stop_event=stop_event,
    ).run()