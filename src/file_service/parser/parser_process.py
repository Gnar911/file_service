import multiprocessing as mp

from lw.platform.linux_platform import _set_linux_process_name
from file_service.dispatcher.qt_object import IPCWakeup
from file_service.parser.native.native_parser import NativeParser
from lw.logger_setup import LOG
from file_service.api.status import ParserStatus

def run_parser_async(
    file_path: str,
    token_id: str,
    wakeup: IPCWakeup,
    state,
) -> mp.Process:
    proc = mp.Process(
            target=run_parse,
            args=(
                file_path,
                token_id,
                wakeup,
                state,
            ),
            daemon=True,
            name="CBCM-parser",
        )
    
    proc.start()
    return proc

def run_parse(file_path: str, token_id: str, wakeup: IPCWakeup, state) -> None:
    _set_linux_process_name("CBCM-parser")
    try:
        state.value = int(ParserStatus.RUNNING)
        rc = NativeParser.parse(file_path, token_id)
        if not rc:
            state.value = int(ParserStatus.FAILED)
            LOG.error(f"C++ run failed (returned {rc})")
        else:
            state.value = int(ParserStatus.DONE)
    except Exception as error:
        state.value = int(ParserStatus.FAILED)
        LOG.error(f"C++ run_worker_2pass failed: {error}")
    finally:
        # Emit one terminal wakeup after parse attempt completes.
        wakeup.signal()