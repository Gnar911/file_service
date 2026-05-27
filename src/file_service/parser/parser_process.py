import multiprocessing as mp
from lw.platform.linux_platform import _set_linux_process_name
from file_service.dispatcher.qt_object import IPCWakeup
from file_service.parser.native.native_parser import NativeParser
from lw.logger_setup import LOG

def run_parser_proc(file_path: str, mmap_path: str, wakeup: IPCWakeup) -> mp.Process:
    proc = mp.Process(
            target=run_parse,
            args=(
                file_path,
                mmap_path,
                wakeup,
            ),
            daemon=True,
            name="CBCM-parser",
        )
    
    proc.start()
    return proc

def run_parse(file_path: str, mmap_path: str, wakeup: IPCWakeup) -> None:
    _set_linux_process_name("CBCM-parser")
    try:
        wakeup.signal()
        rc = NativeParser.parse(file_path, mmap_path)
        if not rc:
            wakeup.signal()
            LOG.error(f"C++ run failed (returned {rc})")
        wakeup.signal()
    except Exception as error:
        LOG.error(f"C++ run_worker_2pass failed: {error}")
        return