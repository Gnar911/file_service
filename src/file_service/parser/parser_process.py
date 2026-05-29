import multiprocessing as mp
from typing import Any

from lw.platform.linux_platform import _set_linux_process_name
from file_service.dispatcher.qt_object import IPCWakeup
from file_service.parser.native.native_parser import NativeParser
from lw.logger_setup import LOG
from native_sdk.can_parser_api import DATA_STATUS_ERROR

def run_parser_async(file_path: str, data_mmap_path: str, index_mmap_path: str, wakeup: IPCWakeup) -> mp.Process:
    proc = mp.Process(
            target=run_parse,
            args=(
                file_path,
                data_mmap_path,
                index_mmap_path,
                wakeup,
            ),
            daemon=True,
            name="CBCM-parser",
        )
    
    proc.start()
    return proc

def run_parse(file_path: str, data_mmap_path: str, index_mmap_path: str, wakeup: IPCWakeup) -> None:
    _set_linux_process_name("CBCM-parser")
    try:
        rc = NativeParser.parse(file_path, data_mmap_path, index_mmap_path)
        if not rc:
            LOG.error(f"C++ run failed (returned {rc})")
    except Exception as error:
        LOG.error(f"C++ run_worker_2pass failed: {error}")
    finally:
        # Emit one terminal wakeup after parse attempt completes.
        wakeup.signal()


def get_status(record_id: Any, record: Any) -> int:
    try:
        if record is None:
            return int(DATA_STATUS_ERROR)

        record.refresh_runtime()
        data_segments = list(record.raw.data_segment_paths())
        if not data_segments:
            return int(DATA_STATUS_ERROR)

        return int(NativeParser.get_status(record_id=record_id, data_mmap_path=str(data_segments[0])))
    except Exception as error:
        LOG.error("Failed to get parser status for %s: %s", record_id, error)
        return int(DATA_STATUS_ERROR)