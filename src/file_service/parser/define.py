"""
patterns.py — All CAN log format regex patterns, DLC maps, and file-type helpers.

No dependencies on internal can_sdk modules — safe to import from anywhere.
"""
import re
import os

# ── Page / batch size ─────────────────────────────────────────────────────────
PAGE_SIZE                    = 10_000
BATCH_SIZE                   = PAGE_SIZE
WORKER_ERR_CAPACITY_OVERFLOW = -8

# ── Regex patterns (raw strings) ──────────────────────────────────────────────

PATTERN_FILTER = (
    r"([\d\.]+)\s+\S+\s+\d+\s+"
    r"(Tx|Rx)\s+"
    r"([0-9a-fA-F]{1,8})\s+"
    r"(\S+)\s+"
    r"(\d{1,2})\s+"
    r"((?:[0-9a-fA-F]{2}(?:\s+|$)){1,64})"
)

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

PATTERN_CANSK = (
    r"([\d\.]+)\s+\d\s+([0-9a-fA-F]{1,8})\s+(Tx|Rx)\s+(\S+)\s+(\d |\d\d)\s+(\s[0-9a-fA-F]{2})+"
)

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
    r"(\d |\d\d)\s+"               # 10) LEN (either "digit space" or "two digits")
    r"(\s[0-9A-Fa-f]{2})+"         # 11) data bytes (one or more " XX")
)

PATTERN_CANCMD_TYPE2 = (
    r"(\d+)\s+"                    # 0) timediff  — integer (e.g. 12399)
    r"(\d)\s+"                     # 1) flags/bus/whatever
    r"([0-9A-Fa-f]{1,8})\s+"       # 2) CAN ID
    r"(?:([^\s]+)\s+)?"            # 3) OPTIONAL message name
    r"([0-9A-Fa-f])\s+"            # 4) exactly 1 hex character (DLC code)
    r"((?:[0-9A-Fa-f]{2}\s+)+)"    # 5) data bytes (at least one hex byte)
    r"(Tx|Rx)\s+"                  # 6) direction
    r"(\S+)\s+"                    # 7) e.g. CANFD
    r"(\d)\s*"                     # 8) another digit (e.g. channel)
)

PATTERN_CANCMD_TYPE3 = (
    r"(\d+)\s+"                    # 0) timediff  — integer (e.g. 12399)
    r"(\d)\s+"                     # 1) flags/bus/whatever
    r"([0-9A-Fa-f]{1,8})\s+"       # 2) CAN ID
    r"([0-9A-Fa-f])\s+"            # 3) exactly 1 hex character (DLC code)
    r"((?:[0-9A-Fa-f]{2}\s+)+)"    # 4) data bytes (at least one hex byte)
    r"(Tx|Rx)\s+"                  # 5) direction
    r"(\S+)\s+"                    # 6) e.g. CANFD
    r"(\d)\s*"                     # 7) another digit (e.g. channel)
)

PATTERN_CANCMD_TYPE4 = (
    r"([\d-]+)\s+"                 # 1) date
    r"([\d:\.]+)\s+"               # 2) time
    r"(\d)\s+"                     # 3) flags/bus/whatever
    r"([0-9A-Fa-f]{1,8})\s+"       # 4) CAN ID
    r"(?:([^\s]+)\s+)? "           # 5) OPTIONAL message name
    r"(\d{1,2})\s+"                # 6) DLC
    r"((?:[0-9A-Fa-f]{2}\s+)+)"    # 7) data bytes (at least one hex byte)
    r"(Tx|Rx)\s+"                  # 8) direction
    r"(\S+)\s+"                    # 9) e.g. CANFD
    r"(\d)\s*"                     # 10) another digit (e.g. channel)
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

# ── CAN FD DLC map (DLC code → byte length) ───────────────────────────────────
CANFD_DLC_MAP: dict[str, int] = {
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


def get_length_from_dlc(dlc: int) -> int:
    """Map a raw DLC value (0–15) to its actual byte length."""
    dlc_to_len = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
    return dlc_to_len[min(dlc, len(dlc_to_len) - 1)]


def get_file_type(file_path: str) -> str:
    """Return a normalised file-type string based on the file extension."""
    extension = os.path.splitext(file_path.lower())[1]
    return {
        ".xls":  "excel",
        ".xlsx": "excel",
        ".csv":  "csv",
        ".blf":  "blf",
        ".asc":  "asc",
        ".log":  "log",
        ".txt":  "txt",
    }.get(extension, "unknown")
