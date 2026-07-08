import re
from typing import Dict
from itertools import islice
from can import ASCReader, BLFReader
from .file_loaders import FileLoaderMixin
from .format_detector import FormatDetector
from .python.line_parsers import LineParserMixin
from .native_parser import NativeParser
from file_service.module.fs_core import FormatType
from .define import (
    PATTERN_CANCMD,
    PATTERN_CANCMD_TYPE2,
    PATTERN_CANCMD_TYPE3,
    PATTERN_CANCMD_TYPE4,
    PATTERN_CANOE,
    PATTERN_CANOE_COMPACT,
    PATTERN_CANOE_SYMBOLIC,
    PATTERN_CANSK,
    PATTERN_FILTER,
    PATTERN_BLF,
)
from lw.singleton import SingletonMeta

# Single source of truth for regexes used for detection
PATTERNS: Dict[FormatType, re.Pattern[str]] = {
    FormatType.CANOE: re.compile(PATTERN_CANOE),
    FormatType.CANOE_FULL: re.compile(PATTERN_CANOE_SYMBOLIC, re.IGNORECASE | re.VERBOSE),
    FormatType.CANOE_CMP: re.compile(PATTERN_CANOE_COMPACT, re.IGNORECASE | re.VERBOSE),
    FormatType.CANCMD: re.compile(PATTERN_CANCMD, re.IGNORECASE | re.VERBOSE),
    FormatType.FILTER: re.compile(PATTERN_FILTER, re.IGNORECASE | re.VERBOSE),
    FormatType.CANSUKE: re.compile(PATTERN_CANSK, re.IGNORECASE | re.VERBOSE),
    FormatType.CANCMD_T2: re.compile(PATTERN_CANCMD_TYPE2, re.IGNORECASE | re.VERBOSE),
    # include TYPE3 and TYPE4 under the same detection id
    FormatType.CANCMD_T3: re.compile(
        f"(?:{PATTERN_CANCMD_TYPE3})|(?:{PATTERN_CANCMD_TYPE4})",
        re.IGNORECASE | re.VERBOSE,
    ),
    # BLF (binary log format) may have a textual header pattern
    FormatType.BLF: re.compile(PATTERN_BLF, re.IGNORECASE | re.VERBOSE),
    FormatType.ASC: re.compile(
    r"\d+\.\d+\s+(\d+\s+(\w+\s+(Tx|Rx)|ErrorFrame)|CANFD)",
    re.ASCII | re.IGNORECASE,
    ),
}

""" NOTE: Using the custom python parser + REGEX + standard can library format API"""
class LogParser(LineParserMixin, FormatDetector, NativeParser, FileLoaderMixin, metaclass=SingletonMeta):
    def __init__(self):
        self._ws_re = re.compile(r"\s+")

    def detect_pattern(self, line: str) -> FormatType:
        for pid, regex in PATTERNS.items():
            if regex.match(line):
                return pid
        return FormatType.UNKNOWN
    
    """ NOTE: API for detect a can log file with custom pattern"""
    def detect_custom_format(self, file_path: str) -> FormatType:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
            # Scan up to 10 lines to find a matching pattern; return first match
            for line in islice(file_handle, 10):
                pid = self.detect_pattern(line)
                if pid != FormatType.UNKNOWN:
                    return pid
            return FormatType.UNKNOWN

    def detect_ASC_format_standard(self, file_path: str) -> FormatType:
        reader = ASCReader(file_path)
        # iterate up to 10 items to check for any content
        cnt = sum(1 for _ in islice(reader, 10))
        return FormatType.ASC if cnt > 0 else FormatType.UNKNOWN

    def detect_BLF_format_standard(self, file_path: str) -> FormatType:
        reader = BLFReader(file_path)
        cnt = sum(1 for _ in islice(reader, 10))
        return FormatType.BLF if cnt > 0 else FormatType.UNKNOWN