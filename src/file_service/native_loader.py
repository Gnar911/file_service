from __future__ import annotations

import ctypes
from pathlib import Path


def _candidate_paths() -> list[Path]:
    this_file = Path(__file__).resolve()
    project_root = this_file.parents[2]
    package_dir = this_file.parent

    return [
        project_root / "build" / "file_service_cpp" / "libfile_service_core.so",
        project_root / "build" / "file_service_cpp" / "libnative_sdk_native.so",
        package_dir / "libfile_service_core.so",
        package_dir / "libnative_sdk_native.so",
    ]


def load_library() -> ctypes.CDLL:
    tried: list[str] = []

    for path in _candidate_paths():
        tried.append(str(path))
        if path.exists():
            return ctypes.CDLL(str(path))

    for name in ("libfile_service_core.so", "libnative_sdk_native.so", "file_service_core.so", "native_sdk_native.so"):
        tried.append(name)
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue

    searched = "\n".join(f"- {item}" for item in tried)
    raise FileNotFoundError(
        "Unable to locate file_service native shared library. Looked for:\n"
        f"{searched}"
    )
