from file_service.module.fs_core import *
from lw.logger_setup import LOG

### RUN on child process, binding class for native C++ parser
class NativeParser:
    file_path: str

    @classmethod
    def _detect_format(cls, file_path: str) -> int | None:
        # Reuse the existing Python parser initialization for format probing.
        from file_service.parser.python.py_parser import LogParser

        return LogParser()._detect_format(file_path)

    @classmethod
    def run_native_to_mmap(cls, file_path: str, token_id: str) -> bool:
        cls.file_path = file_path
        fmt = cls._detect_format(file_path)
        if fmt is None:
            LOG.warning("No parser detected for file format")
            return False

        try:
            rc = run_worker_segmented(file_path, str(token_id), int(fmt))
            return rc == 0
        except Exception as error:
            LOG.error(f"C++ segmented run failed: {error}")
            return False

    @classmethod
    def parse(cls, file_path: str, token_id: str) -> bool:
        return cls.run_native_to_mmap(file_path, str(token_id))