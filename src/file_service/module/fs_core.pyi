from __future__ import annotations

from typing import Iterable, Sequence


def abi_version() -> int: ...
def can_decoder_run(parsed_mmap_token: str, decoder: CanDecoder) -> DecodeError: ...
def run_worker_segmented(file_path: str, token_id: str, fmt: int) -> int: ...
def parse_file(path: str) -> list[ParsedEntry]: ...
def parse_file_with_fmt(path: str, fmt: int) -> list[ParsedEntry]: ...
def parse_line(line: str, line_num: int = 0) -> ParsedEntry | None: ...

FMT_UNKNOWN: int
FMT_CANOE: int
FMT_CANOE_FULL: int
FMT_CANOE_CMP: int
FMT_CANCMD: int
FMT_FILTER: int
FMT_CANSUKE: int
FMT_CANCMD_T2: int
FMT_CANCMD_T3: int


class MessageDef:
    can_id: int
    signal_count: int
    msg_length: int
    signal_offset: int
    padding: int

    def __init__(self) -> None: ...


class SignalDef:
    start_bit: int
    bit_length: int
    byte_order: int
    is_signed: int
    has_choices: int
    padding1: int
    scale: float
    offset: float

    def __init__(self) -> None: ...


class DecodedSignal:
    signal_name: str
    raw_value: int
    phys_value: float

    def __init__(self) -> None: ...


class DecodeError:
    rc: int
    error_message: str

    def __init__(self) -> None: ...


class ParsedEntry:
    line_number: int
    timestamp: float
    last_timestamp: float
    can_id: int
    direction: int
    data_len: int
    changed: int
    data: list[int]
    channel: str

    def __init__(self) -> None: ...


class ParsedMmapInterface:
    def __init__(self, token_id: str) -> None: ...

    def open_mmap(self) -> None: ...
    def write_entries(self, parsed_entries: Iterable[ParsedEntry]) -> None: ...
    def close_mmap(self) -> None: ...

    def read_page(self, first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_can_id(self, can_id: int, first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_can_ids(self, can_ids: Sequence[int], first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_can_id_changed(self, can_id: int, first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_can_ids_changed(self, can_ids: Sequence[int], first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_channel(self, channel: str, first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_channels(self, channels: Sequence[str], first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_direction(self, direction: str, first: int, last: int) -> list[ParsedEntry]: ...
    def read_page_from_directions(self, directions: Sequence[str], first: int, last: int) -> list[ParsedEntry]: ...

    def fetch_count(self) -> int: ...
    def last_error_code(self) -> int: ...


class CanDecoder:
    def load_db(self, messages: list[MessageDef], signals: list[SignalDef]) -> int: ...
    def free_db(self) -> None: ...
    def is_loaded(self) -> bool: ...
    def decode_entry(self, entry: ParsedEntry, max_signals: int = 0) -> list[DecodedSignal]: ...
