import re
import ctypes
from typing import Optional, List, Callable, Tuple, Dict
from can_sdk.data_object import CANLogLine, CANLogFile
import logging
import pandas as pd
from lw.logger_setup import LOG, setup_logger
from can import ASCReader, LogReader, BLFReader
import os
from pathlib import Path
from typing import Any, Final
from file_service.parser.native.native_bridge import CanParserLib as _CanParserLib  # type: ignore[import-not-found]
from native_sdk._loader import load_library  # type: ignore[import-not-found]

PAGE_SIZE          = 10_000
BATCH_SIZE = PAGE_SIZE
WORKER_ERR_CAPACITY_OVERFLOW = -8

PATTERN_FILTER = (
    r"([\d\.]+)\s+\S+\s+\d+\s+"
    r"(Tx|Rx)\s+"
    r"([0-9a-fA-F]{1,8})\s+"
    r"(\S+)\s+"
    r"(\d{1,2})\s+"
    r"((?:[0-9a-fA-F]{2}(?:\s+|$)){1,64})"
)
#PATTERN_FILTER = r"([\d\.]+).+(Tx|Rx)\s+([0-9a-fA-F]{1,8})\s+(\S+)\s+(\d |\d\d)\s+(\s[0-9a-fA-F]{2})+"
# PATTERN_CANOE: Final = re.compile(
#     r"([\d\.]+).+(Tx|Rx)\s+([0-9a-fA-F]{1,8})\s+(\S+)?\s+(\S\s)+( \d|\d\d)(\s[0-9a-fA-F]{2})+"
#     , re.ASCII | re.IGNORECASE)
PATTERN_CANOE = re.compile(
    r"([\d\.]+)"                 # (1) Timestamp: digits and dots, e.g. 123.456
    r".+"                        # (2) ANY chars (greedy!) until later parts match
    r"(Tx|Rx)\s+"                # (3) Direction: Tx or Rx
    r"([0-9a-fA-F]{1,8})\s+"     # (4) CAN ID: 1–8 hex digits
    r"(\S+)?"                    # (5) Optional label (single non-space token)
    r"\s+"                       # (6) Whitespace separator
    r"(\S\s)+"                   # (7) Repeating: NON-space + space (very loose!)
    r"( \d|\d\d)"                # (8) DLC: either " <digit>" OR "<two digits>"
    r"(\s[0-9a-fA-F]{2})+"       # (9) Data bytes: space + 2 hex digits, repeated
    ,
    re.ASCII | re.IGNORECASE
)
PATTERN_CANSK = r"([\d\.]+)\s+\d\s+([0-9a-fA-F]{1,8})\s+(Tx|Rx)\s+(\S+)\s+(\d |\d\d)\s+(\s[0-9a-fA-F]{2})+"
#PATTERN_CANCMD = r"([\d-]+)\s+([\d:\.]+)\s+([\d\.]+)\s+\d\s+\S+\s+\d\s+([0-9a-fA-F]{1,8})\s+(Tx|Rx)\s+(\S+)\s+\S\s+(\d |\d\d)\s+(\s[0-9a-fA-F]{2})+"
#re.IGNORECASE | re.VERBOSE)
PATTERN_BLF = (
    r"([\d\.]+)\s+"              # Timestamp
    r"\S+\s+"                    # Bus type (e.g., CANFD)
    r"\d+\s+"                    # Channel number
    r"(Tx|Rx)\s+"                # Direction
    r"([0-9a-fA-F]{1,8})\s+"     # CAN ID
    r"\S+\s+"                    # Label (e.g., NOT_FOUND)
    r"(\d{1,2})\s+"              # Data length (1-2 digits)
    r"((?:[0-9a-fA-F]{2}\s*)+)"  # Data bytes
)

PATTERN_CANCMD = (
    r"([\d-]+)\s+"                 # 1) date
    r"([\d:\.]+)\s+"               # 2) time
    r"([\d\.]+)\s+"                # 3) timestamp
    r"\d\s+"                       # 4) ?
    r"\S+\s+"                      # 5) ?
    r"\d\s+"                       # 6) ?
    r"([0-9A-Fa-f]{1,8})\s+"       # 7) CAN ID
    r"(Tx|Rx)\s+"                  # 8) direction
    r"(\S+)\s+\S\s+"               # 9) name + skip one field
    r"(\d |\d\d)\s+"               # 10) LEN (either “digit space” or “two digits”)
    r"(\s[0-9A-Fa-f]{2})+"         # 11) data bytes (one or more “ XX”)
)

PATTERN_CANCMD_TYPE2 = (
    r"(\d+)\s+"                    # 0) timediff  — integer (e.g. 12399)
    r"(\d)\s+"                     # 3) flags/bus/whatever (captured now)
    r"([0-9A-Fa-f]{1,8})\s+"       # 4) CAN ID
    r"(?:([^\s]+)\s+)?"            # 4) OPTIONAL message name
    r"([0-9A-Fa-f])\s+"            # <-- exactly 1 hex character here
    r"((?:[0-9A-Fa-f]{2}\s+)+)"    # 7) data bytes (at least one hex byte)
    r"(Tx|Rx)\s+"                  # 8) direction
    r"(\S+)\s+"                    # 10) e.g. CANFD
    r"(\d)\s*"                     # 11) another digit (e.g. channel)
)

PATTERN_CANCMD_TYPE3 = (
    r"(\d+)\s+"                    # 0) timediff  — integer (e.g. 12399)
    r"(\d)\s+"                     # 3) flags/bus/whatever (captured now)
    r"([0-9A-Fa-f]{1,8})\s+"       # 4) CAN ID
    r"([0-9A-Fa-f])\s+"            # <-- exactly 1 hex character here
    r"((?:[0-9A-Fa-f]{2}\s+)+)"    # 7) data bytes (at least one hex byte)
    r"(Tx|Rx)\s+"                  # 8) direction
    r"(\S+)\s+"                    # 10) e.g. CANFD
    r"(\d)\s*"                     # 11) another digit (e.g. channel)
)

PATTERN_CANCMD_TYPE4 = (
    r"([\d-]+)\s+"                 # 1) date
    r"([\d:\.]+)\s+"               # 2) time
    r"(\d)\s+"                     # 3) flags/bus/whatever (captured now)
    r"([0-9A-Fa-f]{1,8})\s+"       # 4) CAN ID
    r"(?:([^\s]+)\s+)? "           # 4) OPTIONAL message name
    r"(\d{1,2})\s+"                # 6) DLC
    r"((?:[0-9A-Fa-f]{2}\s+)+)"    # 7) data bytes (at least one hex byte)
    r"(Tx|Rx)\s+"                  # 8) direction
    r"(\S+)\s+"                    # 10) e.g. CANFD
    r"(\d)\s*"                     # 11) another digit (e.g. channel)
)

PATTERN_CANOE_SYMBOLIC = r"""
^
\d{4}-\d{2}-\d{2}                 # date
\s+\d{2}:\d{2}:\d{2}\.\d+         # time
\s+\d+\.\d+                       # timestamp
\s+\d+\s+CANFD\s+\d+              # bus + CANFD + channel
\s+(?P<id>[0-9A-Fa-f]{1,8})        # CAN ID
\s+(?P<dir>Tx|Rx)                 # direction
.*?                               # message name / columns (ignore)
(?P<data>(?:\s[0-9A-Fa-f]{2})+)   # data bytes
$
"""

PATTERN_CANOE_COMPACT = r"""
^
(?P<ts>\d+\.\d+)                 # timestamp
\s+(?P<channel>\d+)              # channel / bus
\s+(?P<id>[0-9A-Fa-f]{1,8})       # CAN ID (hex)
\s+(?P<dir>Tx|Rx)                # direction
\s+[dD]\s+                       # frame type
(?P<dlc>\d{1,2})                 # DLC
(?P<data>(?:\s+[0-9A-Fa-f]{2})+) # payload
$
"""

def get_file_type(file_path: str) -> str:
    """
    Determine the file type based on its extension.

    Supported types:
        - Excel (.xls, .xlsx)
        - CSV (.csv)
        - ZIP (.blf)
        - ASC (.asc)
        - Unknown (if not matched)

    Returns:
        A string representing the file type.
    """
    extension = os.path.splitext(file_path.lower())[1]

    if extension in (".xls", ".xlsx"):
        return "excel"
    elif extension == ".csv":
        return "csv"
    elif extension == ".blf":
        return "blf"
    elif extension == ".asc":
        return "asc"
    elif extension == ".log":
        return "log"
    elif extension == ".txt":
        return "txt"
    else:
        return "unknown"
        
CANFD_DLC_MAP = {
"0": 0,
"1": 1,
"2": 2,
"3": 3,
"4": 4,
"5": 5,
"6": 6,
"7": 7,
"8": 8,
"9": 12,
"A": 16,
"B": 20,
"C": 24,
"D": 32,
"E": 48,
"F": 64,
}

def get_length_from_dlc(dlc:int) -> int:
        dlc_to_len = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
        return dlc_to_len[min(dlc, len(dlc_to_len) - 1)]

class LogParser():
    def __init__(self):
        self.pattern_parsers: List[Tuple[re.Pattern, Callable]] = [
            (PATTERN_CANOE, self._parse_canoe),
            (re.compile(PATTERN_CANOE_SYMBOLIC, re.IGNORECASE | re.VERBOSE), self._parse_canoe_full),
            (re.compile(PATTERN_CANOE_COMPACT, re.IGNORECASE | re.VERBOSE), self._parse_canoe_compact),
            (re.compile(PATTERN_CANCMD, re.IGNORECASE | re.VERBOSE), self._parse_cancommander), # No channel
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
        # precompile whitespace normalizer for optional use
        self._ws_re = re.compile(r"\s+")
        self._HEX_CHARS = frozenset("0123456789abcdefABCDEF")
        self._VALID_DLC = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64})

        # Map detected Python parser function → C++ FormatType enum (1..8)
        self._FMT_MAP: Dict[Callable, int] = {
            self._parse_canoe:              1,   # FMT_CANOE
            self._parse_canoe_full:         2,   # FMT_CANOE_FULL
            self._parse_canoe_compact:      3,   # FMT_CANOE_CMP
            self._parse_cancommander:       4,   # FMT_CANCMD
            self._parse_filter_log:         5,   # FMT_FILTER
            self._parse_cansuke:            6,   # FMT_CANSUKE
            self._parse_cancommander_type2: 7,   # FMT_CANCMD_T2
            self._parse_cancommander_type3: 8,   # FMT_CANCMD_T3  (type4 also maps here)
        }

    def _detect_format(self, file_path: str) -> Optional[int]:
        """Read first lines to detect log format via regex. Returns C++ FormatType int (1..8) or None."""
        self.detected_parser = None
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f, start=1):
                    self._various_parse_line_test(line, i)
                    if self.detected_parser:
                        return self._FMT_MAP.get(self.detected_parser)
        except Exception:
            pass
        return None

    # ── NEW: Detect field positions by tokenising the first matched line ──
    def _detect_field_layout(self, file_path: str):
        """Read first lines, detect format, tokenize first matched line,
        and return a FieldLayout struct with exact token positions.
        Returns FieldLayout or None."""
        from file_service.parser.native.native_bridge import FieldLayout, FL_TS_IS_MS, FL_DLC_IS_HEX, FL_IS_TAB

        VALID_DLC = frozenset({0,1,2,3,4,5,6,7,8,12,16,20,24,32,48,64})
        _HEX = frozenset('0123456789abcdefABCDEF')

        def _find_dlc_data(tokens, start):
            """Scan from *start* for the first token that is a valid DLC
            value (decimal, 0-64) whose next neighbour is a 2-char hex byte.
            Returns (dlc_idx, data_idx) or (None, None)."""
            for i in range(start, len(tokens) - 1):
                t = tokens[i]
                if len(t) <= 2 and t.isdigit():
                    val = int(t)
                    if val in VALID_DLC:
                        nxt = tokens[i + 1]
                        if len(nxt) == 2 and nxt[0] in _HEX and nxt[1] in _HEX:
                            return i, i + 1
            return None, None

        detected_func = None
        matched_raw = None
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    raw = line.rstrip('\n')
                    normalized = self._ws_re.sub(' ', raw.strip())
                    for pattern, func in self.pattern_parsers:
                        if pattern.match(normalized):
                            detected_func = func
                            matched_raw = raw
                            break
                    if detected_func:
                        break
        except Exception:
            return None

        if not detected_func or not matched_raw:
            return None

        tokens = matched_raw.split()

        # ── CANOE: ts[0] CANFD[1] chan[2] dir[3] id[4] [name] … DLC data … ──
        if detected_func == self._parse_canoe:
            dlc_idx, data_idx = _find_dlc_data(tokens, 5)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANOE: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=4, idx_dir=3,
                               idx_dlc=dlc_idx, idx_data=data_idx,
                               idx_chan=2, flags=0)

        # ── CANOE_FULL: date[0] time[1] ts[2] bus[3] CANFD[4] chan[5] id[6] dir[7] … ──
        if detected_func == self._parse_canoe_full:
            dlc_idx, data_idx = _find_dlc_data(tokens, 8)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANOE_FULL: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=2, idx_id=6, idx_dir=7,
                               idx_dlc=dlc_idx, idx_data=data_idx,
                               idx_chan=5, flags=0)

        # ── CANOE_COMPACT: ts[0] chan[1] id[2] dir[3] "d"[4] DLC data … ──
        if detected_func == self._parse_canoe_compact:
            dlc_idx, data_idx = _find_dlc_data(tokens, 4)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANOE_CMP: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=3,
                               idx_dlc=dlc_idx, idx_data=data_idx,
                               idx_chan=1, flags=0)

        # ── CANCMD: date[0] time[1] ts[2] ?[3] CANFD[4] ?[5] id[6] dir[7] … ──
        if detected_func == self._parse_cancommander:
            dlc_idx, data_idx = _find_dlc_data(tokens, 8)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANCMD: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=2, idx_id=6, idx_dir=7,
                               idx_dlc=dlc_idx, idx_data=data_idx,
                               idx_chan=-1, flags=0)

        # ── FILTER: ts[0] ?[1] ?[2] dir[3] id[4] name[5] DLC data … ──
        if detected_func == self._parse_filter_log:
            dlc_idx, data_idx = _find_dlc_data(tokens, 5)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout FILTER: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=4, idx_dir=3,
                               idx_dlc=dlc_idx, idx_data=data_idx,
                               idx_chan=-1, flags=0)

        # ── CANSUKE: ts[0] ?[1] id[2] dir[3] name[4] DLC data … ──
        if detected_func == self._parse_cansuke:
            dlc_idx, data_idx = _find_dlc_data(tokens, 4)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANSUKE: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=3,
                               idx_dlc=dlc_idx, idx_data=data_idx,
                               idx_chan=-1, flags=0)

        # ── CANCMD_T2 (TAB): ms[0] chan[1] id[2] name[3] hex_dlc[4] data[5] dir[6] ──
        if detected_func == self._parse_cancommander_type2:
            LOG.info("FieldLayout CANCMD_T2 (TAB)")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=6,
                               idx_dlc=4, idx_data=5, idx_chan=1,
                               flags=FL_TS_IS_MS | FL_DLC_IS_HEX | FL_IS_TAB)

        # ── CANCMD_T3: ms[0] chan[1] id[2] hex_dlc[3] data[4+] dir[-3] ──
        if detected_func == self._parse_cancommander_type3:
            LOG.info("FieldLayout CANCMD_T3")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=-3,
                               idx_dlc=3, idx_data=4, idx_chan=1,
                               flags=FL_TS_IS_MS | FL_DLC_IS_HEX)

        return None

    def _run_worker_2pass(
        self,
        file_path: str,
        data_path: str,
        index_path: str,
        fmt: int,
        max_can_ids: int = 4096,
    ) -> int:
        """Call the C++ two-pass worker.

        Pass 1 (C++): fast parallel row-count over the entire file.
        C++ then creates data.mmap and index.mmap with the exact capacity.
        Pass 2 (C++): parallel parse-and-write; each thread gets an exact
        slot range based on the pass-1 count — no overflow, no retry.
        """
        _lib = load_library()

        return _lib.can_parser_run_worker_2pass(
            file_path.encode("utf-8"),
            data_path.encode("utf-8") if data_path else b"",
            index_path.encode("utf-8") if index_path else b"",
            fmt,
            max_can_ids,
        )

    def run_native_to_mmap(self, file_path: str, data_path: str, index_path: str = "") -> bool:
        """Detect format in Python, then call C++ segmented mmap worker."""
        fmt = self._detect_format(file_path)
        if fmt is None:
            LOG.warning("No parser detected for file format")
            return False
        try:
            _lib = _CanParserLib.get()._lib
            if hasattr(_lib, "can_parser_run_worker_segmented"):
                rc = _lib.can_parser_run_worker_segmented(
                    file_path.encode("utf-8"),
                    data_path.encode("utf-8"),
                    index_path.encode("utf-8") if index_path else b"",
                    fmt,
                )
            else:
                LOG.warning("Segmented symbol missing in native SDK, fallback to 2-pass worker")
                rc = _lib.can_parser_run_worker_2pass(
                    file_path.encode("utf-8"),
                    data_path.encode("utf-8") if data_path else b"",
                    index_path.encode("utf-8") if index_path else b"",
                    fmt,
                    4096,
                )
            return rc == 0
        except Exception as e:
            LOG.error(f"C++ segmented run failed: {e}")
            return False

    def run_native_to_mmap_dummy(self, file_path: str, data_path: str, index_path: str = "") -> bool:
        """BENCHMARK: dummy loop — no parsing, just raw I/O + mmap write + hashmap overhead."""
        fmt = self._detect_format(file_path)
        if fmt is None:
            LOG.warning("No parser detected for file format")
            return False
        try:
            from native_sdk._loader import load_library  # type: ignore[import-not-found]
            _lib = load_library()
            if not hasattr(_lib, "can_parser_run_worker_dummy"):
                LOG.error("C++ dummy worker symbol is not available")
                return False
            rc = _lib.can_parser_run_worker_dummy(
                file_path.encode("utf-8"),
                data_path.encode("utf-8"),
                index_path.encode("utf-8") if index_path else b"",
                fmt,
                1000,
            )
            return rc == 0
        except Exception as e:
            LOG.error(f"C++ dummy run failed: {e}")
            return False

    """This function is for testing PATTERN"""
    def _various_parse_line_test(self, line: str, line_number: int):
        raw_line = line.rstrip("\n")
        normalized = self._ws_re.sub(' ', raw_line.strip())
        for pattern, func in self.pattern_parsers:
            """ Only using match regex for gate verify only, all the lines after will be consider to have this pattern"""
            match = pattern.match(normalized)
            if match:
                result = func(raw_line, line_number)
                #LOG.info(f"[Line {line_number}] Detected parser: {func.__name__}")
                self.detected_parser = func
                return result
            return None
    
    def _is_detected_parser(self, line: str, line_number: int) -> bool:
        normalized = self._ws_re.sub(' ', line.strip())
        for pattern, func in self.pattern_parsers:
            match = pattern.match(normalized)
            if match:
                LOG.info(f"[Line {line_number}] Detected parser: {func.__name__}")
                return True
        return False

    def _create_log_entry(self, 
                        line_number: int, 
                        timestamp: float, 
                        can_id_hex: str,
                        direction: str, 
                        data_len: int, 
                        raw_data: str, 
                        channel: Optional[str] = "",
                        message_name: Optional[str] = ""
                        ) -> Optional[CANLogLine]:
        try:
            can_id_token = can_id_hex.strip()
            if can_id_token.endswith(("x", "X")):
                can_id_token = can_id_token[:-1]
            can_id = int(can_id_token, 16)
        except Exception:
            return None
        
        prev_raw = self.last_raw_by_id.get(can_id)
        if not prev_raw: 
            prev_raw = ""
            changed = False
        else:
            changed = prev_raw != raw_data
        self.last_raw_by_id[can_id] = raw_data

        last_time = self.last_timestamp_by_id.get(can_id)
        self.last_timestamp_by_id[can_id] = timestamp
        
        return CANLogLine(
            line_number=line_number,
            timestamp=timestamp,
            channel=channel,
            can_id=can_id,
            _user_message_name =message_name,
            direction=direction,
            data_len=data_len,
            raw_data=raw_data,
            changed=changed,
            last_raw_data=prev_raw,
            last_timestamp=last_time if last_time else timestamp
        )
    
    def _parse_filter_log(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[0])
            direction = "Tx" if "Tx" in tokens else "Rx"
            dir_idx = tokens.index(direction)
            can_id = tokens[dir_idx + 1]
            message_name = tokens[dir_idx + 2] if len(tokens) > dir_idx + 2 else ""
            dlc = 0
            dlc_idx = None
            for i, token in enumerate(tokens):
                if token.isdigit():
                    value = int(token)
                    if value in self._VALID_DLC and i + 1 < len(tokens):
                        nxt = tokens[i + 1]
                        if len(nxt) == 2 and nxt[0] in self._HEX_CHARS and nxt[1] in self._HEX_CHARS:
                            dlc = value
                            dlc_idx = i
                            break
            if dlc_idx is None:
                return None
            data_list = tokens[dlc_idx + 1:dlc_idx + 1 + dlc]
            return self._create_log_entry(
                line_number = line_number, 
                timestamp=timestamp, 
                can_id_hex=can_id, 
                direction=direction, 
                data_len=dlc, 
                raw_data=' '.join(data_list), 
                message_name=message_name)
        except Exception:
            return None

    def _parse_canoe(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()

            # ---- timestamp ----
            timestamp = float(tokens[0])

            # ---- direction (Tx / Rx) ----
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"

            # ---- channel ----
            # Format: CANFD <channel>
            canfd_idx = tokens.index("CANFD")
            channel = tokens[canfd_idx + 1]

            # ---- CAN ID ----
            can_id_hex = tokens[dir_idx + 1]

            # ---- raw data (always trailing hex bytes) ----
            dlc = None
            dlc_idx = None

            for i, t in enumerate(tokens):
                # DLC is decimal, typically 0–64
                if t.isdigit():
                    val = int(t)
                    if val in (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64):
                        # Heuristic: DLC must be followed by hex bytes
                        if i + 1 < len(tokens) and re.fullmatch(r"[0-9A-Fa-f]{2}", tokens[i + 1]):
                            dlc = val
                            dlc_idx = i
                            break

            if dlc is None:
                raise ValueError("Cannot determine DLC")

            # ---- extract exactly DLC data bytes ----
            raw_data_tokens = tokens[dlc_idx + 1 : dlc_idx + 1 + dlc]

            raw_data = " ".join(raw_data_tokens)
            data_len = dlc

            # ---- message name (best effort, optional) ----
            message_name = None
            name_tokens = []

            for t in tokens[dir_idx + 2:]:
                if t.isdigit():
                    break
                if re.fullmatch(r"[0-9A-Fa-f]{2}", t):
                    break
                name_tokens.append(t)

            if name_tokens:
                message_name = " ".join(name_tokens)

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=data_len,
                raw_data=raw_data,
                message_name=message_name,
                channel=channel,
            )

        except Exception:
            return None
        
    def _parse_canoe(self, line: str, line_number: int) -> Optional[CANLogLine]:   
        parts = line.split(None, 5)
        if len(parts) < 6:
            return None
        
        # Direct field extraction by known positions (no .index!)
        timestamp = float(parts[0])
        # parts[1] is "CANFD" - skip
        channel = parts[2]
        direction = parts[3]  # "Tx" or "Rx" 
        can_id_hex = parts[4]
        rest = parts[5]  # msg_name + flags + dlc + data + [trailing]
        
        # Tokenize only the remainder for DLC/data extraction
        tokens = rest.split()
        n = len(tokens)
        
        # Find DLC: valid DLC value followed by 2-char hex byte
        dlc = None
        dlc_idx = None
        _HEX = self._HEX_CHARS
        _DLC = self._VALID_DLC
        
        for i in range(n - 1):
            t = tokens[i]
            # Fast check: 1-2 digit decimal only
            if len(t) <= 2 and t.isdigit():
                val = int(t)
                if val in _DLC:
                    nt = tokens[i + 1]
                    # Fast hex byte check: exactly 2 hex chars
                    if len(nt) == 2 and nt[0] in _HEX and nt[1] in _HEX:
                        dlc = val
                        dlc_idx = i
                        break
        
        if dlc is None:
            return None
        
        # Extract exactly DLC data bytes
        end = dlc_idx + 1 + dlc
        if end > n:
            end = n
        raw_data = " ".join(tokens[dlc_idx + 1 : end])
        
        # Message name: tokens before the "flag1 flag2 dlc_code dlc" pattern
        # Format: [msg_name...] 1 0 8 8 data... so msg_name ends at dlc_idx - 3
        message_name = None
        if dlc_idx >= 4:  # Need at least 4 tokens before data (name + 3 flags/dlc)
            name_end = dlc_idx - 3
            if name_end > 0:
                message_name = " ".join(tokens[:name_end])
            
        return self._create_log_entry(
            line_number=line_number,
            timestamp=timestamp,
            can_id_hex=can_id_hex,
            direction=direction,
            data_len=dlc,
            raw_data=raw_data,
            message_name=message_name,
            channel=channel,
        )

    def _parse_canoe_compact(
        self, line: str, line_number: int
    ) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[0])
            channel = tokens[1]
            can_id_hex = tokens[2]
            direction = tokens[3]

            dlc = int(tokens[5])
            raw_data_tokens = tokens[6 : 6 + dlc]

            if len(raw_data_tokens) != dlc:
                raise ValueError(
                    f"Payload length mismatch: expected {dlc}, got {len(raw_data_tokens)}"
                )

            raw_data = " ".join(raw_data_tokens)

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                channel=channel,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=raw_data,
                message_name=None,
            )

        except Exception:
            return None

    def _parse_canoe_full(
        self,
        line: str,
        line_number: int
    ) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[2])
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"
            can_id_hex = tokens[dir_idx - 1]
            bus_type = tokens[4]            # "CANFD"
            channel = int(tokens[5])        # channel number

            dlc = None
            dlc_idx = None

            for i, t in enumerate(tokens):
                # DLC is decimal, typically 0–64
                if t.isdigit():
                    val = int(t)
                    if val in (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64):
                        # Heuristic: DLC must be followed by hex bytes
                        if i + 1 < len(tokens) and re.fullmatch(r"[0-9A-Fa-f]{2}", tokens[i + 1]):
                            dlc = val
                            dlc_idx = i
                            break

            if dlc is None:
                raise ValueError("Cannot determine DLC")

            # ---- extract exactly DLC data bytes ----
            raw_data_tokens = tokens[dlc_idx + 1 : dlc_idx + 1 + dlc]

            raw_data = " ".join(raw_data_tokens)
            data_len = dlc


            message_name = None
            name_tokens = []

            for t in tokens[dir_idx + 1:]:
                if t.isdigit():
                    break
                if re.fullmatch(r"[0-9A-Fa-f]{2}", t):
                    break
                name_tokens.append(t)

            if name_tokens:
                message_name = " ".join(name_tokens)

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=data_len,
                raw_data=raw_data,
                message_name=message_name,
                channel=channel,
                # bus_type=bus_type,
            )
        except Exception:
            return None

    def _parse_cansuke(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()

            # ---- timestamp ----
            timestamp = float(tokens[0])

            # ---- direction ----
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"

            # ---- CAN ID ----
            can_id_hex = tokens[dir_idx - 1]

            # ---- DLC ----
            # DLC is the first integer AFTER the direction + name
            dlc = None
            dlc_idx = None

            for i in range(dir_idx + 1, len(tokens)):
                if tokens[i].isdigit():
                    dlc = int(tokens[i])
                    dlc_idx = i
                    break

            if dlc is None:
                raise ValueError("DLC not found")

            # ---- extract exactly DLC data bytes ----
            raw_data_tokens = tokens[dlc_idx + 1 : dlc_idx + 1 + dlc]

            if len(raw_data_tokens) != dlc:
                raise ValueError(f"Incomplete payload: expected {dlc}, got {len(raw_data_tokens)}")

            raw_data = " ".join(raw_data_tokens)

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=raw_data,
            )

        except Exception:
            return None


    def _parse_cancommander(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()

            # date time timestamp ...
            timestamp = float(tokens[2])

            # Tx / Rx
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"

            # CAN ID is before Tx/Rx
            can_id_hex = tokens[dir_idx - 1]

            # Message name (best effort)
            message_name = tokens[dir_idx + 1]

            # Data bytes (always at end)
            dlc = None
            dlc_idx = None

            for i, t in enumerate(tokens):
                # DLC is decimal, typically 0–64
                if t.isdigit():
                    val = int(t)
                    if val in (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64):
                        # Heuristic: DLC must be followed by hex bytes
                        if i + 1 < len(tokens) and re.fullmatch(r"[0-9A-Fa-f]{2}", tokens[i + 1]):
                            dlc = val
                            dlc_idx = i
                            break

            if dlc is None:
                raise ValueError("Cannot determine DLC")

            # ---- extract exactly DLC data bytes ----
            raw_data_tokens = tokens[dlc_idx + 1 : dlc_idx + 1 + dlc]

            raw_data = " ".join(raw_data_tokens)
            data_len = dlc

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=data_len,
                raw_data=raw_data,
                message_name=message_name
            )

        except Exception:
            return None

    def _parse_cancommander_type2(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            cols = [c.strip() for c in line.split("\t")]
            if len(cols) < 7:
                return None
            ts_raw = cols[0]
            timestamp = float(ts_raw) / 1000.0 if ts_raw.isdigit() else float(ts_raw)
            ch = cols[1]
            can_id = cols[2]
            message_name = cols[3] or None
            dlc_token = cols[4].upper()
            data_tokens = cols[5].split()
            direction = cols[6]
            if dlc_token.isdigit():
                length = int(dlc_token)
            else:
                length = CANFD_DLC_MAP.get(dlc_token, len(data_tokens))
            data = " ".join(data_tokens[:length])

            return self._create_log_entry(
                line_number=line_number, 
                timestamp=timestamp, 
                can_id_hex=can_id, 
                direction=direction, 
                data_len=length, 
                raw_data=data, 
                message_name=message_name,
                channel=ch)
        except Exception:
            return None
        
    def _parse_cancommander_type3(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()

            # timediff → seconds
            timestamp = float(tokens[0]) * 0.001

            # channel
            channel = tokens[1]

            # CAN ID
            can_id_hex = tokens[2]

            # Tx / Rx
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"

            # Data bytes
            dlc = None
            dlc_idx = None

            for i, t in enumerate(tokens):
                # DLC is decimal, typically 0–64
                if t.isdigit():
                    val = int(t)
                    if val in (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64):
                        # Heuristic: DLC must be followed by hex bytes
                        if i + 1 < len(tokens) and re.fullmatch(r"[0-9A-Fa-f]{2}", tokens[i + 1]):
                            dlc = val
                            dlc_idx = i
                            break

            if dlc is None:
                raise ValueError("Cannot determine DLC")

            # ---- extract exactly DLC data bytes ----
            raw_data_tokens = tokens[dlc_idx + 1 : dlc_idx + 1 + dlc]

            raw_data = " ".join(raw_data_tokens)
            data_len = dlc

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=data_len,
                raw_data=raw_data,
                channel=channel,
                message_name=None
            )
        except Exception:
            return None

    """ Interface method for reading a log file"""
    def load_log_file(self, canlf: CANLogFile) -> bool:
        # read new log then reset parser
        file_path = canlf.file_path
        self.detected_parser: Optional[Callable[[str, int], Optional[CANLogLine]]] = None
        self.last_raw_by_id = {}

        result = False
        file_type = get_file_type(file_path)
        if file_type == "asc" or file_type == "blf":
            result = self._parse_from_asc(canlf)
        elif file_type == "csv":
            result = self._parse_from_csv(canlf)
        elif file_type == "xlsx":
            result = self._parse_from_excel(canlf)
        elif file_type in ("log", "txt"):
            result = self._parse_from_file(canlf)
        else:
            pass
        # canlf.last_raw_by_id = deepcopy(self.last_raw_by_id)
        #self.data[canlf.file_path] = canlf #### -> THIS SHOULD BE DELETED SINCE IT ALREADY HOLD THE REFERENCE
        ## Continue get caculate signas for all message
        #self._cal_signal_all_messages(canlf.file_name)
        return result
    
    def _entry_to_log_line(self, entry) -> Optional[CANLogLine]:
        """Convert a native ParsedEntry into a CANLogLine via state-tracking _create_log_entry."""
        raw_data = " ".join(f"{entry.data[j]:02X}" for j in range(entry.data_len))
        return self._create_log_entry(
            line_number  = int(entry.line_number),
            timestamp    = float(entry.timestamp),
            can_id_hex   = f"{entry.can_id:X}",
            direction    = "Tx" if entry.direction == 1 else "Rx",
            data_len     = int(entry.data_len),
            raw_data     = raw_data,
            channel      = entry.channel.decode("ascii", errors="ignore"),
            message_name = "",
        )

    # ── CSV / Excel loaders ───────────────────────────────────────────────────

    def _parse_from_csv(self, canlf: CANLogFile):
        file_path = canlf.file_path
        parsed_count = 0
        try:
            df = pd.read_csv(file_path)
        except Exception as e:
            LOG.error(f"Failed to read CSV file: {e}")
            return False
        for i, row in df.iterrows():
            line = ' '.join(str(cell) for cell in row if pd.notna(cell))
            line_norm = self._ws_re.sub(' ', line.strip())
            parsed = self._various_parse_line_test(line_norm, i)
            if not parsed:
                continue
            parsed_count += 1
        canlf.total_lines = parsed_count
        LOG.info(f"{parsed_count}")
        return True

    def _parse_from_excel(self, canlf: CANLogFile):
        file_path = canlf.file_path
        parsed_count = 0
        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            LOG.error(f"Failed to read Excel file: {e}")
            return False
        for i, row in df.iterrows():
            line = ' '.join(str(cell) for cell in row if pd.notna(cell))
            line_norm = self._ws_re.sub(' ', line.strip())
            parsed = self._various_parse_line_test(line_norm, i)
            if not parsed:
                continue
            parsed_count += 1
        canlf.total_lines = parsed_count
        LOG.info(f"{parsed_count}")
        return True
    
    def format_channel(self, channel) -> str:
        if channel is None:
            return ""
        if isinstance(channel, (list, tuple)):
            return ",".join(str(c) for c in channel)
        return str(channel)

    def _parse_from_asc(self, canlf: CANLogFile):
        file_path = canlf.file_path
        parsed_count = 0
        try:
            file_type = get_file_type(file_path)
            if file_type == "asc":
                reader = ASCReader(file_path)
            elif file_type == "blf":
                reader = BLFReader(file_path)
            else:
                LOG.debug("Asc file is not in standard format, try to parse by Regex")
                return self._parse_from_file(canlf)

            # last_timestamp_by_id: Dict[int, float] = {}
            for i, msg in enumerate(reader, start=1):
                timestamp = msg.timestamp
                channel = self.format_channel(msg.channel)
                can_id = msg.arbitration_id
                can_id_hex = f"0x{can_id:03X}"
                direction = "Rx" if msg.is_rx else "Tx"
                dlc = msg.dlc if hasattr(msg, 'dlc') else len(msg.data)
                data = list(msg.data)
                hex_data = [f"0x{byte:02X}" for byte in data]
                hex_data_str = " ".join(f"{byte:02X}" for byte in data)
                message_name = getattr(msg, "name", None)
                """
                #LOG.debug(f"[Line {i}] Parsed CAN message:")
                #LOG.debug(f"  Channel: {channel}")
                #LOG.debug(f"  Timestamp: {timestamp}")
                #LOG.debug(f"  CAN ID: 0x{can_id:X} ({can_id})")
                #LOG.debug(f"  Direction: {direction}")
                #LOG.debug(f"  DLC: {dlc}")
                #LOG.debug(f"  Data: {data}")
                #LOG.debug(f"  Message Name: {message_name}")
                """
                # Create your internal log entry
                parsed = self._create_log_entry(
                    line_number=i, 
                    timestamp=float(timestamp), 
                    channel=channel, 
                    can_id_hex=can_id_hex, 
                    direction=direction, 
                    data_len=int(dlc), 
                    raw_data=hex_data_str, 
                    message_name=message_name)
                if not parsed:
                    continue
                parsed_count += 1
                if parsed_count % PAGE_SIZE == 0:
                    LOG.info(f"{parsed_count}")
            canlf.total_lines = parsed_count
            return True

        except Exception as e:
            LOG.error(f"Failed to read ASC log file: {e}")
            LOG.debug("Fallback to regex parser for non-standard ASC/BLF")
            return self._parse_from_file(canlf)
        finally:
            try:
                reader.stop()
            except Exception:
                pass

    def _parse_from_file(self, canlf: CANLogFile,
                          data_path: str = "",
                          index_path: str = "") -> bool:
        file_path = canlf.file_path

        try:
            rc = self.run_native_to_mmap(
                file_path=file_path,
                data_path=data_path,
                index_path=index_path,
            )
            if not rc:
                LOG.error(f"C++ run failed (returned {rc})")
            return bool(rc)
        except Exception as e:
            LOG.error(f"C++ run_worker_2pass failed: {e}")
            return False

def main():
    setup_logger(env="DEV", backup_count=30)
    parser = LogParser()
    ########### TEST LINE ##################
    test_lines = [
        "2132132 CANFD   1 Rx        417                                   1 0 8 8  14 3C 40 00 00 00 09 BC",
        "2132133 CANFD   1 Rx        48E                                   1 0 8 8  40 92 49 60 80 4D 00 00",
        "2132134 CANFD   1 Rx        252                                   1 0 d 32 0F FE 0F FE 0F FE 0F FE 01 7E 01 82 00 BE 0F FF 0F FE 0F FE 0F FE 0F FE 00 00 00 00 00 00 00 00",
        "2132135 CANFD   1 Rx         84                                   1 0 8 8  3F 85 3E 76 81 02 2F 3F",
        "2132136 CANFD   1 Tx        215  WheelSpeed                       1 0 8  8 27 10 27 10 27 10 27 10   102500  130   323040 b0013979 50500250 46140250 20010f3e 2001050c",
        "2132137 CANFD   1 Tx         82  Steering_Wheel_Angle             1 0 8  8 00 00 ff fc 00 00 00 00   108000  138   303040 b0013400 50500250 46140250 20010f3e 2001050c",
        # "2025-03-18 11:34:06.214  12047.715000  1  CANFD 1        228  Rx  TransGearData                     8   8   E2 00 03 00 00 E0 00 00",
        # "2025-03-18 11:34:06.215  12047.715000  1  CANFD 1        252  Rx  Ambient_Light_HMI_Rq              d   32  0F FE 0F FE 0F FE 0F FE 01 7E 01 82 00 AA 0F FF 0F FE 0F FE 0F FE 0F FE 00 00 00 00 00 00 00 00",
        # "64.29609        0  465             Rx    d 8    01 BE 79 2E C5 0F 13 18",
        # "64.39621        0  3BC             Rx    d 8    00 00 00 00 00 00 00 00",
        # "0.00783         0  001             Rx    d 4    00 00 00 00 ",
        # "708368	1	000003A0	WarningTriggerVDT_1	8	00 00 00 00 00 00 00 00	Rx		CANFD	1",
        # "708368	1	206		                        8	4F 02 15 6B E4 7D 03 40	Rx		CANFD	1",
        # "08368	1	455	ADU_HMI_Information_8	    D	10 00 00 00 00 00 00 00 21 40 0F 18 41 86 10 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00	Rx		CANFD	1",
        # "372   323040 c80987db 50500250 46140250 20010f3e 2001050c",
        "151.616805 CANFD   1 Rx         20  CGW_Sync                         1 0 8  8 00 00 00 1f 9f a0 05 23   109516  138   323040 b001e4c5 50500250 46140250 20010f3e 2001050c",
        "151.617030 CANFD   2 Tx         81  VSC1G12                          1 0 d 32 02 00 02 00 02 00 00 00 0b b8 0b b8 27 10 00 00 00 00 00 00 00 00 00 00 00 00 00 00 db d8 62 2a   219516  361   323040 a8045fa8 50500250 46140250 20010f3e 2001050c",
        "151.617262 CANFD   3 Tx         30  EPS1S11                          1 0 d 32 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 52 a9 74 08   226016  371   323040 801bf8f2 50500250 46140250 20010f3e 2001050c",
        "151.617576 CANFD   4 Tx        251  MAZPCM01                         1 0 d 32 00 00 00 00 00 00 00 00 80 00 01 00 00 00 00 00 00 00 00 08 00 f1 00 00 00 00 00 00 00 00 00 da   221516 ",
    ]

    test_lines = [
    "151.610837 CANFD   1 Tx        4f3  Meter_Infomation                 1 0 8  8 00 00 06 40 00 00 00 45   105500  136   303040 98000288 50500250 46140250 20010f3e 2001050c",
    "151.610946 CANFD   1 Rx        215  WheelSpeed                       1 0 8  8 27 10 27 10 27 10 27 10   102500  130   323040 b0013979 50500250 46140250 20010f3e 2001050c",
    "151.611058 CANFD   1 Tx        21c  AVN1S73                          1 0 8  8 80 00 00 00 00 00 10 00   106500  138   323040 b001d12f 50500250 46140250 20010f3e 2001050c",
    "151.611422 CANFD   1 Tx         82  Steering_Wheel_Angle             1 0 8  8 00 00 ff fc 00 00 00 00   108000  138   303040 b0013400 50500250 46140250 20010f3e 2001050c",
    "151.611538 CANFD   1 Tx         50  Push_Start_Status                1 0 8  8 02 00 00 00 00 00 00 5a   110000  139   323040 f800301d 50500250 46140250 20010f3e 2001050c",
    "151.610946 CANFD   1 Rx        215  WheelSpeed                       1 0 8  8 27 10 27 10 27 10 27 10   102500  130   323040 b0013979 50500250 46140250 20010f3e 2001050c",
    ]

    ############ TEST PARSE TYPE ###################3
    # for i, line in enumerate(test_lines, start=1):
    #     ok = parser._various_parse_line_test(line, i)
    #     if not ok:
    #         LOG.info(f"[Line {i}] No parser detected")
    #     else:
    #         LOG.info(ok)

    ########### TEST PATH ##################
    # file_path = Path.home() / "test.asc"
    # FILELOG = str(file_path)
    # with open(file_path, "w", encoding="utf-8") as f:
    #     for line in test_lines:
    #         f.write(line + "\n")
    # canlf = CANLogFile(file_path = FILELOG)

    ############ TEST REAL PATH #############
    FILELOG = "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x4000.asc"
    canlf = CANLogFile(file_path = FILELOG)
 
    ######################### COMMON SET UP ##################
    # C++ 2-pass worker creates the mmap files itself — just pass the paths.
    base_dir = Path(__file__).resolve().parent
    mmap_dir = base_dir / "dumps" / "mmap"
    mmap_dir.mkdir(parents=True, exist_ok=True)
    mmap_path  = mmap_dir / (Path(FILELOG).name + ".data.mmap")
    index_path = mmap_dir / (Path(FILELOG).name + ".index.mmap")

    # Remove stale segmented files for this log to avoid mixing old runs
    for stale in mmap_dir.glob(f"{Path(FILELOG).name}.index.*.mmap"):
        try:
            stale.unlink()
        except Exception:
            pass
    for stale in mmap_dir.glob(f"{Path(FILELOG).name}.data.*.mmap"):
        try:
            stale.unlink()
        except Exception:
            pass

    ######################### NORMAL TEST #######################
    run_start_mtime = Path(FILELOG).stat().st_mtime if Path(FILELOG).exists() else 0.0
    # ok = parser._parse_from_file(canlf,
    #                               data_path=str(mmap_path),
    #                               index_path=str(index_path))
    # LOG.info(f"_parse_from_file result: {ok}")
    # LOG.info(f"raw  mmap path : {mmap_path}")
    # LOG.info(f"index mmap path: {index_path}")

    ################## READ TEST METHOD 1 ##########################
    # import mmap as _mmap
    # import struct
    # HEADER_SIZE = 32
    # ROW_SIZE = 98
    # CAN_ID_OFFSET = 12  # ParsedEntry layout: line_number(4) + timestamp(8) + can_id(4)
    # target_can_id = 0x215
    # rows: List[int] = []
    # canlf.log_entries.clear()
    # try:
    #     with open(mmap_path, "rb") as mmap_file:
    #         mm = _mmap.mmap(mmap_file.fileno(), 0, access=_mmap.ACCESS_READ)
    #         try:
    #             n = struct.unpack_from("<Q", mm, 0)[0]  # header.write_count
    #             base = HEADER_SIZE
    #             max_rows = max(0, (len(mm) - base) // ROW_SIZE)
    #             n = min(n, max_rows)
    #             for i in range(n):
    #                 off = base + i * ROW_SIZE
    #                 can_id = struct.unpack_from("<I", mm, off + CAN_ID_OFFSET)[0]
    #                 if can_id == target_can_id:
    #                     rows.append(i)
    #                     # line_number = struct.unpack_from("<I", mm, off + 0)[0]
    #                     # timestamp = struct.unpack_from("<d", mm, off + 4)[0]
    #                     # direction_raw = struct.unpack_from("<B", mm, off + 16)[0]
    #                     # data_len = struct.unpack_from("<B", mm, off + 17)[0]
    #                     # data_bytes = struct.unpack_from("<64B", mm, off + 18)
    #                     # channel_raw = struct.unpack_from("<16s", mm, off + 82)[0]
    #                     # message_name is removed from ParsedEntry layout

    #                     # direction = "Tx" if direction_raw == 1 else "Rx"
    #                     # raw_data = " ".join(f"{b:02X}" for b in data_bytes[:data_len])
    #                     # channel = channel_raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    #                     # message_name = message_raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    #                     # canlf.log_entries[line_number] = CANLogLine(
    #                     #     line_number=line_number,
    #                     #     timestamp=timestamp,
    #                     #     channel=channel,
    #                     #     can_id=can_id,
    #                     #     _user_message_name=message_name,
    #                     #     direction=direction,
    #                     #     data_len=data_len,
    #                     #     raw_data=raw_data,
    #                     #     changed=False,
    #                     #     last_raw_data="",
    #                     #     last_timestamp=timestamp,
    #                     # )
    #         finally:
    #             mm.close()
    #     LOG.info(f"mmap rows scanned: {n}")
    #     LOG.info(f"rows with can_id={target_can_id}: {len(rows)}")
    #     LOG.info(f"canlf.log_entries collected: {len(canlf.log_entries)}")
    #     # if rows:
    #     #     LOG.info(f"matched row indexes (first 50): {rows[:5]}")
    #     #     for entry in list(canlf.log_entries.values())[:5]:
    #     #         LOG.info(
    #     #             "MATCH row=%s line=%s ts=%.6f can_id=0x%X dir=%s dlc=%s channel='%s' msg='%s' data=%s",
    #     #             "-",
    #     #             entry.line_number,
    #     #             entry.timestamp,
    #     #             entry.can_id,
    #     #             entry.direction,
    #     #             entry.data_len,
    #     #             entry.channel,
    #     #             entry.message_name,
    #     #             entry.raw_data,
    #     #         )
    # except Exception as e:
    #     LOG.error(f"READ TEST failed: {e}")

    ################## 20260305: TEST CASE READ TIMEDIFF OF A CAN_ID ##########################
    target_can_id = 0x215
    try:
        from file_service.parser.native.native_bridge import MmapData  # type: ignore[import-not-found]

        data_files = sorted(mmap_dir.glob(f"{Path(FILELOG).name}.data.*.mmap"))
        data_files = [p for p in data_files if p.stat().st_mtime >= run_start_mtime]
        if not data_files and mmap_path.exists():
            data_files = [mmap_path]

        if not data_files:
            LOG.error("[CAN_ID] data mmap is missing")
            return

        timestamps: List[float] = []
        last_timestamps: List[float] = []
        used_files = 0

        for data_file in data_files:
            with MmapData(str(data_file)) as data_mm:
                used_files += 1
                file_hits = 0
                for entry in data_mm.iter_new_entries(0):
                    if int(entry.can_id) != target_can_id:
                        continue
                    timestamps.append(float(entry.timestamp))
                    file_hits += 1
                LOG.info(
                    f"[CAN_ID] data={data_file.name} write_count={data_mm.write_count} "
                    f"capacity={data_mm.capacity} hits_for_0x{target_can_id:X}={file_hits}"
                )

        if not timestamps:
            LOG.warning(f"[CAN_ID] no entries found for can_id=0x{target_can_id:X}")
            return

        timestamps.sort()
        prev_ts: Optional[float] = None
        for ts in timestamps:
            last_timestamps.append(ts if prev_ts is None else prev_ts)
            prev_ts = ts

        preview = 20
        LOG.info("[CAN_ID] can_id=0x%X total_rows=%d", target_can_id, len(timestamps))
        LOG.info("[CAN_ID] used_data_files=%d", used_files)
        LOG.info("[CAN_ID] timestamp(first_%d)=%s", preview, timestamps[:preview])
        LOG.info("[CAN_ID] timestamp(last_%d)=%s", preview, timestamps[-preview:])
        LOG.info("[CAN_ID] last_timestamp(first_%d)=%s", preview, last_timestamps[:preview])
        LOG.info("[CAN_ID] last_timestamp(last_%d)=%s", preview, last_timestamps[-preview:])
    except Exception as e:
        LOG.error(f"[TIMEDIFF] test failed: {e}", exc_info=True)


    ################## READ PROGRESSING TEST ##########################
    # import threading, time

    # stop_event = threading.Event()

    # def poll_progress(path: str, run_start_ts: float, interval: float = 0.5):
    #     """Background thread: report progress by spawned .NNN mmap chunk files only."""

    #     last_chunk_count = -1
    #     last_latest_name = ""
    #     base = Path(path)

    #     while not stop_event.is_set():
    #         seg_files = sorted(mmap_dir.glob(f"{Path(FILELOG).name}.data.*.mmap"))
    #         fresh = [p for p in seg_files if p.stat().st_mtime >= (run_start_ts - 1.0)]

    #         if fresh:
    #             chunk_count = len(fresh)
    #             latest_name = fresh[-1].name
    #             if chunk_count != last_chunk_count or latest_name != last_latest_name:
    #                 LOG.info(
    #                     "[PROGRESS] chunks=%d latest=%s",
    #                     chunk_count,
    #                     latest_name,
    #                 )
    #                 last_chunk_count = chunk_count
    #                 last_latest_name = latest_name
    #         elif base.exists() and base.stat().st_mtime >= (run_start_ts - 1.0):
    #             if last_chunk_count != 1 or last_latest_name != base.name:
    #                 LOG.info("[PROGRESS] chunks=1 latest=%s", base.name)
    #                 last_chunk_count = 1
    #                 last_latest_name = base.name

    #         time.sleep(interval)

    # # ── Spawn poll thread, then run the hot-path, then join ──
    # run_start_ts = time.time()
    # poll_thread = threading.Thread(
    #     target=poll_progress,
    #     args=(str(mmap_path), run_start_ts, 0.5),
    #     daemon=True,
    # )
    # poll_thread.start()
    # ok2 = parser._parse_from_file(
    #     canlf,
    #     data_path=str(mmap_path),
    #     index_path=str(index_path),
    # )
    # stop_event.set()
    # poll_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
