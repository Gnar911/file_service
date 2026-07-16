from __future__ import annotations

import os
import pickle
from os.path import abspath
from pathlib import Path
from typing import Dict, Optional

import cantools
from canapp.data_object import CANDBInfo, CANDBInfoType
from lw.logger_setup import LOG
from lw.observer import ObservableEvent
from lw.PickleStorage import PickleStorage

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
# class CANDBManager:
#     """DBC parser/holder for decode flow.

#     This manager does not own pkl paths and does not support mixed DB mode.
#     Repository/service layers own persistence paths.
#     """
#     def __init__(self):
#         self._candb_dict: Dict[str, CANDBInfo] = {}
#         self._cur_sel_db: Optional[str] = None
#         self.event_on_db_changed = ObservableEvent()
#         self.event_on_db_list_changed = ObservableEvent()
#         self.event_on_db_loaded = ObservableEvent(CANDBInfo)
#         self._storage = PickleStorage()

#     @property
#     def candb_dict(self) -> Dict[str, CANDBInfo]:
#         return self._candb_dict

#     @property
#     def db_path_ogirin(self) -> Optional[str]:
#         return self._cur_sel_db

#     @db_path_ogirin.setter
#     def db_path_ogirin(self, new_path: Optional[str]) -> None:
#         if new_path == self._cur_sel_db:
#             return
#         self._cur_sel_db = new_path
#         self.event_on_db_changed.notify()

#     def clear_model(self) -> None:
#         self._cur_sel_db = None
#         self._candb_dict = {}
#         self.event_on_db_list_changed.notify()

#     def get_main_db_file(self) -> Optional[str]:
#         return self._cur_sel_db

#     def get_list_all_db_file(self) -> list[str]:
#         return list(self._candb_dict.keys())


    #""" NOTE: Load database means load database on the disk pkl file"""
    # def load_database(self, file_path: str, dump_pkl_path: str | Path):
    #     res = self.read_from_db(file_path)
    #     if file_path in self._candb_dict:
    #         self.db_path_ogirin = file_path
    #     candb_info = self._candb_dict.get(file_path)
    #     if candb_info is None:
    #         return None
    #     try:
    #         pkl_path = Path(dump_pkl_path)
    #         pkl_path.parent.mkdir(parents=True, exist_ok=True)
    #         with pkl_path.open("wb") as pkl_file:
    #             pickle.dump(candb_info, pkl_file, protocol=pickle.HIGHEST_PROTOCOL)
    #     except Exception:
    #         LOG.exception("Failed to dump DBC pkl to %s", dump_pkl_path)
    #         return None
    #     self.event_on_db_loaded.notify(candb_info)
    #     return res
        

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
        self._current: CANDBInfo | None = None

        self.event_on_db_changed = ObservableEvent(CANDBInfo)
        self.event_on_db_loaded = ObservableEvent(CANDBInfo)
        self.event_on_db_saved = ObservableEvent(CANDBInfo)

    @property
    def current(self) -> CANDBInfo | None:
        return self._current

    @current.setter
    def current(self, value):
        if self._current == value:
            return

        self._current = value
        self.event_on_db_changed.notify()

    # @property
    # def current_file_path(self) -> str | None:
    #     if self._current is None:
    #         return None
    #     return self._current.file_path

    def clear(self) -> None:
        self.current = None

    def load_database(
        self,
        file_path: str,
    ) -> CANDBInfo | None:
        """
        Load a cached CANDBInfo from pickle.
        """

        try:
            candb = self._storage.load(file_path)
        except Exception:
            LOG.exception(
                "Failed load_database DBC: %s",
                file_path,
            )
            candb = None

        #NOTE: Notify to app
        self.current = candb

        #self.event_on_db_loaded.notify(candb)
        #self.event_on_db_changed.notify(candb)

        return self.current

    def save_database(self) -> bool:
        """
        Save the current CANDBInfo to pickle.
        """

        if self.current is None:
            return False

        try:
            self._storage.save(
                self.current.file_path,
                self.current,
            )
            return True

        except Exception:

            LOG.exception(
                "Failed save_database DBC: %s",
                self.current,
            )
            return False

        #self.event_on_db_saved.notify(self._current)

    def parse_database(
        self,
        file_path: str,
    ) -> CANDBInfo | None:
        """
        Parse an original .dbc file.
        """

        try:
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

            #return self.current

        except Exception:

            LOG.exception(
                "Failed parsing DBC: %s",
                file_path,
            )
            candb = None
            # return None
        finally:
            #NOTE: Notify to app
            self.current = candb
            return self.current
        

    def _clean_database(self, original_path: str, temp_file: str) -> str:
        with open(original_path, "rb") as src, open(temp_file, "wb") as dst:
            for line in src:
                if b"CAT_DEF_" not in line:
                    dst.write(line)
        return abspath(temp_file)