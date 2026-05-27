from __future__ import annotations

import os
from pathlib import Path
from lw.logger_setup import LOG
from native_sdk.can_decoder_api import DecodeDB

from file_service.decode.native.native_decoder import decode_one_file
from file_service.repository.file_handler.dbc_pkl_handler import DBCPklHandler
_DBC_PKL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps", "dbc_pkl")

_dbc_pkl_handler = DBCPklHandler(_DBC_PKL_DIR)


        
def decode_process(
    record_mmap_path: Path,
    db_file_path: str,
    wakeup,
) -> None:
    """Child-process entry. Decodes one explicit record mmap base path."""

    try:
        candb_info = _dbc_pkl_handler.load(db_file_path)
        if candb_info is None:
            LOG.warning("pkl not found for %s — cannot decode", db_file_path)
            return

        decode_db = DecodeDB.load(candb_info)
        LOG.info("Decode DB loaded from pkl: %s", Path(db_file_path).stem)

        decode_one_file(
            decode_db=decode_db,
            db_file_path=db_file_path,
            record_mmap_path=record_mmap_path,
        )
    finally:
        wakeup.signal()