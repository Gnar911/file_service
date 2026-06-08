from .parsed_mmap import ParsedEntry, ParsedMmapInterface, abi_version

EXPECTED_CORE_ABI_VERSION = 7

_loaded_abi = int(abi_version())
if _loaded_abi != EXPECTED_CORE_ABI_VERSION:
    raise RuntimeError(
        f"ABI version mismatch: parsed_mmap expects {EXPECTED_CORE_ABI_VERSION}, got {_loaded_abi}"
    )

__all__ = [
    "ParsedEntry",
    "ParsedMmapInterface",
    "abi_version",
]