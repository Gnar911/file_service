
from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

from file_service.decode.decode_source import decode_process
from file_service.dispatcher.qt_object import IPCWakeup
from file_service.parser.native.can_parser_api import MmapHeaderConstract
from lw.logger_setup import LOG


def run_decode_async(
    record_mmap_path: Path,
    db_file_path: str,
    wakeup: IPCWakeup,
    dbc_pkl_path: str,
) -> mp.Process:
    proc = mp.Process(
        target=_run_decode_job,
        args=(record_mmap_path, db_file_path, wakeup, dbc_pkl_path),
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
) -> None:
    MmapHeaderConstract.load_from_native_binding()
    try:
        decode_process(record_mmap_path, db_file_path, wakeup, dbc_pkl_path)
    except Exception as exc:
        LOG.error("Decode worker failed: %s", exc)
    finally:
        wakeup.signal()
