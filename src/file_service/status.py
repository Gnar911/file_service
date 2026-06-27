from __future__ import annotations

from enum import IntEnum




class RecorderStatus(IntEnum):
    WRITE_BATCH = 1 # Write state
    PAUSED = 2
    WAIT_RING = 3 # Wait state, right after start 
    STOPPED = 4 # Stop, end service after that

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
