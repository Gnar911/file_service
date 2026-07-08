import multiprocessing as mp

from lw.platform.linux_platform import _set_linux_process_name
from file_service.metadata_id import LogId
# from file_service.qt_object import IPCWakeup
from lw.status_channel import StatusChannel
from file_service.parser.native_parser import NativeParser
from lw.logger_setup import LOG
from file_service.status import ParserStatus

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
        rc = NativeParser.parse(file_path, log_id.path_token())
        if not rc:
            state.mc_send(int(ParserStatus.FAILED))
            LOG.error(f"C++ run failed (returned {rc})")
        else:
            state.mc_send(int(ParserStatus.DONE))
    except Exception as error:
        state.mc_send(int(ParserStatus.FAILED))
        LOG.error(f"C++ run_worker_2pass failed: {error}")
    finally:
        # StatusChannel.mc_send() performed wakeup.
        return