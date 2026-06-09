from __future__ import annotations

from pathlib import Path

from lw.logger_setup import LOG
from file_service.module import can_decoder_run

class NativeDecoder:
    pass


def decode_one_file(
    decode_db,
    decoder,
    db_file_path: str,
    record_mmap_path: Path,
) -> int:
    _ = decode_db
    token = str(Path(record_mmap_path))
    decode_error = can_decoder_run(token, decoder)
    rc = int(decode_error.rc)
    if rc != 0:
        LOG.error(
            "pybind can_decoder_run failed rc=%d error=%s db=%s token=%s",
            rc,
            getattr(decode_error, "error_message", ""),
            db_file_path,
            token,
        )
        return rc

    return 0