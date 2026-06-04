"""Writer process entry point for mmap recording."""

from __future__ import annotations

from file_service.recorder.recorder_process import RecorderProcess


def writer_process(
    shm_name: str,
    output_mmap_path: str,
    stop_event,
    wakeup,
):
    RecorderProcess(
        shm_name=shm_name,
        output_mmap_path=output_mmap_path,
        stop_event=stop_event,
        wakeup=wakeup,
    ).run()