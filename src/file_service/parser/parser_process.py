import multiprocessing as mp

from lw.platform.linux_platform import _set_linux_process_name
from file_service.dispatcher.qt_object import IPCWakeup
from file_service.parser.native.native_parser import NativeParser
from file_service.parser.native.can_parser_api import MmapHeaderConstract
from lw.logger_setup import LOG

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
    MmapHeaderConstract.load_from_native_binding()
    try:
        rc = NativeParser.parse(file_path, data_mmap_path, index_mmap_path)
        if not rc:
            LOG.error(f"C++ run failed (returned {rc})")
    except Exception as error:
        LOG.error(f"C++ run_worker_2pass failed: {error}")
    finally:
        # Emit one terminal wakeup after parse attempt completes.
        wakeup.signal()