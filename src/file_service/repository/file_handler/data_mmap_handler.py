from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any, Tuple, Set
from collections import defaultdict
from pathlib import Path
import mmap as _mmap
import struct
import heapq
from lw.logger_setup import LOG
from file_service.parser.native.can_parser_api import MmapHeaderConstract, ParsedEntry, ParsedEntryLayout, IndexMmapLayout

# .data.mmap
# .index.mmap
@dataclass
class CANLogRawDiskFile: 
    mmap_dir: str
    mmap_name: str

    total_lines: int = field(default=0)
    verified_size: int = field(default=0)
    mmap_file_count: int = field(default=0)
    mmap_capacity: int = field(default=1_000_000)
    can_ids: List[int] = field(default_factory=list)
    channels: List[str] = field(default_factory=list)

    _ENTRY_SIZE: int = field(default=ParsedEntryLayout.ENTRY_SIZE, init=False, repr=False)
    _DATA_HEADER_SIZE: int = field(default=MmapHeaderConstract.SIZE, init=False, repr=False)
    _ENTRY_LINE_NUMBER_OFFSET: int = field(default=ParsedEntryLayout.LINE_NUMBER_OFFSET, init=False, repr=False)
    _ENTRY_TIMESTAMP_OFFSET: int = field(default=ParsedEntryLayout.TIMESTAMP_OFFSET, init=False, repr=False)
    _ENTRY_LAST_TIMESTAMP_OFFSET: int = field(default=ParsedEntryLayout.LAST_TIMESTAMP_OFFSET, init=False, repr=False)
    _ENTRY_CAN_ID_OFFSET: int = field(default=ParsedEntryLayout.CAN_ID_OFFSET, init=False, repr=False)
    _ENTRY_DIRECTION_OFFSET: int = field(default=ParsedEntryLayout.DIRECTION_OFFSET, init=False, repr=False)
    _ENTRY_DATA_LEN_OFFSET: int = field(default=ParsedEntryLayout.DATA_LEN_OFFSET, init=False, repr=False)
    _ENTRY_CHANGED_OFFSET: int = field(default=ParsedEntryLayout.CHANGED_OFFSET, init=False, repr=False)
    _ENTRY_DATA_OFFSET: int = field(default=ParsedEntryLayout.DATA_OFFSET, init=False, repr=False)
    _ENTRY_DATA_CAPACITY: int = field(default=ParsedEntryLayout.DATA_CAPACITY, init=False, repr=False)
    _ENTRY_CHANNEL_OFFSET: int = field(default=ParsedEntryLayout.CHANNEL_OFFSET, init=False, repr=False)
    _ENTRY_CHANNEL_CAPACITY: int = field(default=ParsedEntryLayout.CHANNEL_CAPACITY, init=False, repr=False)
    _INDEX_HEADER_SIZE: int = field(default=IndexMmapLayout.INDEX_HEADER_SIZE, init=False, repr=False)
    _INDEX_CAN_ID_COUNT_OFFSET: int = field(default=IndexMmapLayout.INDEX_HEADER_CAN_ID_COUNT_OFFSET, init=False, repr=False)
    _INDEX_MAX_CAN_IDS_OFFSET: int = field(default=IndexMmapLayout.INDEX_HEADER_MAX_CAN_IDS_OFFSET, init=False, repr=False)
    _INDEX_MAX_ROW_POOL_SIZE_OFFSET: int = field(default=IndexMmapLayout.INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET, init=False, repr=False)
    _INDEX_MAX_CHANGED_ROW_POOL_SIZE_OFFSET: int = field(default=IndexMmapLayout.INDEX_HEADER_MAX_CHANGED_ROW_POOL_SIZE_OFFSET, init=False, repr=False)
    _INDEX_MAX_TS_POOL_SIZE_OFFSET: int = field(default=IndexMmapLayout.INDEX_HEADER_MAX_TS_POOL_SIZE_OFFSET, init=False, repr=False)

    _INDEX_FILTER_SIZE: int = field(default=IndexMmapLayout.CAN_ID_FILTER_SIZE, init=False, repr=False)
    _INDEX_FILTER_CAN_ID_OFFSET: int = field(default=IndexMmapLayout.CAN_ID_FILTER_CAN_ID_OFFSET, init=False, repr=False)
    _INDEX_FILTER_ROW_OFFSET_OFFSET: int = field(default=IndexMmapLayout.CAN_ID_FILTER_ROW_OFFSET_OFFSET, init=False, repr=False)
    _INDEX_FILTER_CHANGED_ROW_OFFSET_OFFSET: int = field(default=IndexMmapLayout.CAN_ID_FILTER_CHANGED_ROW_OFFSET_OFFSET, init=False, repr=False)
    _INDEX_FILTER_TS_OFFSET_OFFSET: int = field(default=IndexMmapLayout.CAN_ID_FILTER_TS_OFFSET_OFFSET, init=False, repr=False)
    _INDEX_FILTER_COUNT_OFFSET: int = field(default=IndexMmapLayout.CAN_ID_FILTER_COUNT_OFFSET, init=False, repr=False)
    _INDEX_FILTER_CHANGED_COUNT_OFFSET: int = field(default=IndexMmapLayout.CAN_ID_FILTER_CHANGED_COUNT_OFFSET, init=False, repr=False)

    _CHANNEL_INDEX_HEADER_SIZE: int = field(default=IndexMmapLayout.CHANNEL_INDEX_HEADER_SIZE, init=False, repr=False)
    _CHANNEL_INDEX_CHANNEL_COUNT_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_INDEX_HEADER_CHANNEL_COUNT_OFFSET, init=False, repr=False)
    _CHANNEL_INDEX_MAX_CHANNELS_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_INDEX_HEADER_MAX_CHANNELS_OFFSET, init=False, repr=False)
    _CHANNEL_INDEX_MAX_ROW_POOL_SIZE_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET, init=False, repr=False)

    _CHANNEL_FILTER_SIZE: int = field(default=IndexMmapLayout.CHANNEL_FILTER_SIZE, init=False, repr=False)
    _CHANNEL_FILTER_CHANNEL_INDEX_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_FILTER_CHANNEL_INDEX_OFFSET, init=False, repr=False)
    _CHANNEL_FILTER_CHANNEL_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_FILTER_CHANNEL_OFFSET, init=False, repr=False)
    _CHANNEL_FILTER_CHANNEL_CAPACITY: int = field(default=IndexMmapLayout.CHANNEL_FILTER_CHANNEL_CAPACITY, init=False, repr=False)
    _CHANNEL_FILTER_ROW_OFFSET_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_FILTER_ROW_OFFSET_OFFSET, init=False, repr=False)
    _CHANNEL_FILTER_COUNT_OFFSET: int = field(default=IndexMmapLayout.CHANNEL_FILTER_COUNT_OFFSET, init=False, repr=False)

    _DIRECTION_INDEX_HEADER_SIZE: int = field(default=IndexMmapLayout.DIRECTION_INDEX_HEADER_SIZE, init=False, repr=False)
    _DIRECTION_INDEX_DIRECTION_COUNT_OFFSET: int = field(default=IndexMmapLayout.DIRECTION_INDEX_HEADER_DIRECTION_COUNT_OFFSET, init=False, repr=False)
    _DIRECTION_INDEX_MAX_DIRECTIONS_OFFSET: int = field(default=IndexMmapLayout.DIRECTION_INDEX_HEADER_MAX_DIRECTIONS_OFFSET, init=False, repr=False)
    _DIRECTION_INDEX_MAX_ROW_POOL_SIZE_OFFSET: int = field(default=IndexMmapLayout.DIRECTION_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET, init=False, repr=False)

    _DIRECTION_FILTER_SIZE: int = field(default=IndexMmapLayout.DIRECTION_FILTER_SIZE, init=False, repr=False)
    _DIRECTION_FILTER_DIRECTION_OFFSET: int = field(default=IndexMmapLayout.DIRECTION_FILTER_DIRECTION_OFFSET, init=False, repr=False)
    _DIRECTION_FILTER_ROW_OFFSET_OFFSET: int = field(default=IndexMmapLayout.DIRECTION_FILTER_ROW_OFFSET_OFFSET, init=False, repr=False)
    _DIRECTION_FILTER_COUNT_OFFSET: int = field(default=IndexMmapLayout.DIRECTION_FILTER_COUNT_OFFSET, init=False, repr=False)
    _multi_can_merge_state: Dict[Tuple[bool, Tuple[int, ...]], Dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    # Lightweight catalog: can_id → list of per-segment descriptors.
    # Each descriptor = (seg_path, row_pool_base, row_pool_off, count,
    #                     changed_pool_base, changed_pool_off, changed_count)
    # Only filter metadata is read — NO row data loaded into RAM.
    _can_id_catalog: Dict[int, List[tuple]] = field(default_factory=dict, init=False, repr=False)
    _can_id_timestamp_bounds: Dict[int, Tuple[float, float]] = field(default_factory=dict, init=False, repr=False)
    _global_timestamp_bounds: Optional[Tuple[float, float]] = field(default=None, init=False, repr=False)
    _channel_catalog: Dict[str, List[tuple]] = field(default_factory=dict, init=False, repr=False)
    _direction_catalog: Dict[str, List[tuple]] = field(default_factory=dict, init=False, repr=False)
    _multi_channel_merge_state: Dict[Tuple[str, ...], Dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _multi_direction_merge_state: Dict[Tuple[str, ...], Dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    # @property
    # def file_name(self) -> str:
    #     if not self.file_path:
    #         return ""
    #     return Path(self.file_path).name

    def __post_init__(self) -> None:
        MmapHeaderConstract.load_from_native_binding()
        ParsedEntryLayout.load_from_native_binding()
        IndexMmapLayout.load_from_native_binding()

        self._DATA_HEADER_SIZE = int(ParsedEntryLayout.DATA_HEADER_SIZE)
        self._ENTRY_SIZE = int(ParsedEntryLayout.ENTRY_SIZE)
        self._ENTRY_LINE_NUMBER_OFFSET = int(ParsedEntryLayout.LINE_NUMBER_OFFSET)
        self._ENTRY_TIMESTAMP_OFFSET = int(ParsedEntryLayout.TIMESTAMP_OFFSET)
        self._ENTRY_LAST_TIMESTAMP_OFFSET = int(ParsedEntryLayout.LAST_TIMESTAMP_OFFSET)
        self._ENTRY_CAN_ID_OFFSET = int(ParsedEntryLayout.CAN_ID_OFFSET)
        self._ENTRY_DIRECTION_OFFSET = int(ParsedEntryLayout.DIRECTION_OFFSET)
        self._ENTRY_DATA_LEN_OFFSET = int(ParsedEntryLayout.DATA_LEN_OFFSET)
        self._ENTRY_CHANGED_OFFSET = int(ParsedEntryLayout.CHANGED_OFFSET)
        self._ENTRY_DATA_OFFSET = int(ParsedEntryLayout.DATA_OFFSET)
        self._ENTRY_DATA_CAPACITY = int(ParsedEntryLayout.DATA_CAPACITY)
        self._ENTRY_CHANNEL_OFFSET = int(ParsedEntryLayout.CHANNEL_OFFSET)
        self._ENTRY_CHANNEL_CAPACITY = int(ParsedEntryLayout.CHANNEL_CAPACITY)

        self._INDEX_HEADER_SIZE = int(IndexMmapLayout.INDEX_HEADER_SIZE)
        self._INDEX_CAN_ID_COUNT_OFFSET = int(IndexMmapLayout.INDEX_HEADER_CAN_ID_COUNT_OFFSET)
        self._INDEX_MAX_CAN_IDS_OFFSET = int(IndexMmapLayout.INDEX_HEADER_MAX_CAN_IDS_OFFSET)
        self._INDEX_MAX_ROW_POOL_SIZE_OFFSET = int(IndexMmapLayout.INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET)
        self._INDEX_MAX_CHANGED_ROW_POOL_SIZE_OFFSET = int(IndexMmapLayout.INDEX_HEADER_MAX_CHANGED_ROW_POOL_SIZE_OFFSET)
        self._INDEX_MAX_TS_POOL_SIZE_OFFSET = int(IndexMmapLayout.INDEX_HEADER_MAX_TS_POOL_SIZE_OFFSET)

        self._INDEX_FILTER_SIZE = int(IndexMmapLayout.CAN_ID_FILTER_SIZE)
        self._INDEX_FILTER_CAN_ID_OFFSET = int(IndexMmapLayout.CAN_ID_FILTER_CAN_ID_OFFSET)
        self._INDEX_FILTER_ROW_OFFSET_OFFSET = int(IndexMmapLayout.CAN_ID_FILTER_ROW_OFFSET_OFFSET)
        self._INDEX_FILTER_CHANGED_ROW_OFFSET_OFFSET = int(IndexMmapLayout.CAN_ID_FILTER_CHANGED_ROW_OFFSET_OFFSET)
        self._INDEX_FILTER_TS_OFFSET_OFFSET = int(IndexMmapLayout.CAN_ID_FILTER_TS_OFFSET_OFFSET)
        self._INDEX_FILTER_COUNT_OFFSET = int(IndexMmapLayout.CAN_ID_FILTER_COUNT_OFFSET)
        self._INDEX_FILTER_CHANGED_COUNT_OFFSET = int(IndexMmapLayout.CAN_ID_FILTER_CHANGED_COUNT_OFFSET)

        self._CHANNEL_INDEX_HEADER_SIZE = int(IndexMmapLayout.CHANNEL_INDEX_HEADER_SIZE)
        self._CHANNEL_INDEX_CHANNEL_COUNT_OFFSET = int(IndexMmapLayout.CHANNEL_INDEX_HEADER_CHANNEL_COUNT_OFFSET)
        self._CHANNEL_INDEX_MAX_CHANNELS_OFFSET = int(IndexMmapLayout.CHANNEL_INDEX_HEADER_MAX_CHANNELS_OFFSET)
        self._CHANNEL_INDEX_MAX_ROW_POOL_SIZE_OFFSET = int(IndexMmapLayout.CHANNEL_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET)

        self._CHANNEL_FILTER_SIZE = int(IndexMmapLayout.CHANNEL_FILTER_SIZE)
        self._CHANNEL_FILTER_CHANNEL_INDEX_OFFSET = int(IndexMmapLayout.CHANNEL_FILTER_CHANNEL_INDEX_OFFSET)
        self._CHANNEL_FILTER_CHANNEL_OFFSET = int(IndexMmapLayout.CHANNEL_FILTER_CHANNEL_OFFSET)
        self._CHANNEL_FILTER_CHANNEL_CAPACITY = int(IndexMmapLayout.CHANNEL_FILTER_CHANNEL_CAPACITY)
        self._CHANNEL_FILTER_ROW_OFFSET_OFFSET = int(IndexMmapLayout.CHANNEL_FILTER_ROW_OFFSET_OFFSET)
        self._CHANNEL_FILTER_COUNT_OFFSET = int(IndexMmapLayout.CHANNEL_FILTER_COUNT_OFFSET)

        self._DIRECTION_INDEX_HEADER_SIZE = int(IndexMmapLayout.DIRECTION_INDEX_HEADER_SIZE)
        self._DIRECTION_INDEX_DIRECTION_COUNT_OFFSET = int(IndexMmapLayout.DIRECTION_INDEX_HEADER_DIRECTION_COUNT_OFFSET)
        self._DIRECTION_INDEX_MAX_DIRECTIONS_OFFSET = int(IndexMmapLayout.DIRECTION_INDEX_HEADER_MAX_DIRECTIONS_OFFSET)
        self._DIRECTION_INDEX_MAX_ROW_POOL_SIZE_OFFSET = int(IndexMmapLayout.DIRECTION_INDEX_HEADER_MAX_ROW_POOL_SIZE_OFFSET)

        self._DIRECTION_FILTER_SIZE = int(IndexMmapLayout.DIRECTION_FILTER_SIZE)
        self._DIRECTION_FILTER_DIRECTION_OFFSET = int(IndexMmapLayout.DIRECTION_FILTER_DIRECTION_OFFSET)
        self._DIRECTION_FILTER_ROW_OFFSET_OFFSET = int(IndexMmapLayout.DIRECTION_FILTER_ROW_OFFSET_OFFSET)
        self._DIRECTION_FILTER_COUNT_OFFSET = int(IndexMmapLayout.DIRECTION_FILTER_COUNT_OFFSET)

        self.mmap_dir = str(self.mmap_dir)
        self.mmap_name = str(self.mmap_name)
        if not self.mmap_dir:
            raise ValueError("mmap_dir is required")
        if not self.mmap_name:
            raise ValueError("mmap_name is required")

    @property
    def mmap_base_path(self) -> Path:
        return Path(self.mmap_dir) / self.mmap_name

    @staticmethod
    def _strip_numeric_segment(stem: str) -> str:
        stem_parts = stem.rsplit(".", 1)
        if len(stem_parts) == 2 and stem_parts[1].isdigit() and len(stem_parts[1]) == 3:
            return stem_parts[0]
        return stem

    @classmethod
    def _derive_mmap_name(cls, mmap_path: str) -> str:
        if not mmap_path:
            raise ValueError("mmap path is required")

        path = Path(mmap_path)
        stem = path.name[:-5] if path.name.endswith(".mmap") else path.name
        stem = cls._strip_numeric_segment(stem)

        for suffix in (".index.channel", ".index.direction", ".data", ".index"):
            if stem.endswith(suffix):
                return stem[: -len(suffix)]
        return stem

    def _update_base_from_path(self, mmap_path: str) -> None:
        if not mmap_path:
            raise ValueError("mmap path is required")
        path = Path(mmap_path)
        self.mmap_dir = str(path.parent)
        self.mmap_name = self._derive_mmap_name(mmap_path)

    @property
    def data_mmap_path(self) -> str:
        return str(self.mmap_base_path.with_name(f"{self.mmap_name}.data.mmap"))

    @data_mmap_path.setter
    def data_mmap_path(self, value: str) -> None:
        self._update_base_from_path(value)

    @property
    def index_mmap_path(self) -> str:
        return str(self.mmap_base_path.with_name(f"{self.mmap_name}.index.mmap"))

    @index_mmap_path.setter
    def index_mmap_path(self, value: str) -> None:
        self._update_base_from_path(value)

    @property
    def channel_index_mmap_path(self) -> str:
        return str(self.mmap_base_path.with_name(f"{self.mmap_name}.index.channel.mmap"))

    @channel_index_mmap_path.setter
    def channel_index_mmap_path(self, value: str) -> None:
        self._update_base_from_path(value)

    @property
    def direction_index_mmap_path(self) -> str:
        return str(self.mmap_base_path.with_name(f"{self.mmap_name}.index.direction.mmap"))

    @direction_index_mmap_path.setter
    def direction_index_mmap_path(self, value: str) -> None:
        self._update_base_from_path(value)

    # ────────────────────────────────────────────────────────────────────
    #  Mmap path management
    # ────────────────────────────────────────────────────────────────────
    def data_segment_paths(self) -> List[Path]:
        return self._segment_paths(self.data_mmap_path, "data")

    def index_segment_paths(self) -> List[Path]:
        return self._segment_paths(self.index_mmap_path, "index")

    def channel_index_segment_paths(self) -> List[Path]:
        base_path = self.channel_index_mmap_path
        if not base_path and self.index_mmap_path:
            stem = self.index_mmap_path[:-5] if self.index_mmap_path.endswith(".mmap") else self.index_mmap_path
            base_path = stem + ".channel.mmap"
        return self._segment_paths(base_path, "channel-index") if base_path else []

    def direction_index_segment_paths(self) -> List[Path]:
        base_path = self.direction_index_mmap_path
        if not base_path and self.index_mmap_path:
            stem = self.index_mmap_path[:-5] if self.index_mmap_path.endswith(".mmap") else self.index_mmap_path
            base_path = stem + ".direction.mmap"
        return self._segment_paths(base_path, "direction-index") if base_path else []

    def _segment_paths(self, base_path: str, kind: str) -> List[Path]:
        base = Path(base_path)
        folder = base.parent
        stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
        # Accept both base paths (e.g. *.data.mmap) and explicit numbered
        # segment paths (e.g. *.data.000.mmap) by stripping the numeric suffix.
        stem_parts = stem.rsplit(".", 1)
        if len(stem_parts) == 2 and stem_parts[1].isdigit() and len(stem_parts[1]) == 3:
            stem = stem_parts[0]
        segments = sorted(folder.glob(f"{stem}.[0-9][0-9][0-9].mmap"))
        if base.exists():
            segments.insert(0, base)

        dedup: list[Path] = []
        seen: set[Path] = set()
        for path in segments:
            if path in seen:
                continue
            seen.add(path)
            dedup.append(path)
        return dedup
    
    # ────────────────────────────────────────────────────────────────────
    #  API for filter rows
    # ────────────────────────────────────────────────────────────────────
    def get_page_from_row_indices(self, first_line: int, page_size: int) -> List[ParsedEntry]:
        start = max(0, int(first_line))
        end = start + max(0, int(page_size))
        return self.get_messages_by_row_indices(range(start, end))

    def get_page_from_can_id_row_indices(self, can_id: int, first_line: int, page_size: int) -> List[ParsedEntry]:
        page_rows = self._read_row_page_from_mmap(can_id, first_line, page_size)
        return self.get_messages_by_row_indices(page_rows)

    def get_page_from_can_ids_row_indices(self, can_ids: List[int], first_line: int, page_size: int) -> List[ParsedEntry]:
        merged = self._merge_can_ids_page_from_mmap(can_ids, first_line, page_size, changed=False)
        return self.get_messages_by_row_indices(merged)

    def get_page_from_can_id_changed_row_indices(self, can_id: int, first_line: int, page_size: int) -> List[ParsedEntry]:
        page_rows = self._read_changed_row_page_from_mmap(can_id, first_line, page_size)
        return self.get_messages_by_row_indices(page_rows)

    def get_page_from_can_ids_changed_row_indices(self, can_ids: List[int], first_line: int, page_size: int) -> List[ParsedEntry]:
        merged = self._merge_can_ids_page_from_mmap(can_ids, first_line, page_size, changed=True)
        return self.get_messages_by_row_indices(merged)

    def get_page_from_channel_row_indices(self, channel: str, first_line: int, page_size: int) -> List[ParsedEntry]:
        page_rows = self._read_channel_row_page_from_mmap(channel, first_line, page_size)
        return self.get_messages_by_row_indices(page_rows)

    def get_page_from_channels_row_indices(self, channels: List[str], first_line: int, page_size: int) -> List[ParsedEntry]:
        merged = self._merge_channels_page_from_mmap(channels, first_line, page_size)
        return self.get_messages_by_row_indices(merged)

    def get_page_from_direction_row_indices(self, direction: str, first_line: int, page_size: int) -> List[ParsedEntry]:
        page_rows = self._read_direction_row_page_from_mmap(direction, first_line, page_size)
        return self.get_messages_by_row_indices(page_rows)

    def get_page_from_directions_row_indices(self, directions: List[str], first_line: int, page_size: int) -> List[ParsedEntry]:
        merged = self._merge_directions_page_from_mmap(directions, first_line, page_size)
        return self.get_messages_by_row_indices(merged)

    def get_page_from_timestamp_range(self,from_t: float,to_t: float,first_line: int,page_size: int,) -> List[ParsedEntry]:
        lo_t = float(from_t)
        hi_t = float(to_t)
        if lo_t > hi_t:
            lo_t, hi_t = hi_t, lo_t

        start_row = self.get_start_row_by_timestamp(lo_t)
        end_row = self.get_end_row_by_timestamp(hi_t)
        if end_row <= start_row:
            return []

        offset = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        window_total = end_row - start_row
        if offset >= window_total:
            return []

        global_first = start_row + offset
        take = min(size, window_total - offset)
        return self.get_page_from_row_indices(global_first, take)
    
    # ────────────────────────────────────────────────────────────────────
    #  API for row
    # ────────────────────────────────────────────────────────────────────
    def get_start_row_by_timestamp(self, timestamp: float) -> int:
        if self.total_lines <= 0:
            self.refresh_mmap_runtime()
        total = int(self.total_lines)
        if total <= 0:
            return 0

        segs = self.data_segment_paths()
        if not segs:
            return 0

        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}
        try:
            return self._timestamp_lower_bound_global(segs, total, float(timestamp), seg_cache)
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()

    def get_start_row_by_can_id_timestamp(self, can_id: int, timestamp: float) -> int:
        total = self.get_total_count_by_can_id(int(can_id))
        if total <= 0:
            return 0
        return self._timestamp_lower_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_row_index_at_pos_can_id(int(can_id), pos),
            target_ts=float(timestamp),
        )

    def get_start_row_by_channel_timestamp(self, channel: str, timestamp: float) -> int:
        total = self.get_total_count_by_channel(channel)
        if total <= 0:
            return 0
        return self._timestamp_lower_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_row_index_at_pos_channel(channel, pos),
            target_ts=float(timestamp),
        )

    def get_start_row_by_direction_timestamp(self, direction: str, timestamp: float) -> int:
        total = self.get_total_count_by_direction(direction)
        if total <= 0:
            return 0
        return self._timestamp_lower_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_row_index_at_pos_direction(direction, pos),
            target_ts=float(timestamp),
        )

    def get_end_row_by_timestamp(self, timestamp: float) -> int:
        """Upper-bound end row (first row with timestamp > target) in global space."""
        if self.total_lines <= 0:
            self.refresh_mmap_runtime()
        total = int(self.total_lines)
        if total <= 0:
            return 0

        segs = self.data_segment_paths()
        if not segs:
            return 0

        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}
        try:
            return self._timestamp_upper_bound_global(segs, total, float(timestamp), seg_cache)
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()

    def get_end_row_by_can_id_timestamp(self, can_id: int, timestamp: float) -> int:
        """Upper-bound end row in CAN-ID filtered space."""
        total = self.get_total_count_by_can_id(int(can_id))
        if total <= 0:
            return 0
        return self._timestamp_upper_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_row_index_at_pos_can_id(int(can_id), pos),
            target_ts=float(timestamp),
        )

    def get_end_row_by_channel_timestamp(self, channel: str, timestamp: float) -> int:
        """Upper-bound end row in channel filtered space."""
        total = self.get_total_count_by_channel(channel)
        if total <= 0:
            return 0
        return self._timestamp_upper_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_row_index_at_pos_channel(channel, pos),
            target_ts=float(timestamp),
        )

    def get_end_row_by_direction_timestamp(self, direction: str, timestamp: float) -> int:
        """Upper-bound end row in direction filtered space."""
        total = self.get_total_count_by_direction(direction)
        if total <= 0:
            return 0
        return self._timestamp_upper_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_row_index_at_pos_direction(direction, pos),
            target_ts=float(timestamp),
        )

    def get_start_row_by_can_id_changed_timestamp(self, can_id: int, timestamp: float) -> int:
        """Lower-bound start row in CAN-ID changed-only filtered space."""
        total = self.get_changed_count_by_can_id(int(can_id))
        if total <= 0:
            return 0
        return self._timestamp_lower_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_changed_row_index_at_pos_can_id(int(can_id), pos),
            target_ts=float(timestamp),
        )

    def get_end_row_by_can_id_changed_timestamp(self, can_id: int, timestamp: float) -> int:
        """Upper-bound end row in CAN-ID changed-only filtered space."""
        total = self.get_changed_count_by_can_id(int(can_id))
        if total <= 0:
            return 0
        return self._timestamp_upper_bound_indexed(
            total_count=total,
            read_row_index_at_pos=lambda pos: self._read_changed_row_index_at_pos_can_id(int(can_id), pos),
            target_ts=float(timestamp),
        )
    
    # ────────────────────────────────────────────────────────────────────
    #  API for size
    # ────────────────────────────────────────────────────────────────────
    def get_total_count_by_can_id(self, can_id: int) -> int:
        """Total row count for one CAN-ID (all + unchanged + changed)."""
        self._ensure_can_id_catalog()
        segs = self._can_id_catalog.get(int(can_id), [])
        return sum(c for _, _, _, c, _, _, _ in segs)

    def get_changed_count_by_can_id(self, can_id: int) -> int:
        """Changed-row count for one CAN-ID."""
        self._ensure_can_id_catalog()
        segs = self._can_id_catalog.get(int(can_id), [])
        return sum(cc for _, _, _, _, _, _, cc in segs)

    def get_total_count_by_can_ids(self, can_ids: List[int]) -> int:
        """Total row count across multiple CAN-IDs (sum, not merged)."""
        self._ensure_can_id_catalog()
        total = 0
        seen: Set[int] = set()
        for cid_raw in can_ids:
            cid = int(cid_raw)
            if cid in seen:
                continue
            seen.add(cid)
            segs = self._can_id_catalog.get(cid, [])
            total += sum(c for _, _, _, c, _, _, _ in segs)
        return total

    def get_changed_count_by_can_ids(self, can_ids: List[int]) -> int:
        """Changed-row count across multiple CAN-IDs."""
        self._ensure_can_id_catalog()
        total = 0
        seen: Set[int] = set()
        for cid_raw in can_ids:
            cid = int(cid_raw)
            if cid in seen:
                continue
            seen.add(cid)
            segs = self._can_id_catalog.get(cid, [])
            total += sum(cc for _, _, _, _, _, _, cc in segs)
        return total

    def get_total_count_by_channel(self, channel: str) -> int:
        self._ensure_channel_catalog()
        segs = self._channel_catalog.get(str(channel).lower(), [])
        return sum(c for _, _, _, c, _ in segs)

    def get_total_count_by_channels(self, channels: List[str]) -> int:
        self._ensure_channel_catalog()
        total = 0
        seen: Set[str] = set()
        for channel in channels:
            key = str(channel).lower()
            if key in seen:
                continue
            seen.add(key)
            segs = self._channel_catalog.get(key, [])
            total += sum(c for _, _, _, c, _ in segs)
        return total

    def get_total_count_by_direction(self, direction: str) -> int:
        self._ensure_direction_catalog()
        segs = self._direction_catalog.get(self._normalize_direction_key(direction), [])
        return sum(c for _, _, _, c, _ in segs)

    def get_total_count_by_directions(self, directions: List[str]) -> int:
        self._ensure_direction_catalog()
        total = 0
        seen: Set[str] = set()
        for direction in directions:
            key = self._normalize_direction_key(direction)
            if key in seen:
                continue
            seen.add(key)
            segs = self._direction_catalog.get(key, [])
            total += sum(c for _, _, _, c, _ in segs)
        return total

    # ────────────────────────────────────────────────────────────────────
    #  API for timestamp
    # ────────────────────────────────────────────────────────────────────

    def get_first_last_timestamp(self) -> Tuple[Optional[float], Optional[float]]:
        if self._global_timestamp_bounds is not None:
            return self._global_timestamp_bounds

        if self.total_lines <= 0:
            self.refresh_mmap_runtime()
        if self.total_lines <= 0:
            return None, None

        segs = self.data_segment_paths()
        if not segs:
            return None, None

        first_entry = self._read_entry_by_global_row(segs, 0)
        last_entry = self._read_entry_by_global_row(segs, int(self.total_lines) - 1)
        if first_entry is None or last_entry is None:
            return None, None

        self._global_timestamp_bounds = (float(first_entry.timestamp), float(last_entry.timestamp))
        return self._global_timestamp_bounds

    def get_first_last_timestamp_by_can_id(self, can_id: int) -> Tuple[Optional[float], Optional[float]]:
        self._ensure_can_id_catalog()
        bounds = self._can_id_timestamp_bounds.get(int(can_id))
        if bounds is None:
            return None, None
        return float(bounds[0]), float(bounds[1])

    def get_first_last_timestamp_by_can_ids(self, can_ids: List[int]) -> Tuple[Optional[float], Optional[float]]:
        self._ensure_can_id_catalog()

        seen: Set[int] = set()
        first_ts: Optional[float] = None
        last_ts: Optional[float] = None
        for can_id in can_ids:
            cid = int(can_id)
            if cid in seen:
                continue
            seen.add(cid)
            bounds = self._can_id_timestamp_bounds.get(cid)
            if bounds is None:
                continue
            f, l = float(bounds[0]), float(bounds[1])
            first_ts = f if first_ts is None else min(first_ts, f)
            last_ts = l if last_ts is None else max(last_ts, l)

        return first_ts, last_ts

    def get_timestamps_by_can_id(self, can_id: int) -> List[float]:
        rows = self.get_row_indices_by_list_id([int(can_id)])
        if not rows:
            return []

        segs = self.data_segment_paths()
        if not segs:
            return []

        timestamps: List[float] = []
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}
        try:
            for row in rows:
                ts = self._read_timestamp_by_global_row_cached(segs, int(row), seg_cache)
                if ts is not None:
                    timestamps.append(float(ts))
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()

        return timestamps

    def get_timestamp_by_row(self, row_index: int) -> Optional[float]:
        """Timestamp for a global row index (0-based)."""
        if self.total_lines <= 0:
            self.refresh_mmap_runtime()
        if self.total_lines <= 0:
            return None

        row = int(row_index)
        if row < 0 or row >= int(self.total_lines):
            return None

        segs = self.data_segment_paths()
        if not segs:
            return None

        entry = self._read_entry_by_global_row(segs, row)
        if entry is None:
            return None
        return float(entry.timestamp)

    def get_timestamp_by_can_id_row(
        self,
        can_id: int,
        row_index: int,
        changed: bool = False,
    ) -> Optional[float]:
        """Timestamp for a row index within one CAN-ID filtered space (0-based)."""
        row = int(row_index)
        if row < 0:
            return None

        if changed:
            rows = self._read_changed_row_page_from_mmap(int(can_id), row, 1)
        else:
            rows = self._read_row_page_from_mmap(int(can_id), row, 1)
        if not rows:
            return None

        return self.get_timestamp_by_row(int(rows[0]))

    def get_timestamp_by_can_ids_row(
        self,
        can_ids: List[int],
        row_index: int,
        changed: bool = False,
    ) -> Optional[float]:
        """Timestamp for a row index within merged CAN-IDs filtered space (0-based)."""
        row = int(row_index)
        if row < 0:
            return None

        rows = self._merge_can_ids_page_from_mmap(
            can_ids=can_ids,
            first_line=row,
            page_size=1,
            changed=changed,
        )
        if not rows:
            return None

        return self.get_timestamp_by_row(int(rows[0]))

    def get_timestamp_by_channel_row(self, channel: str, row_index: int) -> Optional[float]:
        row = int(row_index)
        if row < 0:
            return None

        rows = self._read_channel_row_page_from_mmap(channel, row, 1)
        if not rows:
            return None
        return self.get_timestamp_by_row(int(rows[0]))

    ############################# Internal #################################    
    def _read_timestamp_by_global_row_cached(
        self,
        segs: List[Path],
        global_row: int,
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]],
    ) -> Optional[float]:
        row = int(global_row)
        if row < 0:
            return None
        seg_idx = row // self.mmap_capacity
        local_idx = row % self.mmap_capacity
        if seg_idx < 0 or seg_idx >= len(segs):
            return None

        if seg_idx not in seg_cache:
            f = open(segs[seg_idx], "rb")
            mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
            entry_size, _ = self._get_data_entry_layout(segs[seg_idx])
            seg_cache[seg_idx] = (f, mm, entry_size, None)

        _, mm, entry_size, _ = seg_cache[seg_idx]
        offset = self._DATA_HEADER_SIZE + local_idx * entry_size
        if offset + entry_size > len(mm):
            return None

        timestamp = struct.unpack_from("<d", mm, offset + self._ENTRY_TIMESTAMP_OFFSET)[0]
        return float(timestamp)

    def _timestamp_lower_bound_global(
        self,
        segs: List[Path],
        total: int,
        target_ts: float,
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]],
    ) -> int:
        lo, hi = 0, int(total)
        while lo < hi:
            mid = (lo + hi) // 2
            ts = self._read_timestamp_by_global_row_cached(segs, mid, seg_cache)
            if ts is None or ts < target_ts:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _u32_at(mm: _mmap.mmap, offset: int) -> int:
        return int(struct.unpack_from("<I", mm, int(offset))[0])

    @staticmethod
    def _u64_at(mm: _mmap.mmap, offset: int) -> int:
        return int(struct.unpack_from("<Q", mm, int(offset))[0])

    def _timestamp_upper_bound_global(
        self,
        segs: List[Path],
        total: int,
        target_ts: float,
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]],
    ) -> int:
        lo, hi = 0, int(total)
        while lo < hi:
            mid = (lo + hi) // 2
            ts = self._read_timestamp_by_global_row_cached(segs, mid, seg_cache)
            if ts is None or ts <= target_ts:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _read_row_index_at_pos_can_id(self, can_id: int, pos: int) -> Optional[int]:
        rows = self._read_row_page_from_mmap(int(can_id), int(pos), 1)
        return int(rows[0]) if rows else None

    def _read_row_index_at_pos_channel(self, channel: str, pos: int) -> Optional[int]:
        rows = self._read_channel_row_page_from_mmap(channel, int(pos), 1)
        return int(rows[0]) if rows else None

    def _read_row_index_at_pos_direction(self, direction: str, pos: int) -> Optional[int]:
        rows = self._read_direction_row_page_from_mmap(direction, int(pos), 1)
        return int(rows[0]) if rows else None

    def _read_changed_row_index_at_pos_can_id(self, can_id: int, pos: int) -> Optional[int]:
        rows = self._read_changed_row_page_from_mmap(int(can_id), int(pos), 1)
        return int(rows[0]) if rows else None

    def _timestamp_lower_bound_indexed(
        self,
        total_count: int,
        read_row_index_at_pos: Callable[[int], Optional[int]],
        target_ts: float,
    ) -> int:
        total = int(total_count)
        if total <= 0:
            return 0

        segs = self.data_segment_paths()
        if not segs:
            return 0

        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}

        def ts_by_pos(pos: int) -> float:
            row = read_row_index_at_pos(pos)
            if row is None:
                return float("inf")
            ts = self._read_timestamp_by_global_row_cached(segs, row, seg_cache)
            return float("inf") if ts is None else float(ts)

        try:
            lo, hi = 0, total
            while lo < hi:
                mid = (lo + hi) // 2
                if ts_by_pos(mid) < float(target_ts):
                    lo = mid + 1
                else:
                    hi = mid
            return lo
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()

    def _timestamp_upper_bound_indexed(
        self,
        total_count: int,
        read_row_index_at_pos: Callable[[int], Optional[int]],
        target_ts: float,
    ) -> int:
        total = int(total_count)
        if total <= 0:
            return 0

        segs = self.data_segment_paths()
        if not segs:
            return 0

        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}

        def ts_by_pos(pos: int) -> float:
            row = read_row_index_at_pos(pos)
            if row is None:
                return float("inf")
            ts = self._read_timestamp_by_global_row_cached(segs, row, seg_cache)
            return float("inf") if ts is None else float(ts)

        try:
            lo, hi = 0, total
            while lo < hi:
                mid = (lo + hi) // 2
                if ts_by_pos(mid) <= float(target_ts):
                    lo = mid + 1
                else:
                    hi = mid
            return lo
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()

    # ────────────────────────────────────────────────────────────────────
    #  Lightweight catalog — reads only the small filter entries (36 bytes
    #  per CAN-ID per segment). NO row data is loaded into RAM.
    # ────────────────────────────────────────────────────────────────────
    def _ensure_can_id_catalog(self):
        """Populate *_can_id_catalog* from the index segment headers.

        Each entry is a list of per-segment descriptors:
            (seg_path, row_pool_base, row_pool_off, count,
             changed_pool_base, changed_pool_off, changed_count)
        """
        if self._can_id_catalog:
            return

        catalog: Dict[int, List[tuple]] = defaultdict(list)
        bounds: Dict[int, Tuple[float, float]] = {}
        for seg_path in self.index_segment_paths():
            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    if len(mm) < self._INDEX_HEADER_SIZE:
                        continue
                    can_id_count = self._u32_at(mm, self._INDEX_CAN_ID_COUNT_OFFSET)
                    max_can_ids = self._u32_at(mm, self._INDEX_MAX_CAN_IDS_OFFSET)
                    max_row_pool_size = self._u32_at(mm, self._INDEX_MAX_ROW_POOL_SIZE_OFFSET)
                    max_changed_row_pool_size = self._u32_at(mm, self._INDEX_MAX_CHANGED_ROW_POOL_SIZE_OFFSET)
                    max_ts_pool_size = self._u32_at(mm, self._INDEX_MAX_TS_POOL_SIZE_OFFSET)

                    filter_base = self._INDEX_HEADER_SIZE
                    row_pool_base = filter_base + max_can_ids * self._INDEX_FILTER_SIZE
                    changed_pool_base = row_pool_base + max_row_pool_size * 4
                    ts_pool_base = changed_pool_base + max_changed_row_pool_size * 4

                    for i in range(can_id_count):
                        off = filter_base + i * self._INDEX_FILTER_SIZE
                        if off + self._INDEX_FILTER_SIZE > len(mm):
                            break
                        cid = self._u32_at(mm, off + self._INDEX_FILTER_CAN_ID_OFFSET)
                        rp_off = self._u64_at(mm, off + self._INDEX_FILTER_ROW_OFFSET_OFFSET)
                        crp_off = self._u64_at(mm, off + self._INDEX_FILTER_CHANGED_ROW_OFFSET_OFFSET)
                        tp_off = self._u64_at(mm, off + self._INDEX_FILTER_TS_OFFSET_OFFSET)
                        count = self._u32_at(mm, off + self._INDEX_FILTER_COUNT_OFFSET)
                        changed_count = self._u32_at(mm, off + self._INDEX_FILTER_CHANGED_COUNT_OFFSET)
                        if count == 0 and changed_count == 0:
                            continue
                        catalog[int(cid)].append((
                            seg_path,
                            row_pool_base, int(rp_off), int(count),
                            changed_pool_base, int(crp_off), int(changed_count),
                        ))

                        if int(count) > 0:
                            first_addr = ts_pool_base + int(tp_off) * 8
                            last_addr = ts_pool_base + (int(tp_off) + int(count) - 1) * 8
                            if first_addr + 8 <= len(mm) and last_addr + 8 <= len(mm):
                                first_ts = float(struct.unpack_from("<d", mm, first_addr)[0])
                                last_ts = float(struct.unpack_from("<d", mm, last_addr)[0])
                                cid_i = int(cid)
                                if cid_i not in bounds:
                                    bounds[cid_i] = (first_ts, last_ts)
                                else:
                                    cur_first, _ = bounds[cid_i]
                                    bounds[cid_i] = (cur_first, last_ts)
                finally:
                    mm.close()

        self._can_id_catalog = dict(catalog)
        self._can_id_timestamp_bounds = bounds
        self.can_ids = list(self._can_id_catalog.keys())

    def _ensure_channel_catalog(self):
        if self._channel_catalog:
            return

        catalog: Dict[str, List[tuple]] = defaultdict(list)
        for seg_path in self.channel_index_segment_paths():
            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    if len(mm) < self._CHANNEL_INDEX_HEADER_SIZE:
                        continue
                    channel_count = self._u32_at(mm, self._CHANNEL_INDEX_CHANNEL_COUNT_OFFSET)
                    max_channels = self._u32_at(mm, self._CHANNEL_INDEX_MAX_CHANNELS_OFFSET)
                    max_row_pool_size = self._u32_at(mm, self._CHANNEL_INDEX_MAX_ROW_POOL_SIZE_OFFSET)

                    filter_base = self._CHANNEL_INDEX_HEADER_SIZE
                    row_pool_base = filter_base + max_channels * self._CHANNEL_FILTER_SIZE

                    for i in range(channel_count):
                        off = filter_base + i * self._CHANNEL_FILTER_SIZE
                        if off + self._CHANNEL_FILTER_SIZE > len(mm):
                            break
                        channel_index = self._u32_at(mm, off + self._CHANNEL_FILTER_CHANNEL_INDEX_OFFSET) & 0xFF
                        channel_start = off + self._CHANNEL_FILTER_CHANNEL_OFFSET
                        channel_end = channel_start + self._CHANNEL_FILTER_CHANNEL_CAPACITY
                        channel_raw = bytes(mm[channel_start:channel_end])
                        row_off = self._u64_at(mm, off + self._CHANNEL_FILTER_ROW_OFFSET_OFFSET)
                        count = self._u32_at(mm, off + self._CHANNEL_FILTER_COUNT_OFFSET)
                        if count == 0:
                            continue
                        channel = channel_raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip().lower()
                        if not channel:
                            channel = "unknown"
                        if int(row_off) + int(count) > int(max_row_pool_size):
                            continue
                        catalog[channel].append((
                            seg_path,
                            row_pool_base,
                            int(row_off),
                            int(count),
                            int(channel_index),
                        ))
                finally:
                    mm.close()

        self._channel_catalog = dict(catalog)
        self.channels = list(self._channel_catalog.keys())

    def _normalize_direction_key(self, direction: str) -> str:
        d = str(direction).strip().lower()
        if d in {"tx", "1"}:
            return "tx"
        return "rx"

    def _ensure_direction_catalog(self):
        if self._direction_catalog:
            return

        catalog: Dict[str, List[tuple]] = defaultdict(list)
        for seg_path in self.direction_index_segment_paths():
            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    if len(mm) < self._DIRECTION_INDEX_HEADER_SIZE:
                        continue
                    direction_count = self._u32_at(mm, self._DIRECTION_INDEX_DIRECTION_COUNT_OFFSET)
                    max_directions = self._u32_at(mm, self._DIRECTION_INDEX_MAX_DIRECTIONS_OFFSET)
                    max_row_pool_size = self._u32_at(mm, self._DIRECTION_INDEX_MAX_ROW_POOL_SIZE_OFFSET)

                    filter_base = self._DIRECTION_INDEX_HEADER_SIZE
                    row_pool_base = filter_base + max_directions * self._DIRECTION_FILTER_SIZE

                    for i in range(direction_count):
                        off = filter_base + i * self._DIRECTION_FILTER_SIZE
                        if off + self._DIRECTION_FILTER_SIZE > len(mm):
                            break
                        direction_raw = self._u32_at(mm, off + self._DIRECTION_FILTER_DIRECTION_OFFSET) & 0xFF
                        row_off = self._u64_at(mm, off + self._DIRECTION_FILTER_ROW_OFFSET_OFFSET)
                        count = self._u32_at(mm, off + self._DIRECTION_FILTER_COUNT_OFFSET)
                        if count == 0:
                            continue
                        if int(row_off) + int(count) > int(max_row_pool_size):
                            continue
                        direction_key = "tx" if int(direction_raw) == 1 else "rx"
                        catalog[direction_key].append((
                            seg_path,
                            row_pool_base,
                            int(row_off),
                            int(count),
                            int(direction_raw),
                        ))
                finally:
                    mm.close()

        self._direction_catalog = dict(catalog)

    def _read_direction_row_page_from_mmap(
        self,
        direction: str,
        first_line: int,
        page_size: int,
    ) -> List[int]:
        self._ensure_direction_catalog()
        segs = self._direction_catalog.get(self._normalize_direction_key(direction), [])
        if not segs:
            return []

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        result: List[int] = []
        skipped = 0
        remaining = size

        for seg_path, row_pool_base, row_off, count, _ in segs:
            if remaining <= 0:
                break
            skip_in_seg = max(0, start - skipped)
            if skip_in_seg >= count:
                skipped += count
                continue

            read_start = skip_in_seg
            read_count = min(remaining, count - skip_in_seg)

            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    addr = row_pool_base + (row_off + read_start) * 4
                    if addr + read_count * 4 <= len(mm):
                        result.extend(struct.unpack_from(f"<{read_count}I", mm, addr))
                finally:
                    mm.close()

            remaining -= read_count
            skipped += count

        return result

    def _read_channel_row_page_from_mmap(
        self,
        channel: str,
        first_line: int,
        page_size: int,
    ) -> List[int]:
        self._ensure_channel_catalog()
        segs = self._channel_catalog.get(str(channel).lower(), [])
        if not segs:
            return []

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        result: List[int] = []
        skipped = 0
        remaining = size

        for seg_path, row_pool_base, row_off, count, _ in segs:
            if remaining <= 0:
                break
            skip_in_seg = max(0, start - skipped)
            if skip_in_seg >= count:
                skipped += count
                continue

            read_start = skip_in_seg
            read_count = min(remaining, count - skip_in_seg)

            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    addr = row_pool_base + (row_off + read_start) * 4
                    if addr + read_count * 4 <= len(mm):
                        result.extend(struct.unpack_from(f"<{read_count}I", mm, addr))
                finally:
                    mm.close()

            remaining -= read_count
            skipped += count

        return result

    # ────────────────────────────────────────────────────────────────────
    #  Direct-from-mmap page reads — ZERO RAM caching of row indices.
    #  Only the requested page_size uint32 values are read.
    # ────────────────────────────────────────────────────────────────────
    def _read_row_page_from_mmap(
        self, can_id: int, first_line: int, page_size: int,
    ) -> List[int]:
        """Read a page of row indices for *one* CAN-ID straight from mmap."""
        self._ensure_can_id_catalog()
        segs = self._can_id_catalog.get(int(can_id), [])
        if not segs:
            return []

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        result: List[int] = []
        skipped = 0
        remaining = size

        for seg_path, row_pool_base, rp_off, count, *_ in segs:
            if remaining <= 0:
                break
            skip_in_seg = max(0, start - skipped)
            if skip_in_seg >= count:
                skipped += count
                continue

            read_start = skip_in_seg
            read_count = min(remaining, count - skip_in_seg)

            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    addr = row_pool_base + (rp_off + read_start) * 4
                    if addr + read_count * 4 <= len(mm):
                        result.extend(struct.unpack_from(f"<{read_count}I", mm, addr))
                finally:
                    mm.close()

            remaining -= read_count
            skipped += count

        return result

    def _read_changed_row_page_from_mmap(
        self, can_id: int, first_line: int, page_size: int,
    ) -> List[int]:
        """Read a page of *changed* row indices for one CAN-ID from mmap."""
        self._ensure_can_id_catalog()
        segs = self._can_id_catalog.get(int(can_id), [])
        if not segs:
            return []

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        result: List[int] = []
        skipped = 0
        remaining = size

        for seg_path, _, _, _, changed_pool_base, crp_off, changed_count in segs:
            if remaining <= 0:
                break
            if changed_count == 0:
                continue
            skip_in_seg = max(0, start - skipped)
            if skip_in_seg >= changed_count:
                skipped += changed_count
                continue

            read_start = skip_in_seg
            read_count = min(remaining, changed_count - skip_in_seg)

            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    addr = changed_pool_base + (crp_off + read_start) * 4
                    if addr + read_count * 4 <= len(mm):
                        result.extend(struct.unpack_from(f"<{read_count}I", mm, addr))
                finally:
                    mm.close()

            remaining -= read_count
            skipped += changed_count

        return result

    def _merge_channels_page_from_mmap(
        self,
        channels: List[str],
        first_line: int,
        page_size: int,
    ) -> List[int]:
        self._ensure_channel_catalog()

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        unique_channels: List[str] = []
        seen: Set[str] = set()
        for channel in channels:
            key = str(channel).lower()
            if key not in seen:
                seen.add(key)
                unique_channels.append(key)

        state_key: Tuple[str, ...] = tuple(unique_channels)

        def _build_sources(keys: List[str]) -> Tuple[List[List[tuple]], List[int]]:
            ch_segs_local: List[List[tuple]] = []
            ch_totals_local: List[int] = []
            for key in keys:
                cat = self._channel_catalog.get(key, [])
                if not cat:
                    ch_segs_local.append([])
                    ch_totals_local.append(0)
                    continue
                seg_list = [(sp, rpb, ro, c) for sp, rpb, ro, c, _ in cat if c > 0]
                ch_segs_local.append(seg_list)
                ch_totals_local.append(sum(c for _, _, _, c in seg_list))
            return ch_segs_local, ch_totals_local

        state = self._multi_channel_merge_state.get(state_key)
        if not state or int(state.get("next_first_line", 0)) != start:
            ch_segs, ch_totals = _build_sources(unique_channels)
            heap_q: List[Tuple[int, int, int]] = []
            state = {
                "unique_channels": unique_channels,
                "ch_segs": ch_segs,
                "ch_totals": ch_totals,
                "heap": heap_q,
                "next_first_line": 0,
            }
        else:
            ch_segs = state["ch_segs"]
            ch_totals = state["ch_totals"]
            heap_q = state["heap"]

        mmap_cache: Dict[Path, Tuple[Any, _mmap.mmap]] = {}

        def _open_mm(seg_path: Path) -> _mmap.mmap:
            if seg_path not in mmap_cache:
                fh = open(seg_path, "rb")
                mm = _mmap.mmap(fh.fileno(), 0, access=_mmap.ACCESS_READ)
                mmap_cache[seg_path] = (fh, mm)
            return mmap_cache[seg_path][1]

        def _read_at(ci: int, pos: int) -> int:
            offset = 0
            for seg_path, pool_base, pool_off, count in ch_segs[ci]:
                if pos < offset + count:
                    local = pos - offset
                    mm = _open_mm(seg_path)
                    addr = pool_base + (pool_off + local) * 4
                    return struct.unpack_from("<I", mm, addr)[0]
                offset += count
            raise IndexError(pos)

        def _pop_next() -> Optional[Tuple[int, int, int]]:
            if not heap_q:
                return None
            row_val, ci, cursor = heapq.heappop(heap_q)
            nxt = cursor + 1
            if nxt < ch_totals[ci]:
                heapq.heappush(heap_q, (_read_at(ci, nxt), ci, nxt))
            return row_val, ci, cursor

        try:
            if not heap_q and int(state.get("next_first_line", 0)) == 0:
                for ci in range(len(unique_channels)):
                    if ch_totals[ci] > 0:
                        heapq.heappush(heap_q, (_read_at(ci, 0), ci, 0))

            current_offset = int(state.get("next_first_line", 0))
            while current_offset < start and heap_q:
                popped = _pop_next()
                if popped is None:
                    break
                current_offset += 1

            merged: List[int] = []
            produced = 0
            while produced < size and heap_q:
                popped = _pop_next()
                if popped is None:
                    break
                row_val, _, _ = popped
                merged.append(row_val)
                produced += 1
                current_offset += 1

            state["heap"] = heap_q
            state["next_first_line"] = current_offset
            self._multi_channel_merge_state[state_key] = state
            return merged
        finally:
            for fh, mm in mmap_cache.values():
                mm.close()
                fh.close()

    def _merge_directions_page_from_mmap(
        self,
        directions: List[str],
        first_line: int,
        page_size: int,
    ) -> List[int]:
        self._ensure_direction_catalog()

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        unique_directions: List[str] = []
        seen: Set[str] = set()
        for direction in directions:
            key = self._normalize_direction_key(direction)
            if key not in seen:
                seen.add(key)
                unique_directions.append(key)

        state_key: Tuple[str, ...] = tuple(unique_directions)

        def _build_sources(keys: List[str]) -> Tuple[List[List[tuple]], List[int]]:
            dir_segs_local: List[List[tuple]] = []
            dir_totals_local: List[int] = []
            for key in keys:
                cat = self._direction_catalog.get(key, [])
                if not cat:
                    dir_segs_local.append([])
                    dir_totals_local.append(0)
                    continue
                seg_list = [(sp, rpb, ro, c) for sp, rpb, ro, c, _ in cat if c > 0]
                dir_segs_local.append(seg_list)
                dir_totals_local.append(sum(c for _, _, _, c in seg_list))
            return dir_segs_local, dir_totals_local

        state = self._multi_direction_merge_state.get(state_key)
        if not state or int(state.get("next_first_line", 0)) != start:
            dir_segs, dir_totals = _build_sources(unique_directions)
            heap_q: List[Tuple[int, int, int]] = []
            state = {
                "unique_directions": unique_directions,
                "dir_segs": dir_segs,
                "dir_totals": dir_totals,
                "heap": heap_q,
                "next_first_line": 0,
            }
        else:
            dir_segs = state["dir_segs"]
            dir_totals = state["dir_totals"]
            heap_q = state["heap"]

        mmap_cache: Dict[Path, Tuple[Any, _mmap.mmap]] = {}

        def _open_mm(seg_path: Path) -> _mmap.mmap:
            if seg_path not in mmap_cache:
                fh = open(seg_path, "rb")
                mm = _mmap.mmap(fh.fileno(), 0, access=_mmap.ACCESS_READ)
                mmap_cache[seg_path] = (fh, mm)
            return mmap_cache[seg_path][1]

        def _read_at(di: int, pos: int) -> int:
            offset = 0
            for seg_path, pool_base, pool_off, count in dir_segs[di]:
                if pos < offset + count:
                    local = pos - offset
                    mm = _open_mm(seg_path)
                    addr = pool_base + (pool_off + local) * 4
                    return struct.unpack_from("<I", mm, addr)[0]
                offset += count
            raise IndexError(pos)

        def _pop_next() -> Optional[Tuple[int, int, int]]:
            if not heap_q:
                return None
            row_val, di, cursor = heapq.heappop(heap_q)
            nxt = cursor + 1
            if nxt < dir_totals[di]:
                heapq.heappush(heap_q, (_read_at(di, nxt), di, nxt))
            return row_val, di, cursor

        try:
            if not heap_q and int(state.get("next_first_line", 0)) == 0:
                for di in range(len(unique_directions)):
                    if dir_totals[di] > 0:
                        heapq.heappush(heap_q, (_read_at(di, 0), di, 0))

            current_offset = int(state.get("next_first_line", 0))
            while current_offset < start and heap_q:
                popped = _pop_next()
                if popped is None:
                    break
                current_offset += 1

            merged: List[int] = []
            produced = 0
            while produced < size and heap_q:
                popped = _pop_next()
                if popped is None:
                    break
                row_val, _, _ = popped
                merged.append(row_val)
                produced += 1
                current_offset += 1

            state["heap"] = heap_q
            state["next_first_line"] = current_offset
            self._multi_direction_merge_state[state_key] = state
            return merged
        finally:
            for fh, mm in mmap_cache.values():
                mm.close()
                fh.close()

    def _merge_can_ids_page_from_mmap(
        self,
        can_ids: List[int],
        first_line: int,
        page_size: int,
        changed: bool = False,
    ) -> List[int]:
        """Cursor-based heap merge for multiple CAN IDs.

        Sequential requests (first_line grows by previous page size) continue from
        stored heap/cursor state and run in O(page_size * log N).
        """
        self._ensure_can_id_catalog()

        start = max(0, int(first_line))
        size = max(0, int(page_size))
        if size == 0:
            return []

        # De-duplicate CAN IDs while preserving order
        unique_cids: List[int] = []
        seen: Set[int] = set()
        for cid_raw in can_ids:
            cid = int(cid_raw)
            if cid not in seen:
                seen.add(cid)
                unique_cids.append(cid)

        state_key: Tuple[bool, Tuple[int, ...]] = (bool(changed), tuple(unique_cids))

        def _build_sources(cids: List[int]) -> Tuple[List[List[tuple]], List[int]]:
            cid_segs_local: List[List[tuple]] = []
            cid_totals_local: List[int] = []
            for cid in cids:
                cat = self._can_id_catalog.get(cid, [])
                if not cat:
                    cid_segs_local.append([])
                    cid_totals_local.append(0)
                    continue
                if changed:
                    seg_list = [(sp, cpb, cro, cc) for sp, _, _, _, cpb, cro, cc in cat if cc > 0]
                else:
                    seg_list = [(sp, rpb, ro, c) for sp, rpb, ro, c, _, _, _ in cat if c > 0]
                cid_segs_local.append(seg_list)
                cid_totals_local.append(sum(c for _, _, _, c in seg_list))
            return cid_segs_local, cid_totals_local

        state = self._multi_can_merge_state.get(state_key)
        if not state or int(state.get("next_first_line", 0)) != start:
            cid_segs, cid_totals = _build_sources(unique_cids)
            heap_q: List[Tuple[int, int, int]] = []
            # Fresh state starts at virtual merged offset 0.
            state = {
                "unique_cids": unique_cids,
                "cid_segs": cid_segs,
                "cid_totals": cid_totals,
                "heap": heap_q,
                "next_first_line": 0,
            }
        else:
            cid_segs = state["cid_segs"]
            cid_totals = state["cid_totals"]
            heap_q = state["heap"]

        # Open mmaps lazily, close at end
        mmap_cache: Dict[Path, Tuple[Any, _mmap.mmap]] = {}

        def _open_mm(seg_path: Path) -> _mmap.mmap:
            if seg_path not in mmap_cache:
                fh = open(seg_path, "rb")
                mm = _mmap.mmap(fh.fileno(), 0, access=_mmap.ACCESS_READ)
                mmap_cache[seg_path] = (fh, mm)
            return mmap_cache[seg_path][1]

        def _read_at(ci: int, pos: int) -> int:
            """Read the uint32 row index at virtual position *pos* for CAN-ID #ci."""
            offset = 0
            for seg_path, pool_base, pool_off, count in cid_segs[ci]:
                if pos < offset + count:
                    local = pos - offset
                    mm = _open_mm(seg_path)
                    addr = pool_base + (pool_off + local) * 4
                    return struct.unpack_from("<I", mm, addr)[0]
                offset += count
            raise IndexError(pos)

        def _pop_next() -> Optional[Tuple[int, int, int]]:
            if not heap_q:
                return None
            row_val, ci, cursor = heapq.heappop(heap_q)
            nxt = cursor + 1
            if nxt < cid_totals[ci]:
                heapq.heappush(heap_q, (_read_at(ci, nxt), ci, nxt))
            return row_val, ci, cursor

        try:
            # Seed only when state is fresh
            if not heap_q and int(state.get("next_first_line", 0)) == 0:
                for ci in range(len(unique_cids)):
                    if cid_totals[ci] > 0:
                        heapq.heappush(heap_q, (_read_at(ci, 0), ci, 0))

            # Advance cursor to requested start if needed
            current_offset = int(state.get("next_first_line", 0))
            while current_offset < start and heap_q:
                popped = _pop_next()
                if popped is None:
                    break
                current_offset += 1

            merged: List[int] = []
            produced = 0
            while produced < size and heap_q:
                popped = _pop_next()
                if popped is None:
                    break
                row_val, _, _ = popped
                merged.append(row_val)
                produced += 1
                current_offset += 1

            state["heap"] = heap_q
            state["next_first_line"] = current_offset
            self._multi_can_merge_state[state_key] = state

            return merged
        finally:
            for fh, mm in mmap_cache.values():
                mm.close()
                fh.close()

    def refresh_can_ids_runtime(self):
        # Clear lightweight catalog so it re-scans on next demand
        self._can_id_catalog.clear()
        self._can_id_timestamp_bounds.clear()
        self._global_timestamp_bounds = None
        self._channel_catalog.clear()
        self._direction_catalog.clear()
        self.channels = []
        # Clear cursor states for multi-CAN pagination
        self._multi_can_merge_state.clear()
        self._multi_channel_merge_state.clear()
        self._multi_direction_merge_state.clear()
        # Rebuild catalog (cheap — only filter metadata, no row data)
        self._ensure_can_id_catalog()
        self._ensure_channel_catalog()
        self._ensure_direction_catalog()

    def _get_data_entry_layout(self, seg_path: Path) -> Tuple[int, Any]:
        return self._ENTRY_SIZE, None

    def _decode_entry_from_mmap(self, mm: _mmap.mmap, offset: int) -> ParsedEntry:
        entry_size = int(self._ENTRY_SIZE)
        return ParsedEntry.from_buffer_copy(mm[offset:offset + entry_size])

    def _read_entry_by_global_row(
        self,
        segs: List[Path],
        global_row: int,
        seg_cache: Optional[Dict[int, Tuple[Any, _mmap.mmap, int, Any]]] = None,
    ) -> Optional[ParsedEntry]:
        seg_idx = int(global_row) // self.mmap_capacity
        local_idx = int(global_row) % self.mmap_capacity
        if seg_idx < 0 or seg_idx >= len(segs):
            return None

        if seg_cache is None:
            return self._read_entry_from_segment(segs[seg_idx], local_idx)

        if seg_idx not in seg_cache:
            f = open(segs[seg_idx], "rb")
            mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
            entry_size, _ = self._get_data_entry_layout(segs[seg_idx])
            seg_cache[seg_idx] = (f, mm, entry_size, None)

        _, mm, entry_size, _ = seg_cache[seg_idx]
        offset = self._DATA_HEADER_SIZE + local_idx * entry_size
        if offset + entry_size > len(mm):
            return None
        return self._decode_entry_from_mmap(mm, offset)

    """ O(row_indices) """
    def get_messages_by_row_indices(self, row_indices: List[int]) -> List[ParsedEntry]:
        segs = self.data_segment_paths()
        if not segs:
            return []

        row_list = row_indices
        result: List[ParsedEntry] = []
        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}
        try:
            for global_row in row_list:
                entry = self._read_entry_by_global_row(segs, int(global_row), seg_cache)
                if entry is not None:
                    result.append(entry)
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()
        return result


    def _read_segment_write_count(self, seg_path: Path) -> int:
        try:
            with open(seg_path, "rb") as f:
                hdr = f.read(8)
                if len(hdr) < 8:
                    return 0
                return int(struct.unpack("<Q", hdr)[0])
        except Exception:
            return 0

    def _read_segment_capacity(self, seg_path: Path) -> int:
        try:
            with open(seg_path, "rb") as f:
                f.seek(8)
                raw = f.read(4)
                if len(raw) < 4:
                    return self.mmap_capacity
                cap = int(struct.unpack("<I", raw)[0])
                return cap if cap > 0 else self.mmap_capacity
        except Exception:
            return self.mmap_capacity

    def refresh_mmap_runtime(self):
        segs = self.data_segment_paths()
        self.mmap_file_count = len(segs)
        if segs:
            self.mmap_capacity = self._read_segment_capacity(segs[0])
        self.total_lines = sum(self._read_segment_write_count(seg) for seg in segs)

    def mmap_file_total(self):
        return self.mmap_file_count

    @property
    def loaded_lines(self):
        return self.total_lines

    def _read_entry_from_segment(self, seg_path: Path, local_idx: int) -> Optional[ParsedEntry]:
        entry_size, _ = self._get_data_entry_layout(seg_path)
        offset = self._DATA_HEADER_SIZE + local_idx * entry_size
        try:
            with open(seg_path, "rb") as f:
                mm = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
                try:
                    if offset + entry_size > len(mm):
                        return None
                    line = self._decode_entry_from_mmap(mm, offset)
                finally:
                    mm.close()
        except Exception:
            return None

        return line

    def get_page_lines(self, first_line: int, page_size: int) -> List[ParsedEntry]:
        start = max(0, int(first_line))
        end = start + max(0, int(page_size))
        return self.get_messages_by_row_indices(range(start, end))

    def get_all_can_ids(self) -> List[int]:
        self._ensure_can_id_catalog()
        return self.can_ids

    def get_all_channels(self) -> List[str]:
        self._ensure_channel_catalog()
        return self.channels
    
    def get_all_lines(self) -> List[ParsedEntry]:
        return self.get_page_lines(0, 20_000)

    def get_row_indices_by_list_id(self, can_ids: List[int]) -> List[int]:
        result: List[int] = []
        for can_id in can_ids:
            cid = int(can_id)
            total = self.get_total_count_by_can_id(cid)
            if total > 0:
                result.extend(self._read_row_page_from_mmap(cid, 0, total))
        return result

    def get_row_indices_by_channel(self, channel: str) -> List[int]:
        total = self.get_total_count_by_channel(channel)
        if total <= 0:
            return []
        return self._read_channel_row_page_from_mmap(channel, 0, total)

    def get_row_indices_by_direction(self, direction: str) -> List[int]:
        total = self.get_total_count_by_direction(direction)
        if total <= 0:
            return []
        return self._read_direction_row_page_from_mmap(direction, 0, total)

    def get_row_indices_by_directions(self, directions: List[str]) -> List[int]:
        total = self.get_total_count_by_directions(directions)
        if total <= 0:
            return []
        return self._merge_directions_page_from_mmap(directions, 0, total)

    def get_row_indices_by_channels(self, channels: List[str]) -> List[int]:
        total = self.get_total_count_by_channels(channels)
        if total <= 0:
            return []
        return self._merge_channels_page_from_mmap(channels, 0, total)

    def filter_row_indices_by_direction(self, direction: str, row_indices) -> List[int]:
        if row_indices is None:
            return self.get_row_indices_by_direction(direction)
        rows = list(row_indices)
        lines = self.get_messages_by_row_indices(rows)
        d = direction.lower()
        return [rows[i] for i, entry in enumerate(lines) if entry.direction_str.lower() == d]

    def filter_row_indices_by_channel(self, channel: str, row_indices) -> List[int]:
        rows = list(row_indices)
        lines = self.get_messages_by_row_indices(rows)
        target = str(channel).lower()
        return [rows[i] for i, entry in enumerate(lines) if entry.channel_str.lower() == target]

    def filter_row_indices_by_timestamp_range(self, from_t: float, to_t: float, row_indices) -> List[int]:
        rows = list(row_indices)
        if not rows:
            return []

        lo_t = float(from_t)
        hi_t = float(to_t)
        if lo_t > hi_t:
            lo_t, hi_t = hi_t, lo_t

        segs = self.data_segment_paths()
        if not segs:
            return []

        seg_cache: Dict[int, Tuple[Any, _mmap.mmap, int, Any]] = {}

        def ts_at_pos(pos: int) -> float:
            ts = self._read_timestamp_by_global_row_cached(segs, int(rows[pos]), seg_cache)
            return float("inf") if ts is None else float(ts)

        try:
            lo, hi = 0, len(rows)
            while lo < hi:
                mid = (lo + hi) // 2
                if ts_at_pos(mid) < lo_t:
                    lo = mid + 1
                else:
                    hi = mid
            start = lo

            lo, hi = start, len(rows)
            while lo < hi:
                mid = (lo + hi) // 2
                if ts_at_pos(mid) <= hi_t:
                    lo = mid + 1
                else:
                    hi = mid
            end = lo

            return rows[start:end]
        finally:
            for f, mm, _, _ in seg_cache.values():
                mm.close()
                f.close()

