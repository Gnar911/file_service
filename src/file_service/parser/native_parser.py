from file_service.module.fs_core import *
from lw.logger_setup import LOG

### RUN on child process, binding class for native C++ parser
class NativeParser:
    file_path: str

    @classmethod
    def run_native_to_mmap(cls, file_path: str, token_id: str) -> bool:
        cls.file_path = file_path
        # Use unified enum detection and only forward native-supported formats.
        from file_service.parser.py_parser import LogParser

        fmt = LogParser().detect_custom_format(file_path)
        if fmt in (FormatType.UNKNOWN, FormatType.ASC, FormatType.BLF):
            fmt = None
        else:
            fmt = int(fmt)
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
    
