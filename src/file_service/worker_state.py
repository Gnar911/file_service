from __future__ import annotations

from file_service.status import ParserStatus


# Backward-compatible alias to parser worker status in the public contract.
WorkerTaskState = ParserStatus


__all__ = ["WorkerTaskState"]
