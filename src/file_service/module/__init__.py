from .fs_core import (
    CanDecoder,
    DecodedSignal,
    MessageDef,
    ParsedEntry,
    ParsedMmapInterface,
    SignalDef,
    abi_version,
    can_decoder_run,
)

EXPECTED_CORE_ABI_VERSION = 7

_loaded_abi = int(abi_version())
if _loaded_abi != EXPECTED_CORE_ABI_VERSION:
    raise RuntimeError(
        f"ABI version mismatch: fs_core expects {EXPECTED_CORE_ABI_VERSION}, got {_loaded_abi}"
    )

__all__ = [
    "CanDecoder",
    "DecodedSignal",
    "MessageDef",
    "ParsedEntry",
    "ParsedMmapInterface",
    "SignalDef",
    "abi_version",
    "can_decoder_run",
]