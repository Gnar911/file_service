
from __future__ import annotations

import multiprocessing as mp
from typing import Any
from pathlib import Path

from file_service.decode.decode_source import decode_process
from file_service.decode.native.native_decoder import NativeDecoder
from file_service.dispatcher.qt_object import IPCWakeup
from lw.logger_setup import LOG
from native_sdk.can_decoder_api import DECODE_STATUS_DONE, DECODE_STATUS_ERROR, DECODE_STATUS_RUNNING
from native_sdk.can_parser_api import DATA_STATUS_DONE, DATA_STATUS_ERROR, DATA_STATUS_RUNNING


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
    try:
        decode_process(record_mmap_path, db_file_path, wakeup, dbc_pkl_path)
    except Exception as exc:
        LOG.error("Decode worker failed: %s", exc)
    finally:
        wakeup.signal()


def _map_decode_status_to_data_status(decode_status: int) -> int:
    if decode_status == int(DECODE_STATUS_DONE):
        return int(DATA_STATUS_DONE)
    if decode_status == int(DECODE_STATUS_RUNNING):
        return int(DATA_STATUS_RUNNING)
    if decode_status == int(DECODE_STATUS_ERROR):
        return int(DATA_STATUS_ERROR)
    return int(DATA_STATUS_ERROR)


def get_status(record_id: Any, record: Any) -> int:
    try:
        if record is None:
            return int(DATA_STATUS_ERROR)

        record.refresh_runtime()
        decode_paths = record.get_decode_mmap_paths().get("row_index", [])
        if not decode_paths:
            return int(DATA_STATUS_ERROR)

        decode_status = NativeDecoder.get_status(record_id=record_id, row_index_mmap_path=str(decode_paths[0]))
        return _map_decode_status_to_data_status(int(decode_status))
    except Exception as error:
        LOG.error("Failed to get decode status for %s: %s", record_id, error)
        return int(DATA_STATUS_ERROR)
