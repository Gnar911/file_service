from file_service.parser.native.can_parser_api import CanParserLib as _CanParserLib
from file_service.parser.native.can_parser_api import DATA_STATUS_ERROR
from file_service.parser.native.can_parser_api import MmapData
from lw.logger_setup import LOG

### RUN on child process, binding class for native C++ parser
class NativeParser:
    _lib = _CanParserLib.get()._lib
    file_path: str

    @classmethod
    def _detect_format(cls, file_path: str) -> int | None:
        # Reuse the existing Python parser initialization for format probing.
        from file_service.parser.python.py_parser import LogParser

        return LogParser()._detect_format(file_path)

    @classmethod
    def get_status(cls, record_id=None, data_mmap_path: str | None = None) -> int:
        if data_mmap_path:
            try:
                with MmapData(str(data_mmap_path)) as mmap_data:
                    return int(mmap_data.status)
            except Exception as error:
                LOG.error("Parser mmap status read failed for %s: %s", record_id, error)
                return int(DATA_STATUS_ERROR)

        try:
            return int(cls._lib.get_status())
        except Exception as error:
            LOG.error(f"C++ get_status failed: {error}")
            return int(DATA_STATUS_ERROR)

    @classmethod
    def run_native_to_mmap(cls, file_path: str, data_path: str, index_path: str) -> bool:
        cls.file_path = file_path
        fmt = cls._detect_format(file_path)
        if fmt is None:
            LOG.warning("No parser detected for file format")
            return False
        
        try:
            _lib = _CanParserLib.get()._lib
            rc = _lib.can_parser_run_worker_segmented(
                file_path.encode("utf-8"),
                data_path.encode("utf-8"),
                index_path.encode("utf-8"),
                fmt,
            )
            return rc == 0
        except Exception as error:
            LOG.error(f"C++ segmented run failed: {error}")
            return False
        
    @classmethod
    def parse(cls, file_path: str, data_mmap_path: str, index_mmap_path: str) -> bool:
        return cls.run_native_to_mmap(file_path, str(data_mmap_path), str(index_mmap_path))