from .fs_core import (
    CanDatabaseModel,
    CanDecoder,
    DecodedSignal,
    LogQuery,
    MessageDef,
    MetaDataStorageInterface,
    ParsedEntry,
    LogRecord,
    SignalDef,
    abi_version,
    can_decoder_run,
)

EXPECTED_CORE_ABI_VERSION = 8

_loaded_abi = int(abi_version())
if _loaded_abi != EXPECTED_CORE_ABI_VERSION:
    raise RuntimeError(
        f"ABI version mismatch: fs_core expects {EXPECTED_CORE_ABI_VERSION}, got {_loaded_abi}"
    )

__all__ = [
    "CanDatabaseModel",
    "CanDecoder",
    "DecodedSignal",
    "EntryUpdate",
    "FormatType",
    "LogQuery",
    "MessageDef",
    "MetaDataStorageInterface",
    "LogRecord",
    "ParsedEntry",
    "SignalDef",
    "abi_version",
    "can_decoder_run",
]