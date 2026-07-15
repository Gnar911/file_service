import multiprocessing as mp

from lw.platform.linux_platform import _set_linux_process_name
from file_service.metadata_id import LogId
# from file_service.qt_object import IPCWakeup
from lw.status_channel import StatusChannel
from file_service.parser.native_parser import NativeParser
from lw.logger_setup import LOG
from file_service.status import ParserStatus
from file_service.module.fs_core import *

def run_parser_async(
    file_path: str,
    log_id: LogId,
    state: StatusChannel,
) -> mp.Process:
    proc = mp.Process(
            target=run_parse,
            args=(
                file_path,
                log_id,
                state,
            ),
            daemon=True,
            name="CBCM-parser",
        )

    proc.start()
    return proc

def run_parse(file_path: str, log_id: LogId, state: StatusChannel) -> None:
    _set_linux_process_name("CBCM-parser")
    try:
        LOG.info(f"run_worker_segmented")
        #rc = NativeParser.parse(file_path, log_id.path_token())
        rc = run_worker_segmented(file_path, log_id.path_token())
        if rc:
            LOG.error(f"C++ run failed (returned {rc})")
            state.mc_send(int(ParserStatus.FAILED))
        else:
            LOG.info(f"Parse done")
            state.mc_send(int(ParserStatus.DONE))
    except Exception as error:
        LOG.error(f"C++ run_worker_2pass failed: {error}")
        state.mc_send(int(ParserStatus.FAILED))
    finally:
        # StatusChannel.mc_send() performed wakeup.
        return