from __future__ import annotations

from lw.logger_setup import LOG
from file_service.module import CanDatabaseModel, can_decoder_run
from file_service.metadata_id import LogId

class NativeDecoder:
    pass


def decode_one_file(
    model: CanDatabaseModel,
    record_mmap_path: LogId,
) -> int:
    token = str(record_mmap_path)
    decode_error = can_decoder_run(token, model)
    rc = int(decode_error.rc)
    if rc != 0:
        LOG.error(
            "pybind can_decoder_run failed rc=%d error=%s token=%s",
            rc,
            getattr(decode_error, "error_message", ""),
            token,
        )
        return rc

    return 0