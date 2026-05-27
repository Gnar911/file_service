import re
from typing import Callable, Dict, List, Optional, Tuple

from can_sdk.data_object import CANLogFile, CANLogLine

from ..file_loaders import FileLoaderMixin
from ..format_detector import FormatDetector
from .line_parsers import LineParserMixin
from ..native.native_parser import NativeParser
from ..define import (
    BATCH_SIZE,
    CANFD_DLC_MAP,
    PAGE_SIZE,
    PATTERN_BLF,
    PATTERN_CANCMD,
    PATTERN_CANCMD_TYPE2,
    PATTERN_CANCMD_TYPE3,
    PATTERN_CANCMD_TYPE4,
    PATTERN_CANOE,
    PATTERN_CANOE_COMPACT,
    PATTERN_CANOE_SYMBOLIC,
    PATTERN_CANSK,
    PATTERN_FILTER,
    WORKER_ERR_CAPACITY_OVERFLOW,
    get_file_type,
    get_length_from_dlc,
)

class LogParser(LineParserMixin, FormatDetector, NativeParser, FileLoaderMixin):
    def __init__(self):
        self.pattern_parsers: List[Tuple[re.Pattern, Callable]] = [
            (PATTERN_CANOE, self._parse_canoe),
            (re.compile(PATTERN_CANOE_SYMBOLIC, re.IGNORECASE | re.VERBOSE), self._parse_canoe_full),
            (re.compile(PATTERN_CANOE_COMPACT, re.IGNORECASE | re.VERBOSE), self._parse_canoe_compact),
            (re.compile(PATTERN_CANCMD, re.IGNORECASE | re.VERBOSE), self._parse_cancommander),
            (re.compile(PATTERN_FILTER, re.IGNORECASE | re.VERBOSE), self._parse_filter_log),
            (re.compile(PATTERN_CANSK, re.IGNORECASE | re.VERBOSE), self._parse_cansuke),
            (re.compile(PATTERN_CANCMD_TYPE2, re.IGNORECASE | re.VERBOSE), self._parse_cancommander_type2),
            (re.compile(PATTERN_CANCMD_TYPE3, re.IGNORECASE | re.VERBOSE), self._parse_cancommander_type3),
            (re.compile(PATTERN_CANCMD_TYPE4, re.IGNORECASE | re.VERBOSE), self._parse_cancommander_type3),
        ]
        self.info_one_time = False
        self.detected_parser: Optional[Callable] = None
        self.last_raw_by_id: Dict[int, str] = {}
        self.last_timestamp_by_id: Dict[int, float] = {}
        self._ws_re = re.compile(r"\s+")
        self._HEX_CHARS = frozenset("0123456789abcdefABCDEF")
        self._VALID_DLC = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64})
        self._FMT_MAP: Dict[Callable, int] = {
            self._parse_canoe: 1,
            self._parse_canoe_full: 2,
            self._parse_canoe_compact: 3,
            self._parse_cancommander: 4,
            self._parse_filter_log: 5,
            self._parse_cansuke: 6,
            self._parse_cancommander_type2: 7,
            self._parse_cancommander_type3: 8,
        }