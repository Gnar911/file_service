from __future__ import annotations

import os
import pickle
from os.path import abspath
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass, field

import cantools
# from canapp.data_object import CANDBInfo, CANDBInfoType
from lw.logger_setup import LOG
from lw.observer import ObservableEvent
from lw.PickleStorage import PickleStorage
from file_service.metadata_id import DBCId
from typing import Optional, List, Dict, Callable, Any, Tuple, Set
from collections import defaultdict
from enum import Enum
from pathlib import Path
import mmap as _mmap
import struct
import heapq
import cantools
from cantools.database import Database

DATABASE_TEMP = "db_temp.dbc"

"""NOTE: 

Workspace

Powertrain.dbc
Body.dbc
ADAS.dbc
Gateway.dbc

        │
User clicks "Body.dbc"
        │
        ▼
load pickle
        │
        ▼
CANDBInfo
        │
        ▼
Display

"""

class CANDBInfoType(Enum):
    MIXED = "MIXED"
    NONE = "NONE"

""" NOTE: Have the ability to decode messages, represent a database from disk"""
@dataclass
class CANDBInfo(Database):
    # _db: Database = field(default_factory=Database)
    file_path: str = field(default="")
    backup_file_path: str = field(default="")
    # type: CANDBInfoType = field(default=CANDBInfoType.NONE)
    # _message_index: Dict[int, object] = field(default_factory=dict)
    # _message_index_size: int = field(default=-1)

    @property
    def name(self):
        return basename(self.file_path)

    # @property
    # def messages(self) -> dict[int, cantools.database.can.Message] | dict[int, list[cantools.database.can.Message]]:
    #     self._ensure_message_index()
    #     return self._message_index

    # @property
    # def comboboxs(self) -> list[str]:
    #     return [
    #         f"[{msg.frame_id:03X}] {msg.name}"
    #         for msg in sorted(self.db.messages, key=lambda m: m.frame_id)
    #     ]

    # @property
    # def db(self):
    #     return self._db

    # @db.setter
    # def db(self, value):
    #     if value is self._db:
    #         return
    #     self._db = value
    #     self._message_index.clear()
    #     self._message_index_size = -1

    # def _ensure_message_index(self):
    #     current_size = len(self.db.messages)
    #     if self._message_index_size == current_size and self._message_index:
    #         return

    #     if self.type == CANDBInfoType.MIXED:
    #         grouped: Dict[int, list[cantools.database.can.Message]] = {}
    #         for msg in self.db.messages:
    #             grouped.setdefault(msg.frame_id, []).append(msg)
    #         self._message_index = grouped
    #     else:
    #         by_id: Dict[int, cantools.database.can.Message] = {}
    #         for msg in self.db.messages:
    #             by_id[msg.frame_id] = msg
    #         self._message_index = by_id

    #     self._message_index_size = current_size

    # def _resolve_message_obj(self, msg_obj) -> Optional[cantools.database.can.Message]:
    #     """More than one Message for this CAN-ID → return None, let app handle."""
    #     if isinstance(msg_obj, list):
    #         if len(msg_obj) == 1:
    #             return msg_obj[0]
    #         if len(msg_obj) > 1:
    #             LOG.warning("More than one Message for this CAN-ID")
    #             return None
    #     return msg_obj

    # def get_message(self, can_id) -> Optional[cantools.database.can.Message]:
    #     self._ensure_message_index()
    #     msg_obj = self._message_index.get(can_id)
    #     if msg_obj is None:
    #         return None
    #     return self._resolve_message_obj(msg_obj)
    
    # @property
    # def message_defs(self) -> list[cantools.database.can.Message]:
    #     return self.db.messages
    
    # def get_message_name(self, can_id) -> str:
    #     self._ensure_message_index()
    #     msg_obj = self._message_index.get(can_id)

    #     if msg_obj is None:
    #         return ""

    #     if isinstance(msg_obj, list):
    #         if len(msg_obj) > 1:
    #             return "Unresolved"
    #         if len(msg_obj) == 1:
    #             message = msg_obj[0]
    #             return str(getattr(message, "name", "") or "")
    #         return ""

    #     return str(getattr(msg_obj, "name", "") or "")
    

class CANDBManager:
    """
    Hold the currently loaded CAN database.

    This manager owns only the in-memory CANDBInfo.
    Persistence is delegated to PickleStorage.
    """

    def __init__(
        self,
        storage: PickleStorage[CANDBInfo],
    ) -> None:

        self._storage = storage

        """ NOTE: The DBC Id already define the 2 state loaded and not loaded when None
                because it is no longer a generic storage but a domain app class -> deal with DBCId
        """
        #self._current: CANDBInfo | None = None
        # self.event_on_db_changed = ObservableEvent(CANDBInfo)
        # self.event_on_db_loaded = ObservableEvent(CANDBInfo)
        # self.event_on_db_saved = ObservableEvent(CANDBInfo)

    # def clear(self) -> None:
    #     self.current = None

    def load_database(
        self,
        id: DBCId,
    ) -> CANDBInfo:
        """
        Load a cached CANDBInfo from pickle.
        """

       # try:
        candb = self._storage.load(id.path_token())
        # except Exception:
        #     LOG.exception(
        #         "Failed load_database DBC: %s",
        #         id.path_token(),
        #     )
        #     candb = None

        #NOTE: Notify to app
        #self.current = candb

        #self.event_on_db_loaded.notify(candb)
        #self.event_on_db_changed.notify(candb)

        #return self.current
        return candb

    # def save_database(self, file_path: str) -> bool:
    #     """
    #     Save the current CANDBInfo to pickle.
    #     """

    #     # if self.current is None:
    #     #     return False

    #     try:
    #         self._storage.save(
    #             file_path,
    #             self.current,
    #         )
    #         return True

    #     except Exception:

    #         LOG.exception(
    #             "Failed save_database DBC: %s",
    #             self.current,
    #         )
    #         return False

        #self.event_on_db_saved.notify(self._current)

    """ NOTE: Return CANDBInfo will only work for caller, not observers 
            THROW for thread function, no return, data store in disk
    """
    def parse_database(
        self,
        file_path: str,
        id: DBCId  # Specifying the target save ID
    ) -> None:
        """
        Parse an original .dbc file.
        """

        #try:
        candb = CANDBInfo(
            type=CANDBInfoType.NONE,
        )

        temp_file = self._clean_database(
            file_path,
            DATABASE_TEMP,
        )

        candb.db = cantools.database.load_file(
            temp_file,
        )

        candb.file_path = file_path
        candb.backup_file_path = temp_file


        # self.event_on_db_changed.notify(candb)

        LOG.info(
            "Loaded DBC: %s (%d messages)",
            file_path,
            len(candb.messages),
        )

        self._storage.save(
            id.path_token(),
            candb,
        )

            #return candb
            #return self.current

        # except Exception:

        #     LOG.exception(
        #         "Failed parsing DBC: %s",
        #         file_path,
        #     )

            #return None
            #candb = None
            # return None
        #finally:
            #NOTE: Notify to app
            #self.current = candb
            #return self.current
        

    def _clean_database(self, original_path: str, temp_file: str) -> str:
        with open(original_path, "rb") as src, open(temp_file, "wb") as dst:
            for line in src:
                if b"CAT_DEF_" not in line:
                    dst.write(line)
        return abspath(temp_file)