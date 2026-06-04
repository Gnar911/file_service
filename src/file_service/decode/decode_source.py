from __future__ import annotations

import pickle
from pathlib import Path
from lw.logger_setup import LOG
from file_service.decode.native.can_decoder_api import DecodeDB

from file_service.decode.dbc_manager import CANDBManager
from file_service.decode.native.native_decoder import decode_one_file


def _load_or_build_candb(db_file_path: str, dbc_pkl_path: str):
    target_pkl_path = Path(dbc_pkl_path)
    candb_info = _load_candb_from_path(target_pkl_path)
    if candb_info is not None:
        return candb_info

    manager = CANDBManager()
    parsed = manager.load_database(str(db_file_path))
    if parsed is None:
        LOG.warning("load_database failed for %s", db_file_path)
        return None

    candb_info = manager.candb_dict.get(str(db_file_path))
    if candb_info is None:
        LOG.warning("No candb info after load_database: %s", db_file_path)
        return None

    try:
        target_pkl_path.parent.mkdir(parents=True, exist_ok=True)
        with target_pkl_path.open("wb") as pkl_file:
            pickle.dump(candb_info, pkl_file, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        LOG.exception("Failed to dump record-owned DBC pkl: %s", target_pkl_path)

    return candb_info


def _load_candb_from_path(pkl_path: str | Path):
    path = Path(pkl_path)
    if not path.exists():
        LOG.warning("DBC pkl not found: %s", path)
        return None

    try:
        with path.open("rb") as pkl_file:
            candb_info = pickle.load(pkl_file)
        LOG.info("Loaded DBC pkl: %s", path)
        return candb_info
    except Exception:
        LOG.exception("Failed to load DBC pkl: %s", path)
        return None


def decode_process(
    record_mmap_path: Path,
    db_file_path: str,
    wakeup,
    dbc_pkl_path: str,
) -> bool:
    """Child-process entry. Decodes one explicit record mmap base path."""

    try:
        if not dbc_pkl_path:
            LOG.warning("Missing repository-owned dbc_pkl_path for %s - cannot decode", db_file_path)
            return False

        candb_info = _load_or_build_candb(db_file_path, dbc_pkl_path)

        if candb_info is None:
            LOG.warning("DBC info unavailable for %s - cannot decode", db_file_path)
            return False

        decode_db = DecodeDB.load(candb_info)
        LOG.info("Decode DB loaded from pkl: %s", Path(db_file_path).stem)

        return bool(
            decode_one_file(
            decode_db=decode_db,
            db_file_path=db_file_path,
            record_mmap_path=record_mmap_path,
            )
        )
    finally:
        wakeup.signal()