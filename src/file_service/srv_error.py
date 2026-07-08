from __future__ import annotations


class FileServiceError(RuntimeError):
    pass


class WorkerSpawnError(FileServiceError):
    pass


class WorkerDiedError(FileServiceError):
    pass


class InvalidFileError(FileServiceError):
    pass


class MetadataNotFoundError(FileServiceError):
    pass
