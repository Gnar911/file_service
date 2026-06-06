from __future__ import annotations

from dataclasses import dataclass
import struct
from multiprocessing import shared_memory


# Shared CAN ring layout used by can_service producers/consumers.
ENTRY_SIZE = 128
# Keep the payload near 1,000,000 bytes total.
CAPACITY = 7_812
BATCH_SIZE = 256

# Header stores monotonic write index.
HEADER_STRUCT = struct.Struct("<Q")

# Entry layout (little-endian, fixed-size):
#   timestamp   double    8 B
#   can_id      uint32    4 B
#   direction   uint8     1 B
#   data_len    uint8     1 B
#   data        bytes    64 B
#   channel     bytes    16 B
#   _pad        34 B
ENTRY_STRUCT = struct.Struct("<d I B B 64s 16s 34x")

HEADER_SIZE = HEADER_STRUCT.size
PAYLOAD_SIZE = CAPACITY * ENTRY_SIZE
SHM_SIZE = HEADER_SIZE + PAYLOAD_SIZE


@dataclass(slots=True)
class RingHeader:
	write_idx: int = 0


@dataclass(slots=True)
class CanLogRingPayload:
	timestamp: float
	can_id: int
	direction: int
	data_len: int
	data: bytes
	channel: str


class CanLogRingHandler:
	"""Shared-memory CAN ring handler (header + payload)."""

	def __init__(self, mmap_name: str, create: bool = False):
		if not str(mmap_name):
			raise ValueError("mmap_name is required")
		self.mmap_name = str(mmap_name)
		self._create = bool(create)
		self._owner = False
		self._shm: shared_memory.SharedMemory | None = None

	@property
	def shm(self) -> shared_memory.SharedMemory:
		if self._shm is None:
			raise RuntimeError("shared memory is not opened")
		return self._shm

	@property
	def buf(self):
		return self.shm.buf

	def open(self) -> None:
		if self._shm is not None:
			return
		if self._create:
			self._shm = shared_memory.SharedMemory(name=self.mmap_name, create=True, size=SHM_SIZE)
			self._owner = True
			self.write_header(RingHeader(write_idx=0))
			return
		self._shm = shared_memory.SharedMemory(name=self.mmap_name, create=False)
		self._owner = False

	def close(self, unlink: bool = False) -> None:
		if self._shm is None:
			return
		self._shm.close()
		if unlink and self._owner:
			self._shm.unlink()
		self._shm = None

	def format(self, write_idx: int = 0, zero_payload: bool = True) -> None:
		if zero_payload:
			self.buf[HEADER_SIZE:SHM_SIZE] = b"\x00" * PAYLOAD_SIZE
		self.write_header(RingHeader(write_idx=int(write_idx)))

	def read_header(self) -> RingHeader:
		(write_idx,) = HEADER_STRUCT.unpack_from(self.buf, 0)
		return RingHeader(write_idx=int(write_idx))

	def write_header(self, header: RingHeader) -> None:
		HEADER_STRUCT.pack_into(self.buf, 0, int(header.write_idx))

	@staticmethod
	def slot_offset(slot: int) -> int:
		return HEADER_SIZE + (int(slot) * ENTRY_SIZE)

	@staticmethod
	def _to_data_64(data: bytes) -> bytes:
		raw = bytes(data or b"")[:64]
		return raw + (b"\x00" * (64 - len(raw)))

	@staticmethod
	def _to_channel_16(channel: str | bytes) -> bytes:
		if isinstance(channel, bytes):
			raw = channel[:16]
		else:
			raw = str(channel).encode("ascii", errors="ignore")[:16]
		return raw + (b"\x00" * (16 - len(raw)))

	@staticmethod
	def _decode_channel(channel_16: bytes) -> str:
		return bytes(channel_16).split(b"\x00", 1)[0].decode("ascii", errors="ignore")

	def write(self, payload: CanLogRingPayload) -> int:
		header = self.read_header()
		idx = int(header.write_idx)
		slot = idx % CAPACITY
		offset = self.slot_offset(slot)

		data_64 = self._to_data_64(payload.data)
		channel_16 = self._to_channel_16(payload.channel)
		data_len = max(0, min(int(payload.data_len), 64))

		self.buf[offset: offset + ENTRY_SIZE] = ENTRY_STRUCT.pack(
			float(payload.timestamp),
			int(payload.can_id),
			int(payload.direction),
			data_len,
			data_64,
			channel_16,
		)
		self.write_header(RingHeader(write_idx=idx + 1))
		return idx

	def read_slot(self, slot: int) -> CanLogRingPayload:
		bounded_slot = int(slot) % CAPACITY
		offset = self.slot_offset(bounded_slot)
		ts, can_id, direction, data_len, data_64, channel_16 = ENTRY_STRUCT.unpack_from(self.buf, offset)
		size = max(0, min(int(data_len), 64))
		return CanLogRingPayload(
			timestamp=float(ts),
			can_id=int(can_id),
			direction=int(direction),
			data_len=size,
			data=bytes(data_64[:size]),
			channel=self._decode_channel(channel_16),
		)

	def read_by_index(self, write_index: int) -> CanLogRingPayload:
		return self.read_slot(int(write_index) % CAPACITY)

	def read_latest(self, count: int) -> list[CanLogRingPayload]:
		n = max(0, int(count))
		if n == 0:
			return []

		write_idx = self.read_header().write_idx
		available = min(write_idx, CAPACITY)
		n = min(n, available)
		start = write_idx - n
		return [self.read_by_index(i) for i in range(start, write_idx)]

	def __enter__(self) -> CanLogRingHandler:
		self.open()
		return self

	def __exit__(self, exc_type, exc, tb) -> None:
		self.close()
