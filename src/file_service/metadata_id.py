from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, TypeAlias
from uuid import UUID, uuid4


MetadataIdValue: TypeAlias = Hashable

""" Identifier of a metadata object.
The path is merely one storage implementation. 
NOTE: This is the generic identifier so when quiry metadata, this only works for a merge metadata storage
service.get_entries(resource_id)
NOTE: This identity is pickable and share-able between the processes as the key for quiery database at low level C++
"""
@dataclass(frozen=True, slots=True)
class MetadataId:
    value: MetadataIdValue

    def __post_init__(self) -> None:
        if not isinstance(self.value, Hashable):
            raise TypeError("MetadataId value must be hashable")

    @classmethod
    def new(cls) -> MetadataId:
        return cls(uuid4())

    @classmethod
    def from_value(cls, value: MetadataId | MetadataIdValue) -> MetadataId:
        if isinstance(value, cls):
            return value
        return cls(value)

    def path_token(self) -> str:
        value = self.value
        if isinstance(value, UUID):
            return value.hex
        return str(value)

    """ NOTE: this is for take the actual filepath for lower level layer C++ native function call"""
    def __str__(self) -> str:
        return self.path_token()

@dataclass(frozen=True, slots=True)
class DBCId(MetadataId):
    pass

@dataclass(frozen=True, slots=True)
class LogId(MetadataId):
    pass

@dataclass(frozen=True, slots=True)
class DecodeId:
    value: MetadataIdValue
    log_id: LogId
    dbc_id: DBCId

    def __post_init__(self) -> None:
        if not isinstance(self.value, Hashable):
            raise TypeError("DecodeId value must be hashable")

    @classmethod
    def new(cls, log_id: LogId, dbc_id: DBCId) -> DecodeId:
        return cls(value=uuid4(), log_id=log_id, dbc_id=dbc_id)

    @classmethod
    def from_value(
        cls,
        value: DecodeId | MetadataIdValue,
        log_id: LogId,
        dbc_id: DBCId,
    ) -> DecodeId:
        if isinstance(value, cls):
            return value
        return cls(value=value, log_id=log_id, dbc_id=dbc_id)

    def path_token(self) -> str:
        if isinstance(self.value, UUID):
            return self.value.hex
        return str(self.value)

    def __str__(self) -> str:
        return self.path_token()


__all__ = ["MetadataId", "MetadataIdValue", "DBCId", "LogId", "DecodeId"]