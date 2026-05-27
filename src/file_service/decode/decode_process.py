
from __future__ import annotations

import multiprocessing as mp
from typing import Iterable
from pathlib import Path

from file_service.decode.decode_source import decode_process
from file_service.dispatcher.qt_object import IPCWakeup
from lw.logger_setup import LOG


def run_decode_async(record_mmap_path: Path, db_file_path: str, wakeup: IPCWakeup) -> mp.Process:
    proc = mp.Process(
        target=_run_decode_job,
        args=(record_mmap_path, db_file_path, wakeup),
        daemon=True,
        name="FileService-decoder",
    )
    proc.start()
    return proc


def _run_decode_job(record_mmap_path: Path, db_file_path: str, wakeup: IPCWakeup) -> None:
    try:
        decode_process(record_mmap_path, db_file_path, wakeup)
    except Exception as exc:
        LOG.error("Decode worker failed: %s", exc)
    finally:
        wakeup.signal()
