from __future__ import annotations

from os.path import isfile

from lw.logger_setup import LOG

from .parser.python.py_parser import LogParser


class CANLogVerification:
    def is_supported_log_file(self, file_path: str) -> bool:
        normalized = str(file_path)
        if not isfile(normalized):
            LOG.info("Input file path is invalid: %s", normalized)
            return False

        parser_instance = LogParser()
        if parser_instance._detect_format(normalized) is None:
            LOG.info("Unsupported CAN log file format: %s", normalized)
            return False
        
        return True

    def verify_log_file(self, file_path: str) -> bool:
        return self.is_supported_log_file(file_path)
