"""
can_parser_api.py
Python ctypes binding for the C++ CAN log parser (can_parser.cpp).

Usage:
    from native_sdk.can_parser_api import CanParserLib, ParsedEntry
    from native_sdk.can_parser_api import CanParserMonitor

    lib = CanParserLib()
    entries = lib.parse_file("/path/to/log.asc")  # list[ParsedEntry]

    # Or single-line (for CSV/Excel per-row calls):
    entry = lib.parse_line("151.6 CANFD 1 Rx 020 ...", line_num=1)

    # mmap mode: create mmap files, then call can_parser_run_worker directly
    # in the same Python process via CanParserLib()._lib.can_parser_run_worker(...)
"""
import ctypes
import mmap as _mmap_mod
import struct
from typing import Generator, Optional, List
import os

from file_service.native_loader import load_library


class MmapHeaderConstract:
    SIZE = 32
    WRITE_COUNT_OFFSET = 0
    CAPACITY_OFFSET = 8
    STATUS_OFFSET = 12

    WRITE_COUNT_STRUCT = struct.Struct("<Q")
    CAPACITY_STRUCT = struct.Struct("<I")
    STATUS_STRUCT = struct.Struct("<I")

    @classmethod
    def load_from_native_binding(cls) -> None:
        try:
            lib = load_library()

            lib.mmap_header_constract_size.argtypes = []
            lib.mmap_header_constract_size.restype = ctypes.c_uint32
            lib.mmap_header_constract_write_count_offset.argtypes = []
            lib.mmap_header_constract_write_count_offset.restype = ctypes.c_uint32
            lib.mmap_header_constract_capacity_offset.argtypes = []
            lib.mmap_header_constract_capacity_offset.restype = ctypes.c_uint32
            lib.mmap_header_constract_status_offset.argtypes = []
            lib.mmap_header_constract_status_offset.restype = ctypes.c_uint32

            size = int(lib.mmap_header_constract_size())
            write_count_offset = int(lib.mmap_header_constract_write_count_offset())
            capacity_offset = int(lib.mmap_header_constract_capacity_offset())
            status_offset = int(lib.mmap_header_constract_status_offset())

            if size <= 0:
                return

            cls.SIZE = size
            cls.WRITE_COUNT_OFFSET = write_count_offset
            cls.CAPACITY_OFFSET = capacity_offset
            cls.STATUS_OFFSET = status_offset
        except Exception:
            return

# ─────────────────────────────────────────────────────────────────────────────
# Packed struct matching C++ ParsedEntry (can_parser.cpp, #pragma pack(1))
# ─────────────────────────────────────────────────────────────────────────────
class ParsedEntry(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("line_number",  ctypes.c_uint32),       #  4
        ("timestamp",    ctypes.c_double),       #  8
        ("last_timestamp", ctypes.c_double),     #  8
        ("can_id",       ctypes.c_uint32),       #  4
        ("direction",    ctypes.c_uint8),        #  1  (0=Rx, 1=Tx)
        ("data_len",     ctypes.c_uint8),        #  1
        ("changed",      ctypes.c_uint8),        #  1
        ("data",         ctypes.c_uint8 * 64),   # 64
        ("channel",      ctypes.c_char * 16),    # 16  (null-terminated)
    ]
    # ──  total: 107 bytes  ──

    @property
    def hex_data(self) -> str:
        """Return space-separated uppercase hex string, e.g. '14 3C 40 00'."""
        return " ".join(f"{self.data[i]:02X}" for i in range(self.data_len))

    @property
    def channel_str(self) -> str:
        return self.channel.decode("ascii", errors="ignore")

    @property
    def changed_bool(self) -> bool:
        return bool(self.changed)

    @property
    def direction_str(self) -> str:
        return "Tx" if self.direction == 1 else "Rx"


class _CanParserHandle(ctypes.Structure):
    pass


CanParserHandlePtr = ctypes.POINTER(_CanParserHandle)


class _ParsedEntryHandlerOpaque(ctypes.Structure):
    pass


ParsedEntryHandlerHandlePtr = ctypes.POINTER(_ParsedEntryHandlerOpaque)


class ParsedEntryLayout:
    ENTRY_SIZE = ctypes.sizeof(ParsedEntry)
    LINE_NUMBER_OFFSET = 0
    TIMESTAMP_OFFSET = 4
    LAST_TIMESTAMP_OFFSET = 12
    CAN_ID_OFFSET = 20
    DIRECTION_OFFSET = 24
    DATA_LEN_OFFSET = 25
    CHANGED_OFFSET = 26
    DATA_OFFSET = 27
    DATA_CAPACITY = 64
    CHANNEL_OFFSET = 91
    CHANNEL_CAPACITY = 16
    DATA_HEADER_SIZE = MmapHeaderConstract.SIZE

    @classmethod
    def load_from_native_binding(cls) -> None:
        try:
            lib = load_library()

            lib.can_parser_entry_size.argtypes = []
            lib.can_parser_entry_size.restype = ctypes.c_uint32
            lib.can_parser_entry_line_number_offset.argtypes = []
            lib.can_parser_entry_line_number_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_timestamp_offset.argtypes = []
            lib.can_parser_entry_timestamp_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_last_timestamp_offset.argtypes = []
            lib.can_parser_entry_last_timestamp_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_can_id_offset.argtypes = []
            lib.can_parser_entry_can_id_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_direction_offset.argtypes = []
            lib.can_parser_entry_direction_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_data_len_offset.argtypes = []
            lib.can_parser_entry_data_len_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_changed_offset.argtypes = []
            lib.can_parser_entry_changed_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_data_offset.argtypes = []
            lib.can_parser_entry_data_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_data_capacity.argtypes = []
            lib.can_parser_entry_data_capacity.restype = ctypes.c_uint32
            lib.can_parser_entry_channel_offset.argtypes = []
            lib.can_parser_entry_channel_offset.restype = ctypes.c_uint32
            lib.can_parser_entry_channel_capacity.argtypes = []
            lib.can_parser_entry_channel_capacity.restype = ctypes.c_uint32
            lib.can_parser_data_header_size.argtypes = []
            lib.can_parser_data_header_size.restype = ctypes.c_uint32

            entry_size = int(lib.can_parser_entry_size())
            if entry_size <= 0:
                return

            cls.ENTRY_SIZE = entry_size
            cls.LINE_NUMBER_OFFSET = int(lib.can_parser_entry_line_number_offset())
            cls.TIMESTAMP_OFFSET = int(lib.can_parser_entry_timestamp_offset())
            cls.LAST_TIMESTAMP_OFFSET = int(lib.can_parser_entry_last_timestamp_offset())
            cls.CAN_ID_OFFSET = int(lib.can_parser_entry_can_id_offset())
            cls.DIRECTION_OFFSET = int(lib.can_parser_entry_direction_offset())
            cls.DATA_LEN_OFFSET = int(lib.can_parser_entry_data_len_offset())
            cls.CHANGED_OFFSET = int(lib.can_parser_entry_changed_offset())
            cls.DATA_OFFSET = int(lib.can_parser_entry_data_offset())
            cls.DATA_CAPACITY = int(lib.can_parser_entry_data_capacity())
            cls.CHANNEL_OFFSET = int(lib.can_parser_entry_channel_offset())
            cls.CHANNEL_CAPACITY = int(lib.can_parser_entry_channel_capacity())
            cls.DATA_HEADER_SIZE = int(lib.can_parser_data_header_size())
        except Exception:
            return


class IndexMmapLayout:
    INDEX_HEADER_SIZE = 40
    INDEX_HEADER_CAN_ID_COUNT_OFFSET = 0
    INDEX_HEADER_ROW_POOL_SIZE_OFFSET = 4
    INDEX_HEADER_CHANGED_ROW_POOL_SIZE_OFFSET = 8
    INDEX_HEADER_TS_POOL_SIZE_OFFSET = 12
    INDEX_HEADER_MAX_CAN_IDS_OFFSET = 16
    INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET = 20
    INDEX_HEADER_MAX_CHANGED_ROW_POOL_SIZE_OFFSET = 24
    INDEX_HEADER_MAX_TS_POOL_SIZE_OFFSET = 28

    CAN_ID_FILTER_SIZE = 36
    CAN_ID_FILTER_CAN_ID_OFFSET = 0
    CAN_ID_FILTER_ROW_OFFSET_OFFSET = 4
    CAN_ID_FILTER_CHANGED_ROW_OFFSET_OFFSET = 12
    CAN_ID_FILTER_TS_OFFSET_OFFSET = 20
    CAN_ID_FILTER_COUNT_OFFSET = 28
    CAN_ID_FILTER_CHANGED_COUNT_OFFSET = 32

    CHANNEL_INDEX_HEADER_SIZE = 32
    CHANNEL_INDEX_HEADER_CHANNEL_COUNT_OFFSET = 0
    CHANNEL_INDEX_HEADER_MAX_CHANNELS_OFFSET = 8
    CHANNEL_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET = 12

    CHANNEL_FILTER_SIZE = 32
    CHANNEL_FILTER_CHANNEL_INDEX_OFFSET = 0
    CHANNEL_FILTER_CHANNEL_OFFSET = 1
    CHANNEL_FILTER_CHANNEL_CAPACITY = 15
    CHANNEL_FILTER_ROW_OFFSET_OFFSET = 16
    CHANNEL_FILTER_COUNT_OFFSET = 24

    DIRECTION_INDEX_HEADER_SIZE = 32
    DIRECTION_INDEX_HEADER_DIRECTION_COUNT_OFFSET = 0
    DIRECTION_INDEX_HEADER_MAX_DIRECTIONS_OFFSET = 8
    DIRECTION_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET = 12

    DIRECTION_FILTER_SIZE = 24
    DIRECTION_FILTER_DIRECTION_OFFSET = 0
    DIRECTION_FILTER_ROW_OFFSET_OFFSET = 8
    DIRECTION_FILTER_COUNT_OFFSET = 16

    @classmethod
    def load_from_native_binding(cls) -> None:
        try:
            lib = load_library()

            def _u32(name: str) -> int:
                fn = getattr(lib, name)
                fn.argtypes = []
                fn.restype = ctypes.c_uint32
                return int(fn())

            cls.INDEX_HEADER_SIZE = _u32("can_parser_index_header_size")
            cls.INDEX_HEADER_CAN_ID_COUNT_OFFSET = _u32("can_parser_index_header_can_id_count_offset")
            cls.INDEX_HEADER_ROW_POOL_SIZE_OFFSET = _u32("can_parser_index_header_row_pool_size_offset")
            cls.INDEX_HEADER_CHANGED_ROW_POOL_SIZE_OFFSET = _u32("can_parser_index_header_changed_row_pool_size_offset")
            cls.INDEX_HEADER_TS_POOL_SIZE_OFFSET = _u32("can_parser_index_header_ts_pool_size_offset")
            cls.INDEX_HEADER_MAX_CAN_IDS_OFFSET = _u32("can_parser_index_header_max_can_ids_offset")
            cls.INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET = _u32("can_parser_index_header_max_row_pool_size_offset")
            cls.INDEX_HEADER_MAX_CHANGED_ROW_POOL_SIZE_OFFSET = _u32("can_parser_index_header_max_changed_row_pool_size_offset")
            cls.INDEX_HEADER_MAX_TS_POOL_SIZE_OFFSET = _u32("can_parser_index_header_max_ts_pool_size_offset")

            cls.CAN_ID_FILTER_SIZE = _u32("can_parser_can_id_filter_size")
            cls.CAN_ID_FILTER_CAN_ID_OFFSET = _u32("can_parser_can_id_filter_can_id_offset")
            cls.CAN_ID_FILTER_ROW_OFFSET_OFFSET = _u32("can_parser_can_id_filter_row_offset_offset")
            cls.CAN_ID_FILTER_CHANGED_ROW_OFFSET_OFFSET = _u32("can_parser_can_id_filter_changed_row_offset_offset")
            cls.CAN_ID_FILTER_TS_OFFSET_OFFSET = _u32("can_parser_can_id_filter_ts_offset_offset")
            cls.CAN_ID_FILTER_COUNT_OFFSET = _u32("can_parser_can_id_filter_count_offset")
            cls.CAN_ID_FILTER_CHANGED_COUNT_OFFSET = _u32("can_parser_can_id_filter_changed_count_offset")

            cls.CHANNEL_INDEX_HEADER_SIZE = _u32("can_parser_channel_index_header_size")
            cls.CHANNEL_INDEX_HEADER_CHANNEL_COUNT_OFFSET = _u32("can_parser_channel_index_header_channel_count_offset")
            cls.CHANNEL_INDEX_HEADER_MAX_CHANNELS_OFFSET = _u32("can_parser_channel_index_header_max_channels_offset")
            cls.CHANNEL_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET = _u32("can_parser_channel_index_header_max_row_pool_size_offset")

            cls.CHANNEL_FILTER_SIZE = _u32("can_parser_channel_filter_size")
            cls.CHANNEL_FILTER_CHANNEL_INDEX_OFFSET = _u32("can_parser_channel_filter_channel_index_offset")
            cls.CHANNEL_FILTER_CHANNEL_OFFSET = _u32("can_parser_channel_filter_channel_offset")
            cls.CHANNEL_FILTER_CHANNEL_CAPACITY = _u32("can_parser_channel_filter_channel_capacity")
            cls.CHANNEL_FILTER_ROW_OFFSET_OFFSET = _u32("can_parser_channel_filter_row_offset_offset")
            cls.CHANNEL_FILTER_COUNT_OFFSET = _u32("can_parser_channel_filter_count_offset")

            cls.DIRECTION_INDEX_HEADER_SIZE = _u32("can_parser_direction_index_header_size")
            cls.DIRECTION_INDEX_HEADER_DIRECTION_COUNT_OFFSET = _u32("can_parser_direction_index_header_direction_count_offset")
            cls.DIRECTION_INDEX_HEADER_MAX_DIRECTIONS_OFFSET = _u32("can_parser_direction_index_header_max_directions_offset")
            cls.DIRECTION_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET = _u32("can_parser_direction_index_header_max_row_pool_size_offset")

            cls.DIRECTION_FILTER_SIZE = _u32("can_parser_direction_filter_size")
            cls.DIRECTION_FILTER_DIRECTION_OFFSET = _u32("can_parser_direction_filter_direction_offset")
            cls.DIRECTION_FILTER_ROW_OFFSET_OFFSET = _u32("can_parser_direction_filter_row_offset_offset")
            cls.DIRECTION_FILTER_COUNT_OFFSET = _u32("can_parser_direction_filter_count_offset")
        except Exception:
            return


_ENTRY_SIZE: int = ParsedEntryLayout.ENTRY_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# FieldLayout struct (mirrors C++ FieldLayout, #pragma pack(1))
# Python tokenises the FIRST matched line, determines token positions,
# and passes this struct to C++ so it reads fields at exact positions.
# ─────────────────────────────────────────────────────────────────────────────
class FieldLayout(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("idx_ts",   ctypes.c_int32),   # timestamp token index
        ("idx_id",   ctypes.c_int32),   # CAN ID token index
        ("idx_dir",  ctypes.c_int32),   # direction index (negative = from end)
        ("idx_dlc",  ctypes.c_int32),   # DLC token index
        ("idx_data", ctypes.c_int32),   # first data byte token index
        ("idx_chan",  ctypes.c_int32),   # channel token index (-1 = none)
        ("flags",    ctypes.c_uint32),  # FL_TS_IS_MS | FL_DLC_IS_HEX | FL_IS_TAB
    ]

FL_TS_IS_MS   = 1 << 0   # timestamp is integer ms → ×0.001
FL_DLC_IS_HEX = 1 << 1   # DLC may be single hex digit → CANFD_DLC_MAP
FL_IS_TAB     = 1 << 2   # line is TAB-separated (data column has spaces inside)


# ─────────────────────────────────────────────────────────────────────────────
# CAN-ID filter struct  (mirrors C++ CANIDFilter, #pragma pack(1))
# ─────────────────────────────────────────────────────────────────────────────
class CANIDFilter(ctypes.Structure):
    """Mirrors C++ CANIDFilter (36 bytes, packed)."""
    _pack_ = 1
    _fields_ = [
        ("can_id",      ctypes.c_uint32),   #  4  CAN ID (hex-parsed integer)
        ("row_offset",  ctypes.c_uint64),   #  8  start index into row-index pool
        ("changed_row_offset", ctypes.c_uint64),  # 8 start index into changed-row-index pool
        ("ts_offset",   ctypes.c_uint64),   #  8  start index into timestamp pool (double units)
        ("count",       ctypes.c_uint32),   #  4  element count for row/timestamp pools
        ("changed_count", ctypes.c_uint32), #  4  element count for changed-row pool
    ]  # total: 36 bytes

_CANID_FILTER_SIZE: int = ctypes.sizeof(CANIDFilter)  # 36


# ─────────────────────────────────────────────────────────────────────────────
# mmap IPC constants  (mirror C++ enums in can_parser.cpp)
# ─────────────────────────────────────────────────────────────────────────────
# data.mmap layout — 32-byte header + ParsedEntry array
#   offset  0 : uint64  write_count  (C++ increments after writing each entry)
#   offset  8 : uint32  capacity     (set at creation, read-only after that)
#   offset 12 : uint32  status       (ParserStatus, written by C++)
#   offset 16 : 16 bytes padding
#   offset 32 : ParsedEntry[capacity]
DATA_HEADER_SIZE = ParsedEntryLayout.DATA_HEADER_SIZE
_HDR_STRUCT = struct.Struct("<QII16x")  # write_count(8) capacity(4) status(4) pad(16)

PARSER_STATUS_RUNNING = 0
PARSER_STATUS_DONE = 1
PARSER_STATUS_ERROR = 2

# CAN-ID index mmap constants
# IndexHeader layout (40 bytes):
#   offset  0 : uint32  can_id_count      (unique CAN IDs written, set after parse)
#   offset  4 : uint32  row_pool_size     (total row indices in pool, set after parse)
#   offset  8 : uint32  changed_row_pool_size (total changed row indices in pool)
#   offset 12 : uint32  ts_pool_size      (total timestamps in pool, set after parse)
#   offset 16 : uint32  max_can_ids       (filter table capacity, set at creation)
#   offset 20 : uint32  max_row_pool_size (row-index pool capacity, set at creation)
#   offset 24 : uint32  max_changed_row_pool_size (changed-row-index pool capacity)
#   offset 28 : uint32  max_ts_pool_size  (timestamp pool capacity, set at creation)
#   offset 32 : uint32  status
#   offset 36 : 4 bytes padding
INDEX_HEADER_SIZE = 40
_IDX_HDR_STRUCT = struct.Struct("<IIIIIIIII4x")  # 9×4 + 4 pad = 40 bytes

MmapHeaderConstract.load_from_native_binding()
ParsedEntryLayout.load_from_native_binding()
IndexMmapLayout.load_from_native_binding()
_ENTRY_SIZE = ParsedEntryLayout.ENTRY_SIZE
DATA_HEADER_SIZE = ParsedEntryLayout.DATA_HEADER_SIZE


# ─────────────────────────────────────────────────────────────────────────────
# MmapData  — Process B (Python UI/Controller): read parsed entries
# ─────────────────────────────────────────────────────────────────────────────
class MmapData:
    """
    Manages the data.mmap IPC channel.

    Process B creates it (``MmapData.create()``), then reads back the parsed
    entries that Process A (C++ worker) appends continuously.
    """

    @classmethod
    def create(cls, path: str, capacity: int = 2_000_000) -> "MmapData":
        """Create and initialise a new data mmap file sized for *capacity* entries."""
        total = DATA_HEADER_SIZE + capacity * _ENTRY_SIZE
        with open(path, "w+b") as f:
            f.write(_HDR_STRUCT.pack(0, capacity, PARSER_STATUS_RUNNING))
            f.seek(total - 1)
            f.write(b"\x00")
        return cls(path)

    def __init__(self, path: str) -> None:
        self._path = path
        self._f    = open(path, "r+b")
        self._mm   = _mmap_mod.mmap(self._f.fileno(), 0)
        hdr = _HDR_STRUCT.unpack_from(self._mm, 0)
        self.capacity: int = hdr[1]

    # ── header reads ────────────────────────────────────────────────────────
    @property
    def write_count(self) -> int:
        """Number of entries fully written by the C++ worker so far."""
        return struct.unpack_from("<Q", self._mm, 0)[0]

    @property
    def status(self) -> int:
        return struct.unpack_from("<I", self._mm, 12)[0]

    @property
    def is_done(self) -> bool:
        return self.status != PARSER_STATUS_RUNNING

    # ── entry reads ─────────────────────────────────────────────────────────
    def read_entry(self, index: int) -> Optional[ParsedEntry]:
        """Read a single entry by absolute 0-based index."""
        if index >= self.capacity:
            return None
        e      = ParsedEntry()
        offset = DATA_HEADER_SIZE + index * _ENTRY_SIZE
        ctypes.memmove(
            ctypes.byref(e),
            (ctypes.c_char * _ENTRY_SIZE).from_buffer(self._mm, offset),
            _ENTRY_SIZE,
        )
        return e

    def iter_new_entries(self, from_index: int = 0) -> Generator[ParsedEntry, None, None]:
        """Yield all entries from *from_index* up to the current write_count."""
        end = self.write_count
        for i in range(from_index, end):
            e = self.read_entry(i)
            if e is not None:
                yield e

    # ── lifecycle ───────────────────────────────────────────────────────────
    def close(self) -> None:
        self._mm.close()
        self._f.close()

    def __enter__(self)    -> "MmapData": return self
    def __exit__(self, *_) -> None:       self.close()


# ─────────────────────────────────────────────────────────────────────────────
# IndexMmapData  — CAN-ID index mmap (written by C++ worker, read by Python)
# ─────────────────────────────────────────────────────────────────────────────
class IndexMmapData:
    """
    Manages the CAN-ID index mmap.

    Layout (pre-created by Python):
        [0..39]                     IndexHeader (40 bytes)
            [40 .. 40+max_can_ids*36-1] CANIDFilter[max_can_ids]
            [..]                        uint32_t row_index_pool[max_row_pool_size]
            [..]                        uint32_t changed_row_index_pool[max_changed_row_pool_size]
            [..]                        double timestamp_pool[max_ts_pool_size]
    """

    @classmethod
    def create(cls,
               path: str,
               max_can_ids: int  = 4096,
               max_pool_size: int = 2_000_000) -> "IndexMmapData":
        """Create and initialise a new index mmap file."""
        max_row_pool_size = max_pool_size
        max_changed_row_pool_size = max_pool_size
        max_ts_pool_size = max_pool_size
        total = (INDEX_HEADER_SIZE
                 + max_can_ids  * _CANID_FILTER_SIZE
                 + max_row_pool_size * 4
                 + max_changed_row_pool_size * 4
                 + max_ts_pool_size * 8)
        with open(path, "w+b") as f:
            f.write(_IDX_HDR_STRUCT.pack(
                0,
                0,
                0,
                0,
                max_can_ids,
                max_row_pool_size,
                max_changed_row_pool_size,
                max_ts_pool_size,
                PARSER_STATUS_RUNNING,
            ))
            f.seek(total - 1)
            f.write(b"\x00")
        return cls(path)

    def __init__(self, path: str) -> None:
        self._path = path
        self._f    = open(path, "r+b")
        self._mm   = _mmap_mod.mmap(self._f.fileno(), 0)
        hdr = _IDX_HDR_STRUCT.unpack_from(self._mm, 0)
        self.max_can_ids:       int = hdr[4]
        self.max_row_pool_size: int = hdr[5]
        self.max_changed_row_pool_size: int = hdr[6]
        self.max_ts_pool_size:  int = hdr[7]

    def _row_pool_base(self) -> int:
        return INDEX_HEADER_SIZE + self.max_can_ids * _CANID_FILTER_SIZE

    def _ts_pool_base(self) -> int:
        return self._changed_row_pool_base() + self.max_changed_row_pool_size * 4

    def _changed_row_pool_base(self) -> int:
        return self._row_pool_base() + self.max_row_pool_size * 4

    # ── header reads ───────────────────────────────────────────────────
    @property
    def can_id_count(self) -> int:
        return struct.unpack_from("<I", self._mm, 0)[0]

    @property
    def pool_size(self) -> int:
        return self.row_pool_size

    @property
    def row_pool_size(self) -> int:
        return struct.unpack_from("<I", self._mm, 4)[0]

    @property
    def ts_pool_size(self) -> int:
        return struct.unpack_from("<I", self._mm, 12)[0]

    @property
    def changed_row_pool_size(self) -> int:
        return struct.unpack_from("<I", self._mm, 8)[0]

    @property
    def status(self) -> int:
        return struct.unpack_from("<I", self._mm, 32)[0]

    @property
    def is_done(self) -> bool:
        return self.status != PARSER_STATUS_RUNNING

    # ── lookup ─────────────────────────────────────────────────────
    def get_row_indices(self, can_id: int) -> List[int]:
        """
        Return the list of raw-mmap row indices for *can_id*, or [] if not found.
        O(can_id_count) scan — fast enough for ≤4096 unique IDs.
        """
        n = self.can_id_count
        filter_base = INDEX_HEADER_SIZE
        row_pool_base = self._row_pool_base()

        for i in range(n):
            off = filter_base + i * _CANID_FILTER_SIZE
            fid, row_off, _, _, fcnt, _ = struct.unpack_from("<IQQQII", self._mm, off)
            if fid == can_id:
                pool_off = row_pool_base + row_off * 4
                indices  = list(struct.unpack_from(f"<{fcnt}I", self._mm, pool_off))
                return indices
        return []

    def get_changed_row_indices(self, can_id: int) -> List[int]:
        """Return row indices where ParsedEntry.changed == 1 for *can_id*, or []."""
        n = self.can_id_count
        filter_base = INDEX_HEADER_SIZE
        changed_row_pool_base = self._changed_row_pool_base()

        for i in range(n):
            off = filter_base + i * _CANID_FILTER_SIZE
            fid, _, changed_row_off, _, _, changed_cnt = struct.unpack_from("<IQQQII", self._mm, off)
            if fid == can_id:
                pool_off = changed_row_pool_base + changed_row_off * 4
                indices = list(struct.unpack_from(f"<{changed_cnt}I", self._mm, pool_off))
                return indices
        return []

    def get_timestamps(self, can_id: int) -> List[float]:
        """Return the list of parsed timestamps (double) for *can_id*, or [] if not found."""
        n = self.can_id_count
        filter_base = INDEX_HEADER_SIZE
        ts_pool_base = self._ts_pool_base()

        for i in range(n):
            off = filter_base + i * _CANID_FILTER_SIZE
            fid, _, _, ts_off, fcnt, _ = struct.unpack_from("<IQQQII", self._mm, off)
            if fid == can_id:
                pool_off = ts_pool_base + ts_off * 8
                timestamps = list(struct.unpack_from(f"<{fcnt}d", self._mm, pool_off))
                return timestamps
        return []

    def iter_filters(self) -> Generator["CANIDFilter", None, None]:
        """Yield all CANIDFilter entries written by C++."""
        n = self.can_id_count
        filter_base = INDEX_HEADER_SIZE
        for i in range(n):
            off = filter_base + i * _CANID_FILTER_SIZE
            f   = CANIDFilter()
            ctypes.memmove(
                ctypes.byref(f),
                (ctypes.c_char * _CANID_FILTER_SIZE).from_buffer(self._mm, off),
                _CANID_FILTER_SIZE,
            )
            yield f

    # ── lifecycle ────────────────────────────────────────────────────
    def close(self) -> None:
        self._mm.close()
        self._f.close()

    def __enter__(self)    -> "IndexMmapData": return self
    def __exit__(self, *_) -> None:            self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Library wrapper
# ─────────────────────────────────────────────────────────────────────────────
class CanParserLib:
    """Thin wrapper around the native can_parser C++ functions."""

    _instance: "CanParserLib | None" = None

    def __init__(self) -> None:
        self._lib = load_library()
        self._bind_symbols()

    # ── lazy singleton ──────────────────────────────────────────────────────
    @classmethod
    def get(cls) -> "CanParserLib":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── symbol binding ──────────────────────────────────────────────────────
    def _bind_symbols(self) -> None:
        lib = self._lib

        if hasattr(lib, "can_parser_open"):
            lib.can_parser_open.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
            ]
            lib.can_parser_open.restype = CanParserHandlePtr

        if hasattr(lib, "can_parser_close"):
            lib.can_parser_close.argtypes = [CanParserHandlePtr]
            lib.can_parser_close.restype = None

        if hasattr(lib, "can_parser_get_write_count"):
            lib.can_parser_get_write_count.argtypes = [CanParserHandlePtr]
            lib.can_parser_get_write_count.restype = ctypes.c_uint64

        if hasattr(lib, "can_parser_get_status"):
            lib.can_parser_get_status.argtypes = [CanParserHandlePtr]
            lib.can_parser_get_status.restype = ctypes.c_int32

        if os.name == "nt":
            if hasattr(lib, "set_event_handle"):
                lib.set_event_handle.argtypes = [ctypes.c_void_p]
                lib.set_event_handle.restype = ctypes.c_void_p
            if hasattr(lib, "can_parser_get_event_handle"):
                lib.can_parser_get_event_handle.argtypes = [CanParserHandlePtr]
                lib.can_parser_get_event_handle.restype = ctypes.c_void_p
        else:
            if hasattr(lib, "set_event_fd"):
                lib.set_event_fd.argtypes = [ctypes.c_int]
                lib.set_event_fd.restype = ctypes.c_int
            if hasattr(lib, "can_parser_get_event_fd"):
                lib.can_parser_get_event_fd.argtypes = [CanParserHandlePtr]
                lib.can_parser_get_event_fd.restype = ctypes.c_int

        if hasattr(lib, "get_status"):
            lib.get_status.argtypes = []
            lib.get_status.restype = ctypes.c_int32

        # int32_t can_parser_parse_file(const char* path,
        #                               ParsedEntry** out_entries,
        #                               uint32_t* out_count)
        lib.can_parser_parse_file.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.POINTER(ParsedEntry)),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.can_parser_parse_file.restype = ctypes.c_int32

        # int32_t can_parser_parse_file_with_fmt(const char* path,
        #                                        int32_t fmt,
        #                                        ParsedEntry** out_entries,
        #                                        uint32_t* out_count)
        lib.can_parser_parse_file_with_fmt.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.POINTER(ParsedEntry)),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.can_parser_parse_file_with_fmt.restype = ctypes.c_int32

        # int32_t can_parser_parse_line(const char* line,
        #                               uint32_t line_num,
        #                               ParsedEntry* out)
        lib.can_parser_parse_line.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.POINTER(ParsedEntry),
        ]
        lib.can_parser_parse_line.restype = ctypes.c_int32

        # void can_parser_free_entries(ParsedEntry* ptr)
        lib.can_parser_free_entries.argtypes = [ctypes.POINTER(ParsedEntry)]
        lib.can_parser_free_entries.restype  = None

        # int32_t can_parser_run_worker_segmented(const char* file_path,
        #                                         const char* base_path,
        #                                         int32_t fmt)
        lib.can_parser_run_worker_segmented.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int32,
        ]
        lib.can_parser_run_worker_segmented.restype = ctypes.c_int32

        if hasattr(lib, "parsed_entry_handler_create"):
            lib.parsed_entry_handler_create.argtypes = [ctypes.c_char_p]
            lib.parsed_entry_handler_create.restype = ParsedEntryHandlerHandlePtr

        if hasattr(lib, "parsed_entry_handler_destroy"):
            lib.parsed_entry_handler_destroy.argtypes = [ParsedEntryHandlerHandlePtr]
            lib.parsed_entry_handler_destroy.restype = None

        if hasattr(lib, "parsed_entry_handler_open"):
            lib.parsed_entry_handler_open.argtypes = [ParsedEntryHandlerHandlePtr]
            lib.parsed_entry_handler_open.restype = ctypes.c_int32

        if hasattr(lib, "parsed_entry_handler_write"):
            lib.parsed_entry_handler_write.argtypes = [
                ParsedEntryHandlerHandlePtr,
                ctypes.POINTER(ParsedEntry),
                ctypes.c_uint32,
            ]
            lib.parsed_entry_handler_write.restype = ctypes.c_int32

        if hasattr(lib, "parsed_entry_handler_close"):
            lib.parsed_entry_handler_close.argtypes = [ParsedEntryHandlerHandlePtr]
            lib.parsed_entry_handler_close.restype = ctypes.c_int32

        if hasattr(lib, "can_parser_run_worker_dummy"):
            # legacy optional symbol
            lib.can_parser_run_worker_dummy.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int32,
                ctypes.c_uint32,
            ]
            lib.can_parser_run_worker_dummy.restype = ctypes.c_int32

    # ── public API ───────────────────────────────────────────────────────────
    def parse_file(self, path: str) -> List[ParsedEntry]:
        """
        Parse an entire CAN log file synchronously (auto-detect format).
        Returns a list of ParsedEntry (copied from C heap; C memory freed).
        """
        entries_ptr = ctypes.POINTER(ParsedEntry)()
        count       = ctypes.c_uint32(0)
        rc = self._lib.can_parser_parse_file(
            path.encode("utf-8"),
            ctypes.byref(entries_ptr),
            ctypes.byref(count),
        )
        if rc != 0 or count.value == 0:
            return []
        return self._copy_and_free(entries_ptr, count.value)

    def parse_file_with_fmt(self, path: str, fmt: int) -> List[ParsedEntry]:
        """
        Parse an entire CAN log file using a known FormatType (1..8).
        Python detects the format first, then calls this so C++ skips detection.
        """
        entries_ptr = ctypes.POINTER(ParsedEntry)()
        count       = ctypes.c_uint32(0)
        rc = self._lib.can_parser_parse_file_with_fmt(
            path.encode("utf-8"),
            ctypes.c_int32(fmt),
            ctypes.byref(entries_ptr),
            ctypes.byref(count),
        )
        if rc != 0 or count.value == 0:
            return []
        return self._copy_and_free(entries_ptr, count.value)

    def _copy_and_free(self, entries_ptr, n: int) -> List[ParsedEntry]:
        """Copy n entries from C heap into Python list, then free C memory."""
        result: List[ParsedEntry] = []
        try:
            for i in range(n):
                e = ParsedEntry()
                ctypes.memmove(
                    ctypes.byref(e),
                    ctypes.byref(entries_ptr[i]),
                    ctypes.sizeof(ParsedEntry),
                )
                result.append(e)
        finally:
            self._lib.can_parser_free_entries(entries_ptr)
        return result

    def parse_line(self, line: str, line_num: int = 0) -> Optional[ParsedEntry]:
        """Parse a single text line. Returns ParsedEntry or None on failure."""
        out = ParsedEntry()
        rc  = self._lib.can_parser_parse_line(
            line.encode("utf-8"),
            ctypes.c_uint32(line_num),
            ctypes.byref(out),
        )
        return out if rc == 1 else None

    def set_event_fd(self, event_fd: int) -> int:
        if os.name == "nt" or not hasattr(self._lib, "set_event_fd"):
            return -1
        return int(self._lib.set_event_fd(ctypes.c_int(int(event_fd))))

    def get_status(self) -> int:
        if not hasattr(self._lib, "get_status"):
            return PARSER_STATUS_ERROR
        return int(self._lib.get_status())

    def open_monitor(self, data_base_path: str, index_base_path: str = "") -> "CanParserMonitor | None":
        if not hasattr(self._lib, "can_parser_open"):
            return None

        handle = self._lib.can_parser_open(
            str(data_base_path).encode("utf-8"),
            str(index_base_path).encode("utf-8"),
        )
        if not handle:
            return None
        return CanParserMonitor(self._lib, handle)


class CanParserMonitor:
    def __init__(self, lib, handle) -> None:
        self._lib = lib
        self._handle = handle
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._lib.can_parser_close(self._handle)

    @property
    def closed(self) -> bool:
        return self._closed

    def get_write_count(self) -> int:
        if self._closed:
            return 0
        return int(self._lib.can_parser_get_write_count(self._handle))

    def get_status(self) -> int:
        if self._closed:
            return PARSER_STATUS_ERROR
        return int(self._lib.can_parser_get_status(self._handle))

    def get_wait_source(self) -> int | None:
        if self._closed:
            return None
        if os.name == "nt":
            if not hasattr(self._lib, "can_parser_get_event_handle"):
                return None
            handle = self._lib.can_parser_get_event_handle(self._handle)
            return int(handle) if handle else None

        if not hasattr(self._lib, "can_parser_get_event_fd"):
            return None
        fd = int(self._lib.can_parser_get_event_fd(self._handle))
        return fd if fd >= 0 else None

    def __enter__(self) -> "CanParserMonitor":
        return self

    def __exit__(self, *_) -> None:
        self.close()


class ParsedEntryHandlerClient:
    """Python wrapper over native ParsedEntryHandler opaque handle."""

    def __init__(self, token_id: str) -> None:
        self._lib = CanParserLib.get()._lib
        self._handle = None
        self._opened = False

        if not hasattr(self._lib, "parsed_entry_handler_create"):
            raise RuntimeError("Native library does not expose ParsedEntryHandler bridge")

        handle = self._lib.parsed_entry_handler_create(str(token_id).encode("utf-8"))
        if not handle:
            raise RuntimeError(f"Failed to create ParsedEntryHandler for token id: {token_id}")

        self._handle = handle

    def open(self) -> None:
        if self._opened:
            return
        self._require_handle()
        rc = int(self._lib.parsed_entry_handler_open(self._handle))
        if rc != 0:
            raise RuntimeError(f"Failed to open segment writers: error code {rc}")
        self._opened = True

    def write(self, entries: List[ParsedEntry]) -> None:
        if not entries:
            return
        if not self._opened:
            raise RuntimeError("Segment writers not opened or already closed")

        self._require_handle()
        arr_type = ParsedEntry * len(entries)
        arr = arr_type(*entries)
        rc = int(self._lib.parsed_entry_handler_write(
            self._handle,
            ctypes.cast(arr, ctypes.POINTER(ParsedEntry)),
            ctypes.c_uint32(len(entries)),
        ))
        if rc != 0:
            raise RuntimeError(f"Failed to write entries to segmented mmap: error code {rc}")

    def close(self) -> None:
        if self._handle is None:
            return

        if self._opened and hasattr(self._lib, "parsed_entry_handler_close"):
            rc = int(self._lib.parsed_entry_handler_close(self._handle))
            if rc != 0:
                raise RuntimeError(f"Failed to close segment writers: error code {rc}")
            self._opened = False

        if hasattr(self._lib, "parsed_entry_handler_destroy"):
            self._lib.parsed_entry_handler_destroy(self._handle)
        self._handle = None

    def _require_handle(self) -> None:
        if self._handle is None:
            raise RuntimeError("ParsedEntryHandler is closed")

    def __enter__(self) -> "ParsedEntryHandlerClient":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
