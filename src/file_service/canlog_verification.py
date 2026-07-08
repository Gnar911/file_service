from __future__ import annotations

from os.path import isfile

from lw.logger_setup import LOG
from file_service.parser.py_parser import LogParser
from file_service.module.fs_core import FormatType
from lw.singleton import SingletonMeta

class CANLogVerification(metaclass=SingletonMeta):
    # @staticmethod
    # def is_supported_log_file(file_path: str) -> bool:

    #     """ NOTE: No need to pass the file not existed error since this is more like the programatic error, just let it raise FileNotFoundError"""
    #     # normalized = str(file_path)
    #     # if not isfile(normalized):
    #     #     LOG.info("Input file path is invalid: %s", normalized)
    #     #     return False

    #     # """ Using 2 detect function from python and C++ native"""
    #     # if LogParser()._detect_format(normalized) is None:
    #     #     LOG.info("Unsupported CAN log file format: %s", normalized)
    #     #     return False
        
    #     return True

    """ NOTE about iterable TextIO
    A TextIO is an abstraction over an open file object, not over "a file on disk". 
    An open file object generally cannot be pickled or sent to another process.
    """
    @staticmethod
    def verify_log_file(file_path: str) -> FormatType | 0:
        """ NOTE: No need to pass the file not existed error since this is more like the programatic error, 
                just let it raise FileNotFoundError"""
        parser = LogParser()
        # Try custom pattern detection first
        pid = parser.detect_custom_format(file_path)
        if pid == 0:
            # Try standard ASC reader detection
            pid = parser.detect_ASC_format_standard(file_path)
        if pid == 0:
            # Try BLF reader detection
            pid = parser.detect_BLF_format_standard(file_path)
        if pid == 0:
            LOG.info("Unsupported CAN log file format: %s", file_path)
            return 0
        return pid

    @staticmethod
    def verify_log_line(text_l: str) -> FormatType | 0:
        return LogParser().detect_pattern(text_l)
