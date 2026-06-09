"""Writer process entry point for mmap recording."""

from __future__ import annotations

from file_service.recorder.rcd_process import RecorderProcess

"""
Option A — Metadata outside the shared memory (your mp.Value approach)
SharedMemory:
+---------+
| frame 0 |
+---------+
| frame 1 |
+---------+
| frame N |
+---------+

Shared:
write_idx = mp.Value(...)

Pros

Ring buffer contains only payload.
No need to reserve header bytes.
Writer can update write_idx atomically through a synchronized primitive.
Easier to share additional metadata (write_idx, read_idx, overflow count, state flags) without redesigning the memory layout.
Cleaner separation between data and control information.

Cons

Reader must receive both:
shm name
write_idx handle/reference
Doesn't work if another process only knows the shm name.
"""
def writer_process(
    shm_name: str,
    base_path: str,
    stop_event,
    wakeup,
    state,
):
    RecorderProcess(
        shm_name=shm_name,
        base_path=base_path,
        stop_event=stop_event,
        wakeup=wakeup,
        state=state,
    ).run()