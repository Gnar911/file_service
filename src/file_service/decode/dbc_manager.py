from __future__ import annotations

import os
import pickle
from os.path import abspath
from pathlib import Path
from typing import Dict, Optional

import cantools
from can_sdk.data_object import CANDBInfo, CANDBInfoType
from lw.logger_setup import LOG
from lw.observer import ObservableEvent


DATABASE_TEMP = "db_temp.dbc"


class CANDBManager:
    """DBC parser/holder for decode flow.

    This manager does not own pkl paths and does not support mixed DB mode.
    Repository/service layers own persistence paths.
    """

    def __init__(self):
        self._candb_dict: Dict[str, CANDBInfo] = {}
        self._cur_sel_db: Optional[str] = None
        self.event_on_db_changed = ObservableEvent()
        self.event_on_db_list_changed = ObservableEvent()
        self.event_on_db_loaded = ObservableEvent(CANDBInfo)

    def init_model(self) -> None:
        return

    def exit_model(self) -> None:
        return

    @property
    def candb_dict(self) -> Dict[str, CANDBInfo]:
        return self._candb_dict

    @property
    def db_path_ogirin(self) -> Optional[str]:
        return self._cur_sel_db

    @db_path_ogirin.setter
    def db_path_ogirin(self, new_path: Optional[str]) -> None:
        if new_path == self._cur_sel_db:
            return
        self._cur_sel_db = new_path
        self.event_on_db_changed.notify()

    def clear_model(self) -> None:
        self._cur_sel_db = None
        self._candb_dict = {}
        self.event_on_db_list_changed.notify()

    def _clean_database(self, original_path: str, temp_file: str) -> str:
        with open(original_path, "rb") as src, open(temp_file, "wb") as dst:
            for line in src:
                if b"CAT_DEF_" not in line:
                    dst.write(line)
        return abspath(temp_file)

    def load_database(self, file_path: str, dump_pkl_path: str | Path | None = None):
        res = self.read_from_db(file_path)
        if file_path in self._candb_dict:
            self.db_path_ogirin = file_path
        candb_info = self._candb_dict.get(file_path)
        if candb_info is None:
            return None
        if dump_pkl_path is not None:
            try:
                pkl_path = Path(dump_pkl_path)
                pkl_path.parent.mkdir(parents=True, exist_ok=True)
                with pkl_path.open("wb") as pkl_file:
                    pickle.dump(candb_info, pkl_file, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                LOG.exception("Failed to dump DBC pkl to %s", dump_pkl_path)
                return None
        self.event_on_db_loaded.notify(candb_info)
        return res

    def read_from_db(self, file_path: str):
        LOG.debug("read_from_db: %s", file_path)
        if file_path in self._candb_dict:
            LOG.debug("Load base database existed")
            return []

        try:
            candb = CANDBInfo(type=CANDBInfoType.NONE)
            temp_file = self._clean_database(file_path, DATABASE_TEMP)
            db = cantools.database.load_file(temp_file)
            candb.db = db
            candb.file_path = file_path
            candb.backup_file_path = temp_file

            self._candb_dict[file_path] = candb
            self.db_path_ogirin = file_path
            self.event_on_db_list_changed.notify()
            LOG.info("Load base database, total: %d messages", len(candb.messages))
            return candb.messages
        except Exception:
            LOG.exception("Unhandled exception while loading DBC: %s", file_path)
            return None

    def get_main_db_file(self) -> Optional[str]:
        return self._cur_sel_db

    def get_list_all_db_file(self) -> list[str]:
        return list(self._candb_dict.keys())
