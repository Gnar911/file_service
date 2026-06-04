from __future__ import annotations

from enum import IntEnum


class RecorderState(IntEnum):
    IDLE = 0
    WRITE = 1
    STOP = 2

RECORDER_STATUS_IDLE = int(RecorderState.IDLE)
RECORDER_STATUS_WRITE = int(RecorderState.WRITE)
RECORDER_STATUS_STOP = int(RecorderState.STOP)

__all__ = [
    "RecorderState",
    "RECORDER_STATUS_IDLE",
    "RECORDER_STATUS_WRITE",
    "RECORDER_STATUS_STOP",
]