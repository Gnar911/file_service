
from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

from file_service.decode.decode_source import decode_process
from file_service.qt_object import IPCWakeup
from lw.logger_setup import LOG
from file_service.status import DecodeStatus


def run_decode_async(
    record_mmap_path: Path,
    db_file_path: str,
    wakeup: IPCWakeup,
    dbc_pkl_path: str,
    state,
) -> mp.Process:
    proc = mp.Process(
        target=_run_decode_job,
        args=(record_mmap_path, db_file_path, wakeup, dbc_pkl_path, state),
        daemon=True,
        name="FileService-decoder",
    )
    proc.start()
    return proc


def _run_decode_job(
    record_mmap_path: Path,
    db_file_path: str,
    wakeup: IPCWakeup,
    dbc_pkl_path: str,
    state,
) -> None:
    try:
        state.value = int(DecodeStatus.RUNNING)
        rc = int(decode_process(record_mmap_path, db_file_path, wakeup, dbc_pkl_path))
        state.value = int(DecodeStatus.DONE if rc == 0 else DecodeStatus.FAILED)
    except Exception as exc:
        state.value = int(DecodeStatus.FAILED)
        LOG.error("Decode worker failed: %s", exc)
    finally:
        wakeup.signal()
