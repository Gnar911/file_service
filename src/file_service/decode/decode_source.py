from __future__ import annotations

import pickle
from pathlib import Path
from typing import Dict

from lw.logger_setup import LOG
from file_service.module import CanDatabaseModel, MessageDef, SignalDef
from file_service.metadata_id import LogId

from file_service.decode.dbc_manager import CANDBManager
from file_service.decode.native.native_decoder import decode_one_file


def _build_decoder_defs(candb_info) -> tuple[list[MessageDef], list[SignalDef], Dict[int, int]]:
    messages: list[MessageDef] = []
    signals: list[SignalDef] = []
    msg_sig_count: Dict[int, int] = {}

    sig_offset = 0
    for msg in candb_info.db.messages:
        sorted_sigs = sorted(msg.signals, key=lambda s: s.name)

        message = MessageDef()
        message.can_id = int(msg.frame_id)
        message.signal_count = int(len(sorted_sigs))
        message.msg_length = int(msg.length)
        message.signal_offset = int(sig_offset)
        message.padding = 0
        messages.append(message)

        msg_sig_count[int(msg.frame_id)] = int(len(sorted_sigs))

        for sig in sorted_sigs:
            signal = SignalDef()
            signal.start_bit = int(sig.start)
            signal.bit_length = int(sig.length)
            signal.byte_order = 0 if sig.byte_order == "little_endian" else 1
            signal.is_signed = 1 if sig.is_signed else 0
            signal.has_choices = 1 if sig.choices else 0
            signal.padding1 = 0
            signal.scale = float(sig.scale) if sig.scale else 1.0
            signal.offset = float(sig.offset) if sig.offset else 0.0
            signals.append(signal)

        sig_offset += len(sorted_sigs)

    return messages, signals, msg_sig_count


def _load_decode_db_with_pybind(candb_info) -> CanDatabaseModel:
    messages, signals, _ = _build_decoder_defs(candb_info)
    model = CanDatabaseModel()
    model.messages = messages
    model.signals = signals
    model.canid_to_msg = {int(msg.can_id): int(idx) for idx, msg in enumerate(messages)}
    return model


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
    record_mmap_path: LogId,
    wakeup,
    dbc_pkl_path: str,
) -> int:
    """Child-process entry. Decodes one explicit record mmap base path."""

    try:
        # if not dbc_pkl_path:
        #     LOG.warning("Missing repository-owned dbc_pkl_path - cannot decode")
        #     return -101

        #candb_info = _load_or_build_candb("", dbc_pkl_path)
        candb_info = _load_candb_from_path(dbc_pkl_path)

        if candb_info is None:
            #LOG.warning("DBC info unavailable - cannot decode")
            return -102

        try:
            decode_model = _load_decode_db_with_pybind(candb_info)
        except Exception:
            #LOG.exception("Failed to load decode DB")
            return -103

        #LOG.info("Decode DB loaded with pybind CanDecoder")
        return int(
            decode_one_file(
                model=decode_model,
                record_mmap_path=record_mmap_path,
            )
        )
    except Exception:
        LOG.exception("Unhandled decode_process failure for log_id=%s", record_mmap_path)
        return -199
    finally:
        wakeup.signal()