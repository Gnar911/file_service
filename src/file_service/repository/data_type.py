from __future__ import annotations
from dataclasses import dataclass, field

### Internal system data type wrapping mmap data set storage

# Parsing and Recording must have
@dataclass
class MMapDataSet:
    data_mmap_path: str
    index_mmap_path: str
    channel_index_mmap_path: str = field(default="")
    direction_index_mmap_path: str = field(default="")

# For decoding
@dataclass
class DecodedDataset:
    signal_dir_mmap_path: str
    row_index_changed_mmap_path: str
    row_index_mmap_path: str
    value_mmap_path: str
    rawvalue_mmap_path: str
