from __future__ import annotations

from file_service.api.status import RecorderStatus


# Backward-compatible alias to the public contract enum.
RecorderState = RecorderStatus

RECORDER_STATUS_IDLE = int(RecorderState.IDLE)
RECORDER_STATUS_RECORDING = int(RecorderState.RECORDING)
RECORDER_STATUS_PAUSED = int(RecorderState.PAUSED)
RECORDER_STATUS_STOPPED = int(RecorderState.STOPPED)
RECORDER_STATUS_FAILED = int(RecorderState.FAILED)

# Backward-compat aliases
RECORDER_STATUS_WRITE = RECORDER_STATUS_RECORDING
RECORDER_STATUS_STOP = RECORDER_STATUS_STOPPED

__all__ = [
    "RecorderState",
    "RECORDER_STATUS_IDLE",
    "RECORDER_STATUS_RECORDING",
    "RECORDER_STATUS_PAUSED",
    "RECORDER_STATUS_STOPPED",
    "RECORDER_STATUS_FAILED",
    "RECORDER_STATUS_WRITE",
    "RECORDER_STATUS_STOP",
]