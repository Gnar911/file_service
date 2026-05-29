from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any, Tuple, Set
from collections import defaultdict
from enum import Enum
from pathlib import Path
import mmap as _mmap
import struct
import heapq
import cantools
from lw.logger_setup import LOG
from can_sdk.data_object import CANLogLine, SignalName


CANID = int
SigID = int
@dataclass
class CANLogDecodedDiskFile:
    path : Path
    decode_verified_size: int = field(default=0)
    decode_mmap_file_count: int = field(default=0)
    decode_current_size: int = field(default=0)
    decode_percent: int = field(default=0)
    # decode_is_loading: bool = field(default=False)
    # decode_state: DecodeLogState = DecodeLogState.UNAVAILABLE
    decode_signal_list: List[Tuple[CANID, SigID]] = field(default_factory=list)

    _DECODE_HDR_SIZE: int = 32
    _DECODE_HDR_STRUCT: Any = field(default=struct.Struct("<QII16x"), init=False, repr=False)
    _SIGNAL_DIR_HDR_STRUCT: Any = field(default=struct.Struct("<II24x"), init=False, repr=False)
    _SIGNAL_DIR_ENTRY_SIZE: int = 52
    _SIGNAL_DIR_ENTRY_STRUCT: Any = field(default=struct.Struct("<IHHQQQQIIHH"), init=False, repr=False)

    # @property
    # def file_name(self) -> str:
    #     if not self.file_path:
    #         return ""
    #     return Path(self.file_path).name

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not str(self.path):
            raise ValueError("path is required")

    @property
    def decode_signal_dir_mmap_path(self) -> str:
        return str(self.path.with_name(f"{self.path.name}.signal_dir.mmap"))

    @property
    def decode_row_index_changed_mmap_path(self) -> str:
        return str(self.path.with_name(f"{self.path.name}.row_index_changed.mmap"))

    @property
    def decode_row_index_mmap_path(self) -> str:
        return str(self.path.with_name(f"{self.path.name}.row_index.mmap"))

    @property
    def decode_value_mmap_path(self) -> str:
        return str(self.path.with_name(f"{self.path.name}.value.mmap"))

    @property
    def decode_rawvalue_mmap_path(self) -> str:
        return str(self.path.with_name(f"{self.path.name}.rawvalue.mmap"))

    @property
    def decode_signal_name_list(self) -> List[SignalName]:
        return [f"{can_id}:{signal_id}" for can_id, signal_id in self.decode_signal_list]

    @property
    def decode_signal_id_list(self) -> List[int]:
        seen: Set[int] = set()
        ordered: List[int] = []
        for _, signal_id in self.decode_signal_list:
            sid = int(signal_id)
            if sid in seen:
                continue
            seen.add(sid)
            ordered.append(sid)
        return ordered

    def _get_decode_signal_entries(
        self,
        signal_id: Optional[int] = None,
        can_id: Optional[int] = None,
    ) -> List[Tuple[int, int, int, int, int, int, int, int]]:
        entries = self._load_decode_signal_directory_entries()
        if signal_id is None and can_id is None:
            return entries

        matched: List[Tuple[int, int, int, int, int, int, int, int]] = []
        sid = int(signal_id) if signal_id is not None else None
        cid = int(can_id) if can_id is not None else None
        for entry in entries:
            entry_can_id, entry_signal_id = int(entry[0]), int(entry[1])
            if sid is not None and entry_signal_id != sid:
                continue
            if cid is not None and entry_can_id != cid:
                continue
            matched.append(entry)
        return matched

    def get_signal_value_list_by_key(self, can_id: int, signal_id: int) -> List[float]:
        return self.get_signal_value_list(signal_id=int(signal_id), can_id=int(can_id))

    def get_signal_rawvalue_list_by_key(self, can_id: int, signal_id: int) -> List[int]:
        return self.get_signal_rawvalue_list(signal_id=int(signal_id), can_id=int(can_id))

    def get_page_from_signal_row_indices_with_rawvalue_list(
        self,
        signal_id: int,
        rvalues: List[int],
        first_line: int = 0,
        page_size: int = 100,
    ) -> List[CANLogLine]:
        return self.get_page_from_signal_ids_row_indices_with_rawvalue_map(
            signal_rawvalues={int(signal_id): [int(v) for v in rvalues]},
            first_line=first_line,
            page_size=page_size,
            match_mode="or",
        )

    def get_signal_value_list(self, signal_id: int, can_id: Optional[int] = None) -> List[float]:
        matched = self._get_decode_signal_entries(signal_id=int(signal_id), can_id=can_id)
        if not matched:
            LOG.debug("get_signal_value_list: no directory entry for signal_id=%s can_id=%s", signal_id, can_id)
            return []

        for m in matched:
            LOG.debug(
                "get_signal_value_list: dir entry can_id=0x%X sig_id=%d idx_off=%d val_off=%d raw_off=%d chg_off=%d sample_count=%d chg_count=%d",
                m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7],
            )

        value_paths = self.decode_value_segment_paths()
        if not value_paths:
            return []

        seg_cache, capacities = self._open_decode_array_segments(value_paths)
        values: List[float] = []
        try:
            for _, _, _, value_off, _, _, sample_count, _ in matched:
                for j in range(sample_count):
                    v = self._decode_read_scalar_from_sample_pos(
                        sample_pos=value_off + j,
                        seg_cache=seg_cache,
                        capacities=capacities,
                        elem_size=8,
                        unpack_fmt="<d",
                    )
                    if v is not None:
                        values.append(float(v))
        finally:
            self._close_seg_cache(seg_cache)
        return values

    def get_signal_rawvalue_list(self, signal_id: int, can_id: Optional[int] = None) -> List[int]:
        matched = self._get_decode_signal_entries(signal_id=int(signal_id), can_id=can_id)
        if not matched:
            LOG.debug("get_signal_rawvalue_list: no directory entry for signal_id=%s can_id=%s", signal_id, can_id)
            return []

        for m in matched:
            LOG.debug(
                "get_signal_rawvalue_list: dir entry can_id=0x%X sig_id=%d sample_count=%d chg_count=%d",
                m[0], m[1], m[6], m[7],
            )

        raw_paths = self.decode_rawvalue_segment_paths()
        if not raw_paths:
            return []

        seg_cache, capacities = self._open_decode_array_segments(raw_paths)
        raw_values: List[int] = []
        try:
            for _, _, _, _, raw_off, _, sample_count, _ in matched:
                for j in range(sample_count):
                    rv = self._decode_read_scalar_from_sample_pos(
                        sample_pos=raw_off + j,
                        seg_cache=seg_cache,
                        capacities=capacities,
                        elem_size=8,
                        unpack_fmt="<q",
                    )
                    if rv is not None:
                        raw_values.append(int(rv))
        finally:
            self._close_seg_cache(seg_cache)
        return raw_values

    def get_signal_changed_row_index_list(self, signal_id: int) -> List[int]:
        entries = self._load_decode_signal_directory_entries()
        matched = [e for e in entries if e[1] == int(signal_id)]
        if not matched:
            return []

        changed_paths = self.decode_row_index_changed_segment_paths()
        if not changed_paths:
            return []

        seg_cache, capacities = self._open_decode_array_segments(changed_paths)
        changed_rows: List[int] = []
        try:
            for _, _, _, _, _, changed_off, _, changed_count in matched:
                for j in range(changed_count):
                    ridx = self._decode_read_scalar_from_sample_pos(
                        sample_pos=changed_off + j,
                        seg_cache=seg_cache,
                        capacities=capacities,
                        elem_size=4,
                        unpack_fmt="<I",
                    )
                    if ridx is not None:
                        changed_rows.append(int(ridx))
        finally:
            self._close_seg_cache(seg_cache)
        return changed_rows

    def decode_signal_dir_segment_paths(self) -> List[Path]:
        return self._decode_segment_paths(self.decode_signal_dir_mmap_path)

    def decode_row_index_segment_paths(self) -> List[Path]:
        return self._decode_segment_paths(self.decode_row_index_mmap_path)

    def decode_row_index_changed_segment_paths(self) -> List[Path]:
        return self._decode_segment_paths(self.decode_row_index_changed_mmap_path)

    def decode_value_segment_paths(self) -> List[Path]:
        return self._decode_segment_paths(self.decode_value_mmap_path)

    def decode_rawvalue_segment_paths(self) -> List[Path]:
        return self._decode_segment_paths(self.decode_rawvalue_mmap_path)

    def _decode_segment_paths(self, base_path: str) -> List[Path]:
        if not base_path:
            return []
        base = Path(base_path)
        if base.exists():
            return [base]
        stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
        return sorted(base.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))

    def _decode_global_sample_pos_to_segment_local(
        self,
        sample_pos: int,
        capacities: List[int],
    ) -> Optional[Tuple[int, int]]:
        rem = int(sample_pos)
        for seg_idx, cap in enumerate(capacities):
            if rem < cap:
                return seg_idx, rem
            rem -= cap
        return None

    def _decode_read_scalar_from_sample_pos(
        self,
        sample_pos: int,
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap]],
        capacities: List[int],
        elem_size: int,
        unpack_fmt: str,
    ) -> Optional[Any]:
        mapped = self._decode_global_sample_pos_to_segment_local(sample_pos, capacities)
        if mapped is None:
            return None
        seg_idx, local = mapped
        if seg_idx not in seg_cache:
            return None
        _, mm = seg_cache[seg_idx]
        offset = self._DECODE_HDR_SIZE + local * elem_size
        if offset + elem_size > len(mm):
            return None
        return struct.unpack_from(unpack_fmt, mm, offset)[0]

    def _load_decode_signal_directory_entries(self) -> List[Tuple[int, int, int, int, int, int, int, int]]:
        entries: List[Tuple[int, int, int, int, int, int, int, int]] = []
        for seg_path in self.decode_signal_dir_segment_paths():
            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    entry_count, status = self._SIGNAL_DIR_HDR_STRUCT.unpack_from(mm, 0)
                    if status == 0 or entry_count == 0:
                        continue
                    for i in range(int(entry_count)):
                        off = self._DECODE_HDR_SIZE + i * self._SIGNAL_DIR_ENTRY_SIZE
                        if off + self._SIGNAL_DIR_ENTRY_SIZE > len(mm):
                            break
                        (
                            can_id,
                            signal_id,
                            _,
                            index_offset,
                            value_offset,
                            rawvalue_offset,
                            changed_index_offset,
                            sample_count,
                            changed_sample_count,
                            _,
                            _,
                        ) = self._SIGNAL_DIR_ENTRY_STRUCT.unpack_from(mm, off)
                        if sample_count <= 0:
                            continue
                        entries.append((
                            int(can_id),
                            int(signal_id),
                            int(index_offset),
                            int(value_offset),
                            int(rawvalue_offset),
                            int(changed_index_offset),
                            int(sample_count),
                            int(changed_sample_count),
                        ))
                finally:
                    mm.close()
        return entries

    def _open_decode_array_segments(self, paths: List[Path]) -> Tuple[Dict[int, Tuple[Any, _mmap.mmap]], List[int]]:
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap]] = {}
        capacities: List[int] = []
        for i, seg_path in enumerate(paths):
            f = open(seg_path, "rb")
            mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
            seg_cache[i] = (f, mm)
            _, capacity, status = self._DECODE_HDR_STRUCT.unpack_from(mm, 0)
            capacities.append(int(capacity) if status != 0 else 0)
        return seg_cache, capacities

    def _close_seg_cache(self, seg_cache: Dict[int, Tuple[Any, _mmap.mmap]]) -> None:
        for f, mm in seg_cache.values():
            mm.close()
            f.close()

    def _read_decode_segment_status(self, seg_path: Path) -> int:
        try:
            with open(seg_path, "rb") as f:
                f.seek(12)
                raw = f.read(4)
                if len(raw) < 4:
                    return 0
                return int(struct.unpack("<I", raw)[0])
        except Exception:
            return 0

    def _read_decode_segment_write_count(self, seg_path: Path) -> int:
        try:
            with open(seg_path, "rb") as f:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return 0
                return int(struct.unpack("<Q", hdr)[0])
        except Exception:
            return 0

    def refresh_decode_mmap_runtime(self):
        row_index_segs = self.decode_row_index_segment_paths()
        row_index_changed_segs = self.decode_row_index_changed_segment_paths()
        self.decode_mmap_file_count = len(row_index_segs)
        self.decode_verified_size = sum(self._read_decode_segment_write_count(seg) for seg in row_index_segs)

        signal_dir_ready = len(self.decode_signal_dir_segment_paths()) > 0
        row_index_changed_ready = len(row_index_changed_segs) > 0
        row_index_ready = len(row_index_segs) > 0
        value_ready = len(self.decode_value_segment_paths()) > 0
        rawvalue_ready = len(self.decode_rawvalue_segment_paths()) > 0

        # if not (signal_dir_ready and row_index_changed_ready and row_index_ready and value_ready and rawvalue_ready):
        #     self.decode_state = DecodeLogState.UNAVAILABLE
        #     return

        # all_done = all(self._read_decode_segment_status(seg) != 0 for seg in row_index_segs)
        # self.decode_state = DecodeLogState.AVAILABLE if all_done else DecodeLogState.UNAVAILABLE

