
from __future__ import annotations

import multiprocessing as mp

from file_service.decode.decode_source import decode_process
from lw.status_channel import StatusChannel
from file_service.metadata_id import LogId
# from file_service.qt_object import IPCWakeup
from lw.logger_setup import LOG
from file_service.status import DecodeStatus


def run_decode_async(
    record_mmap_path: LogId,
    state: StatusChannel,
    dbc_pkl_path: str,
) -> mp.Process:
    proc = mp.Process(
        target=_run_decode_job,
        args=(record_mmap_path, state, dbc_pkl_path),
        daemon=True,
        name="FileService-decoder",
    )
    proc.start()
    return proc


def _run_decode_job(
    record_mmap_path: LogId,
    state: StatusChannel,
    dbc_pkl_path: str,
) -> None:
    try:
        rc = int(decode_process(record_mmap_path, state, dbc_pkl_path))
        state.mc_send(int(DecodeStatus.DONE if rc == 0 else DecodeStatus.FAILED))
    except Exception as exc:
        state.mc_send(int(DecodeStatus.FAILED))
        LOG.error("Decode worker failed: %s", exc)
    finally:
        return
