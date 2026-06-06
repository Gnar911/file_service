from __future__ import annotations

from enum import IntEnum


class RecorderStatus(IntEnum):
    IDLE = 0
    RECORDING = 1
    PAUSED = 2
    STOPPED = 3
    FAILED = 4


class ParserStatus(IntEnum):
    IDLE = 0
    RUNNING = 1
    DONE = 2
    FAILED = 3


class DecodeStatus(IntEnum):
    IDLE = 0
    RUNNING = 1
    DONE = 2
    FAILED = 3


__all__ = [
    "RecorderStatus",
    "ParserStatus",
    "DecodeStatus",
]
