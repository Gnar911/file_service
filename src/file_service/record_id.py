from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, TypeAlias
from uuid import UUID, uuid4


RecordIdValue: TypeAlias = Hashable


@dataclass(frozen=True, slots=True)
class RecordId:
    value: RecordIdValue

    def __post_init__(self) -> None:
        if not isinstance(self.value, Hashable):
            raise TypeError("RecordId value must be hashable")

    @classmethod
    def new(cls) -> RecordId:
        return cls(uuid4())

    @classmethod
    def from_value(cls, value: RecordId | RecordIdValue) -> RecordId:
        if isinstance(value, cls):
            return value
        return cls(value)

    def path_token(self) -> str:
        value = self.value
        if isinstance(value, UUID):
            return value.hex
        return str(value)

    def __str__(self) -> str:
        return self.path_token()


__all__ = ["RecordId", "RecordIdValue"]