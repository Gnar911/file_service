"""
can_decoder_api.py

Python ctypes binding for the C++ CAN signal decoder (can_decoder.cpp).

DBC metadata is passed ONCE to C++ via ``can_decoder_load_db()`` and stored
in C++ heap memory.  The hot-path ``can_decoder_run()`` never reads DBC
from disk — zero file I/O for the DB during decode.

Workflow
--------
1. Python parses the DBC with cantools → ``DecodeDB.load(candb_info)``
   builds ctypes arrays in memory and calls ``can_decoder_load_db()``.
2. After the parser has fully written data.mmap (can_parser_run_worker),
   Python pre-creates the two output mmaps via
   ``SignalDirMmap.create()`` and ``SignalSampleMmap.create()``.
3. ``lib.decode()`` calls the C++ hot-path ``can_decoder_run()`` which:
     • reads data.mmap  (parsed CAN entries)
     • uses the pre-loaded DBC from C++ memory
     • writes signal_dir.mmap  (directory: (can_id, signal_id) → offset, count)
     • writes signal_sample.mmap  (samples: row_index, phys, raw)
4. Python reads back the results from the output mmaps.

Usage
-----
    from native_sdk.can_decoder_api import (
        DecodeDB, SignalDirMmap, SignalSampleMmap, CanDecoderLib,
        estimate_sample_count,
    )

    # Step 1 — load DBC signal definitions into C++ memory (once)
    decode_db = DecodeDB.load(candb_info)

    # Step 2 — estimate sizes
    n_dir, n_samples = estimate_sample_count(index_mmap, decode_db)

    # Step 3 — create output mmaps (SoA layout)
    sig_dir  = SignalDirMmap.create("/tmp/signal_dir.mmap", n_dir)
    ridx_chg = RowIndexChangedMmap.create("/tmp/row_index_changed.mmap", n_samples)
    ridx_mm  = RowIndexMmap.create("/tmp/row_index.mmap", n_samples)
    val_mm   = ValueMmap.create("/tmp/value.mmap", n_samples)
    raw_mm   = RawValueMmap.create("/tmp/rawvalue.mmap", n_samples)

    # Step 4 — run C++ decoder
    lib = CanDecoderLib.get()
    rc  = lib.decode("/tmp/data.mmap", "/tmp/signal_dir.mmap",
                     "/tmp/row_index_changed.mmap", "/tmp/row_index.mmap",
                     "/tmp/value.mmap", "/tmp/rawvalue.mmap")

    # Step 5 — read results
    for entry in sig_dir.iter_entries():
        for i in range(entry.sample_count):
            pos = entry.index_offset + i
            row = ridx_mm.read(pos)
            val = val_mm.read(pos)
            raw = raw_mm.read(pos)
"""
from __future__ import annotations

import ctypes
import mmap as _mmap_mod
import struct
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

from file_service.native_loader import load_library

# ─────────────────────────────────────────────────────────────────────────────
# Status constants (shared across all decoder mmaps)
# ─────────────────────────────────────────────────────────────────────────────
DECODE_STATUS_RUNNING = 0
DECODE_STATUS_DONE    = 1
DECODE_STATUS_ERROR   = 2

# ═══════════════════════════════════════════════════════════════════════════
# Packed ctypes structs — must match can_decoder.cpp exactly
# ═══════════════════════════════════════════════════════════════════════════

# ── DBC definitions (MessageDef / SignalDef — passed to C++ once) ────────

class MessageDefC(ctypes.Structure):
    """16 bytes."""
    _pack_ = 1
    _fields_ = [
        ("can_id",         ctypes.c_uint32),   #  4
        ("signal_count",   ctypes.c_uint16),   #  2
        ("msg_length",     ctypes.c_uint16),   #  2
        ("signal_offset",  ctypes.c_uint32),   #  4
        ("padding",        ctypes.c_uint32),   #  4
    ]

class SignalDefC(ctypes.Structure):
    """32 bytes."""
    _pack_ = 1
    _fields_ = [
        ("start_bit",    ctypes.c_uint16),      #  2
        ("bit_length",   ctypes.c_uint16),      #  2
        ("byte_order",   ctypes.c_uint8),       #  1  0=LE, 1=BE
        ("is_signed",    ctypes.c_uint8),       #  1
        ("has_choices",  ctypes.c_uint8),       #  1
        ("padding1",     ctypes.c_uint8),       #  1
        ("scale",        ctypes.c_double),      #  8
        ("offset",       ctypes.c_double),      #  8
        ("padding2",     ctypes.c_uint8 * 8),   #  8
    ]

_MESSAGE_DEF_SIZE   = ctypes.sizeof(MessageDefC)       # 16
_SIGNAL_DEF_SIZE    = ctypes.sizeof(SignalDefC)         # 32


# ── signal_dir.mmap ─────────────────────────────────────────────────────

class SignalDirHeader(ctypes.Structure):
    """32 bytes."""
    _pack_ = 1
    _fields_ = [
        ("entry_count", ctypes.c_uint32),       #  4
        ("status",      ctypes.c_uint32),       #  4
        ("padding",     ctypes.c_uint8 * 24),   # 24
    ]

class SignalDirectoryEntry(ctypes.Structure):
    """52 bytes."""
    _pack_ = 1
    _fields_ = [
        ("can_id",          ctypes.c_uint32),     #  4
        ("signal_id",       ctypes.c_uint16),     #  2
        ("padding",         ctypes.c_uint16),     #  2
        ("index_offset",    ctypes.c_uint64),     #  8  offset into row_index.mmap
        ("value_offset",    ctypes.c_uint64),     #  8  offset into value.mmap
        ("rawvalue_offset", ctypes.c_uint64),     #  8  offset into rawvalue.mmap
        ("changed_index_offset", ctypes.c_uint64),#  8  offset into row_index_changed.mmap
        ("sample_count",    ctypes.c_uint32),     #  4
        ("changed_sample_count", ctypes.c_uint32),#  4
        ("signal_count",    ctypes.c_uint16),     #  2  total signals for this CAN message
        ("padding2",        ctypes.c_uint16),     #  2
    ]

_SIG_DIR_HDR_SIZE   = ctypes.sizeof(SignalDirHeader)          # 32
_SIG_DIR_ENTRY_SIZE = ctypes.sizeof(SignalDirectoryEntry)     # 52


# ── row_index_changed.mmap / row_index.mmap / value.mmap / rawvalue.mmap (SoA output arrays) ────
# All four share the same header layout.

class SoAHeaderC(ctypes.Structure):
    """32 bytes — shared header for row_index_changed.mmap, row_index.mmap, value.mmap, rawvalue.mmap."""
    _pack_ = 1
    _fields_ = [
        ("sample_count", ctypes.c_uint64),      #  8
        ("capacity",     ctypes.c_uint32),      #  4
        ("status",       ctypes.c_uint32),      #  4
        ("padding",      ctypes.c_uint8 * 16),  # 16
    ]

_SOA_HDR_SIZE        = ctypes.sizeof(SoAHeaderC)   # 32
_ROW_INDEX_ELEM_SIZE = 4   # uint32
_VALUE_ELEM_SIZE     = 8   # float64
_RAWVALUE_ELEM_SIZE  = 8   # int64


def _resolve_segment_paths(path: str) -> List[str]:
    base = Path(path)
    if base.exists():
        return [str(base)]
    stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
    segs = sorted(base.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))
    return [str(p) for p in segs]


# ═══════════════════════════════════════════════════════════════════════════
# DecodeDB — load DBC definitions into C++ memory (no mmap file)
# ═══════════════════════════════════════════════════════════════════════════

class DecodeDB:
    """
    Parses a cantools CANDBInfo and passes the resulting MessageDef / SignalDef
    arrays to C++ via ``can_decoder_load_db()``.  The C++ side deep-copies
    the data into heap memory — Python arrays can be freed afterwards.

    Also keeps a Python-side ``_msg_sig_count`` dict so
    ``estimate_sample_count()`` can compute output sizes without touching C++.
    """

    @classmethod
    def load(cls, candb_info) -> "DecodeDB":
        """
        Build ctypes arrays from a ``CANDBInfo`` (cantools wrapper), pass them
        to C++ once, and return a ``DecodeDB`` handle.
        """
        messages_raw = []  # list of (can_id, msg_length, sorted_signals)

        for msg in candb_info.db.messages:
            can_id     = msg.frame_id
            msg_length = msg.length
            sorted_sigs = sorted(msg.signals, key=lambda s: s.name)
            signals = []
            for sig in sorted_sigs:
                byte_order  = 0 if sig.byte_order == "little_endian" else 1
                is_signed   = 1 if sig.is_signed else 0
                has_choices = 1 if sig.choices else 0
                scale       = float(sig.scale) if sig.scale else 1.0
                offset      = float(sig.offset) if sig.offset else 0.0
                signals.append((sig.start, sig.length, byte_order,
                                is_signed, has_choices, scale, offset))
            messages_raw.append((can_id, msg_length, signals))

        msg_count       = len(messages_raw)
        total_sig_count = sum(len(sigs) for _, _, sigs in messages_raw)

        # Build ctypes arrays
        MsgArray = MessageDefC * msg_count
        SigArray = SignalDefC  * total_sig_count
        msg_arr  = MsgArray()
        sig_arr  = SigArray()

        sig_offset = 0
        msg_sig_count: Dict[int, int] = {}

        for mi, (can_id, msg_length, sigs) in enumerate(messages_raw):
            md = msg_arr[mi]
            md.can_id        = can_id
            md.signal_count  = len(sigs)
            md.msg_length    = msg_length
            md.signal_offset = sig_offset
            md.padding       = 0
            msg_sig_count[can_id] = len(sigs)

            for si, (start_bit, bit_length, byte_order, is_signed, has_choices, scale, offset) in enumerate(sigs):
                sd = sig_arr[sig_offset + si]
                sd.start_bit   = start_bit
                sd.bit_length  = bit_length
                sd.byte_order  = byte_order
                sd.is_signed   = is_signed
                sd.has_choices = has_choices
                sd.padding1    = 0
                sd.scale       = scale
                sd.offset      = offset

            sig_offset += len(sigs)

        # Pass to C++ — deep-copied into C++ heap memory
        lib = CanDecoderLib.get()
        rc = lib.load_db(msg_arr, msg_count, sig_arr, total_sig_count)
        if rc != 0:
            raise RuntimeError(f"can_decoder_load_db failed: rc={rc}")

        return cls(msg_sig_count, msg_count, total_sig_count)

    def __init__(self, msg_sig_count: Dict[int, int],
                 message_count: int, total_signal_count: int) -> None:
        self._msg_sig_count    = msg_sig_count
        self.message_count     = message_count
        self.total_signal_count = total_signal_count

    def get_signal_count(self, can_id: int) -> int:
        """Return number of signals for *can_id*, or 0 if not in DB."""
        return self._msg_sig_count.get(can_id, 0)

    @property
    def can_ids(self) -> List[int]:
        return list(self._msg_sig_count.keys())

    def close(self) -> None:
        """Release C++ memory holding the DBC data."""
        try:
            lib = CanDecoderLib.get()
            lib.free_db()
        except Exception:
            pass

    def __enter__(self)    -> "DecodeDB": return self
    def __exit__(self, *_) -> None:       self.close()


# ═══════════════════════════════════════════════════════════════════════════
# SignalDirMmap — read signal directory written by C++ (unchanged layout)
# ═══════════════════════════════════════════════════════════════════════════

class SignalDirMmap:
    """
    Layout:
      [0..31]                              SignalDirHeader  (32 B)
      [32 .. 32 + max_entries*24 - 1]     SignalDirectoryEntry[]
    """

    @classmethod
    def create(cls, path: str, max_entries: int) -> "SignalDirMmap":
        total = _SIG_DIR_HDR_SIZE + max_entries * _SIG_DIR_ENTRY_SIZE
        with open(path, "w+b") as f:
            hdr = SignalDirHeader()
            hdr.entry_count = 0
            hdr.status      = DECODE_STATUS_RUNNING
            f.write(bytes(hdr))
            f.seek(total - 1)
            f.write(b"\x00")
        return cls(path)

    def __init__(self, path: str) -> None:
        self._path = path
        self._paths = _resolve_segment_paths(path)
        if not self._paths:
            raise FileNotFoundError(path)
        self._files = [open(p, "r+b") for p in self._paths]
        self._mms = [_mmap_mod.mmap(f.fileno(), 0) for f in self._files]

    @property
    def entry_count(self) -> int:
        return sum(struct.unpack_from("<I", mm, 0)[0] for mm in self._mms)

    @property
    def status(self) -> int:
        return DECODE_STATUS_RUNNING if any(struct.unpack_from("<I", mm, 4)[0] == DECODE_STATUS_RUNNING for mm in self._mms) else DECODE_STATUS_DONE

    @property
    def is_done(self) -> bool:
        return self.status != DECODE_STATUS_RUNNING

    def read_entry(self, index: int) -> Optional[SignalDirectoryEntry]:
        idx = int(index)
        for mm in self._mms:
            n = struct.unpack_from("<I", mm, 0)[0]
            if idx >= n:
                idx -= n
                continue
            off = _SIG_DIR_HDR_SIZE + idx * _SIG_DIR_ENTRY_SIZE
            if off + _SIG_DIR_ENTRY_SIZE > len(mm):
                return None
            e = SignalDirectoryEntry()
            ctypes.memmove(
                ctypes.byref(e),
                (ctypes.c_char * _SIG_DIR_ENTRY_SIZE).from_buffer(mm, off),
                _SIG_DIR_ENTRY_SIZE,
            )
            return e
        return None

    def iter_entries(self) -> Generator[SignalDirectoryEntry, None, None]:
        n = self.entry_count
        for i in range(n):
            e = self.read_entry(i)
            if e is not None:
                yield e

    def close(self) -> None:
        for mm in self._mms:
            mm.close()
        for f in self._files:
            f.close()

    def __enter__(self)    -> "SignalDirMmap": return self
    def __exit__(self, *_) -> None:            self.close()


# ═══════════════════════════════════════════════════════════════════════════
# RowIndexMmap / ValueMmap / RawValueMmap — SoA output arrays
# ═══════════════════════════════════════════════════════════════════════════

def _make_soa_mmap(path: str, capacity: int, elem_size: int) -> None:
    """Create a blank SoA mmap file (SoAHeaderC + capacity * elem_size bytes)."""
    total = _SOA_HDR_SIZE + capacity * elem_size
    with open(path, "w+b") as f:
        hdr = SoAHeaderC()
        hdr.sample_count = 0
        hdr.capacity     = capacity
        hdr.status       = DECODE_STATUS_RUNNING
        f.write(bytes(hdr))
        f.seek(total - 1)
        f.write(b"\x00")


class RowIndexMmap:
    """
    Layout:
      [0..31]                            SoAHeaderC  (32 B)
      [32 .. 32 + capacity*4 - 1]       uint32_t[]  row indices into data.mmap
    """

    @classmethod
    def create(cls, path: str, capacity: int) -> "RowIndexMmap":
        _make_soa_mmap(path, capacity, _ROW_INDEX_ELEM_SIZE)
        return cls(path)

    def __init__(self, path: str) -> None:
        self._path = path
        self._paths = _resolve_segment_paths(path)
        if not self._paths:
            raise FileNotFoundError(path)
        self._files = [open(p, "r+b") for p in self._paths]
        self._mms = [_mmap_mod.mmap(f.fileno(), 0) for f in self._files]
        self._caps = [struct.unpack_from("<I", mm, 8)[0] for mm in self._mms]

    @property
    def sample_count(self) -> int:
        return sum(struct.unpack_from("<Q", mm, 0)[0] for mm in self._mms)

    @property
    def capacity(self) -> int:
        return sum(self._caps)

    @property
    def status(self) -> int:
        return DECODE_STATUS_RUNNING if any(struct.unpack_from("<I", mm, 12)[0] == DECODE_STATUS_RUNNING for mm in self._mms) else DECODE_STATUS_DONE

    @property
    def is_done(self) -> bool:
        return self.status != DECODE_STATUS_RUNNING

    def read(self, index: int) -> Optional[int]:
        idx = int(index)
        for mm, cap in zip(self._mms, self._caps):
            if idx >= cap:
                idx -= cap
                continue
            off = _SOA_HDR_SIZE + idx * _ROW_INDEX_ELEM_SIZE
            if off + _ROW_INDEX_ELEM_SIZE > len(mm):
                return None
            return struct.unpack_from("<I", mm, off)[0]
        return None

    def close(self) -> None:
        for mm in self._mms:
            mm.close()
        for f in self._files:
            f.close()

    def __enter__(self)    -> "RowIndexMmap": return self
    def __exit__(self, *_) -> None:           self.close()


class RowIndexChangedMmap(RowIndexMmap):
        """
        Layout:
            [0..31]                            SoAHeaderC  (32 B)
            [32 .. 32 + capacity*4 - 1]       uint32_t[]  changed row indices into data.mmap
        """


class ValueMmap:
    """
    Layout:
      [0..31]                            SoAHeaderC  (32 B)
      [32 .. 32 + capacity*8 - 1]       float64[]   physical values
    """

    @classmethod
    def create(cls, path: str, capacity: int) -> "ValueMmap":
        _make_soa_mmap(path, capacity, _VALUE_ELEM_SIZE)
        return cls(path)

    def __init__(self, path: str) -> None:
        self._path = path
        self._paths = _resolve_segment_paths(path)
        if not self._paths:
            raise FileNotFoundError(path)
        self._files = [open(p, "r+b") for p in self._paths]
        self._mms = [_mmap_mod.mmap(f.fileno(), 0) for f in self._files]
        self._caps = [struct.unpack_from("<I", mm, 8)[0] for mm in self._mms]

    @property
    def sample_count(self) -> int:
        return sum(struct.unpack_from("<Q", mm, 0)[0] for mm in self._mms)

    @property
    def capacity(self) -> int:
        return sum(self._caps)

    @property
    def status(self) -> int:
        return DECODE_STATUS_RUNNING if any(struct.unpack_from("<I", mm, 12)[0] == DECODE_STATUS_RUNNING for mm in self._mms) else DECODE_STATUS_DONE

    @property
    def is_done(self) -> bool:
        return self.status != DECODE_STATUS_RUNNING

    def read(self, index: int) -> Optional[float]:
        idx = int(index)
        for mm, cap in zip(self._mms, self._caps):
            if idx >= cap:
                idx -= cap
                continue
            off = _SOA_HDR_SIZE + idx * _VALUE_ELEM_SIZE
            if off + _VALUE_ELEM_SIZE > len(mm):
                return None
            return struct.unpack_from("<d", mm, off)[0]
        return None

    def close(self) -> None:
        for mm in self._mms:
            mm.close()
        for f in self._files:
            f.close()

    def __enter__(self)    -> "ValueMmap": return self
    def __exit__(self, *_) -> None:        self.close()


class RawValueMmap:
    """
    Layout:
      [0..31]                            SoAHeaderC  (32 B)
      [32 .. 32 + capacity*8 - 1]       int64[]     raw decoded integers
    """

    @classmethod
    def create(cls, path: str, capacity: int) -> "RawValueMmap":
        _make_soa_mmap(path, capacity, _RAWVALUE_ELEM_SIZE)
        return cls(path)

    def __init__(self, path: str) -> None:
        self._path = path
        self._paths = _resolve_segment_paths(path)
        if not self._paths:
            raise FileNotFoundError(path)
        self._files = [open(p, "r+b") for p in self._paths]
        self._mms = [_mmap_mod.mmap(f.fileno(), 0) for f in self._files]
        self._caps = [struct.unpack_from("<I", mm, 8)[0] for mm in self._mms]

    @property
    def sample_count(self) -> int:
        return sum(struct.unpack_from("<Q", mm, 0)[0] for mm in self._mms)

    @property
    def capacity(self) -> int:
        return sum(self._caps)

    @property
    def status(self) -> int:
        return DECODE_STATUS_RUNNING if any(struct.unpack_from("<I", mm, 12)[0] == DECODE_STATUS_RUNNING for mm in self._mms) else DECODE_STATUS_DONE

    @property
    def is_done(self) -> bool:
        return self.status != DECODE_STATUS_RUNNING

    def read(self, index: int) -> Optional[int]:
        idx = int(index)
        for mm, cap in zip(self._mms, self._caps):
            if idx >= cap:
                idx -= cap
                continue
            off = _SOA_HDR_SIZE + idx * _RAWVALUE_ELEM_SIZE
            if off + _RAWVALUE_ELEM_SIZE > len(mm):
                return None
            return struct.unpack_from("<q", mm, off)[0]
        return None

    def close(self) -> None:
        for mm in self._mms:
            mm.close()
        for f in self._files:
            f.close()

    def __enter__(self)    -> "RawValueMmap": return self
    def __exit__(self, *_) -> None:           self.close()


# ═══════════════════════════════════════════════════════════════════════════
# Utility: estimate output mmap sizes from index mmap + decode DB
# ═══════════════════════════════════════════════════════════════════════════

def estimate_sample_count(index_mmap, decode_db: DecodeDB) -> Tuple[int, int]:
    """
    Compute the exact number of (directory entries, total samples) needed
    for the output mmaps.

    *index_mmap*: ``IndexMmapData`` from ``can_parser_api`` (has ``iter_filters()``).
    *decode_db*: ``DecodeDB`` (this module).

    Returns ``(n_dir_entries, n_total_samples)``.
    """
    n_dir     = 0
    n_samples = 0
    for filt in index_mmap.iter_filters():
        sig_count = decode_db.get_signal_count(filt.can_id)
        if sig_count == 0:
            continue
        n_dir     += sig_count
        n_samples += filt.count * sig_count
    return n_dir, n_samples


# ═══════════════════════════════════════════════════════════════════════════
# CanDecoderLib — thin wrapper around C++ decoder functions
# ═══════════════════════════════════════════════════════════════════════════

class CanDecoderLib:
    """
    Loads native_sdk_native and binds:
      - ``can_decoder_load_db``  — pass DBC once to C++ memory
      - ``can_decoder_free_db``  — release C++ DBC storage
      - ``can_decoder_run``      — hot-path decode (no DB I/O)
    """

    _instance: Optional["CanDecoderLib"] = None

    def __init__(self) -> None:
        self._lib = load_library()
        self._bind()

    @classmethod
    def get(cls) -> "CanDecoderLib":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _bind(self) -> None:
        lib = self._lib

        # int32_t can_decoder_load_db(const MessageDef* messages,
        #                              uint32_t msg_count,
        #                              const SignalDef* signals,
        #                              uint32_t sig_count)
        lib.can_decoder_load_db.argtypes = [
            ctypes.POINTER(MessageDefC),
            ctypes.c_uint32,
            ctypes.POINTER(SignalDefC),
            ctypes.c_uint32,
        ]
        lib.can_decoder_load_db.restype = ctypes.c_int32

        # void can_decoder_free_db()
        lib.can_decoder_free_db.argtypes = []
        lib.can_decoder_free_db.restype  = None

        # int32_t can_decoder_run(const char* data_path,
        #                         const char* signal_dir_path,
        #                         const char* row_index_changed_path,
        #                         const char* row_index_path,
        #                         const char* value_path,
        #                         const char* rawvalue_path)
        lib.can_decoder_run.argtypes = [
            ctypes.c_char_p,   # data_path
            ctypes.c_char_p,   # signal_dir_path
            ctypes.c_char_p,   # row_index_changed_path
            ctypes.c_char_p,   # row_index_path
            ctypes.c_char_p,   # value_path
            ctypes.c_char_p,   # rawvalue_path
        ]
        lib.can_decoder_run.restype = ctypes.c_int32

    def load_db(self, msg_arr, msg_count: int,
                sig_arr, sig_count: int) -> int:
        """Pass MessageDef / SignalDef arrays to C++ once."""
        return self._lib.can_decoder_load_db(
            msg_arr, msg_count, sig_arr, sig_count,
        )

    def free_db(self) -> None:
        """Release C++ DBC storage."""
        self._lib.can_decoder_free_db()

    def decode(self,
               data_path: str,
               signal_dir_path: str,
               row_index_changed_path: str,
               row_index_path: str,
               value_path: str,
               rawvalue_path: str) -> int:
        """
        Run the C++ decoder (SoA layout).  Returns 0 on success.
        ``can_decoder_load_db`` must have been called first.
        """
        return self._lib.can_decoder_run(
            data_path.encode("utf-8"),
            signal_dir_path.encode("utf-8"),
            row_index_changed_path.encode("utf-8"),
            row_index_path.encode("utf-8"),
            value_path.encode("utf-8"),
            rawvalue_path.encode("utf-8"),
        )
