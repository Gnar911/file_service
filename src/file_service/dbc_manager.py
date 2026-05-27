from dataclasses import dataclass, field
from lw.logger_setup import LOG
import cantools
import cantools.database
from enum import Enum
from typing import Optional, List, Dict, Tuple
from os.path import abspath, basename
import os
import pickle
import json
from lw.observer import ObservableEvent

DATABASE_BACKUP = "db_backup.dbc"
DATABASE_TEMP = "db_temp.dbc"
MIXED_DB_KEY = "__All DBC__"

# Per-DBC pickle directory — each DBC is dumped as <stem>.pkl
_DBC_PKL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dumps", "dbc_pkl")
from can_sdk.data_object import SignalFilter
from cantools.database.can import Database
import traceback
from can_sdk.global_event import event_on_signal_select
from can_sdk.data_object import CANDBInfo, CANDBInfoType
        
STATE_DIR = r"config"
STATE_BINARY_PATH = r"config\state.pkl"
# region Class: CANDBInfoManager
class CANDBManager:
    def __init__(self):
        self._candb_dict: Dict[str, CANDBInfo] = {}
        self.mixed_candb = CANDBInfo(type=CANDBInfoType.MIXED)
        self._cur_sel_db = MIXED_DB_KEY
        self._candb_dict[self._cur_sel_db] = self.mixed_candb
        
        self.event_on_db_changed = ObservableEvent()
        self.event_on_db_list_changed = ObservableEvent()

        self._COMMON_STATE = [
            'mCDM',
            'current_dbc_file',
            ]
        
        self._cur_sig: Optional[SignalFilter] = None
        # self.event_on_signal_select = ObservableEvent(Optional[SignalFilter])

    def init_model(self):
        try:
            if self.load_common_state(): LOG.debug(f"Load data base done!")
        except Exception:
            LOG.exception(f"Load common state with exception")

    def exit_model(self):
        self.save_common_state()

    def load_common_state(self):
        if not os.path.exists(STATE_BINARY_PATH):
            LOG.warning(f"File config not existed {STATE_BINARY_PATH}, may be deleted")
            return False

        with open(STATE_BINARY_PATH, 'rb') as f:
            common_state = pickle.load(f)
        for key in self._COMMON_STATE:
            if key not in common_state:
                LOG.warning(f"Loaded state Failed, lost key {key}")
                return False
        try:
            """ TYPICAL BUG, THIS IS THE INSTANCE LOADED FROM DISK, NOT THE FIRST SINGLETION INSTANCE"""
            self._candb_dict = common_state['mCDM']._candb_dict
            self._normalize_candb_types()
            self.db_path_ogirin = common_state['mCDM'].db_path_ogirin
            if not self.db_path_ogirin:
                self.set_main_db_file(next(iter(self.candb_dict), None))
            LOG.info(f"Load State CANDB, CanDB dict: {len(self.candb_dict)}")
            LOG.info(f"Load State CANDB, db_path_ogirin: {self.db_path_ogirin}")
        except (AttributeError, KeyError, TypeError) as e:
            LOG.warning(f"{e}.No dbc stated -> skip")
        return True

    def trace_state(self, state):
        LOG.info("🔍 state trace:")
        for key, value in state.items():
            if isinstance(value, (list, dict)):
                LOG.info(f"{key}: (len={len(value)})")
            else:
                LOG.info(f"{key}: {value}")

    def save_common_state(self):
        folder = os.path.dirname(STATE_BINARY_PATH)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)
        try:
            common_state = {
                attr: getattr(self, attr, None)
                for attr in self._COMMON_STATE
            }
            self.trace_state(common_state)

            with open(STATE_BINARY_PATH, 'wb') as f:
                pickle.dump(common_state, f, protocol=pickle.HIGHEST_PROTOCOL)
            LOG.info(f"Saved common state done")
        except Exception as e:
            LOG.critical(f"Saved common state failed {e}")
        
    @property
    def messages_view(self):
        return self.candb.comboboxs

    @property
    def current_dbc_file(self):
        return self.candb.name
    
    @property
    def db_path_ogirin(self) -> Optional[str]:
        return self._cur_sel_db

    @db_path_ogirin.setter
    def db_path_ogirin(self, new_path: Optional[str]):
        if new_path == self._cur_sel_db:
            return
        self._cur_sel_db = new_path
        self.event_on_db_changed.notify()

    @property
    def candb_dict(self) -> Dict[str, CANDBInfo]:
        return self._candb_dict

    @property
    def rawvalue(self):
        return self.cur_sig.rawvalue

    @property
    def selected_signal_info(self):
        return self.cur_sig.signal_info

    @property
    def selected_message_info(self):
        return self.cur_sig.msg_info
    
    @property
    def cur_sig(self) -> Optional[SignalFilter]:
        return self._cur_sig

    @cur_sig.setter
    def cur_sig(self, value):
        if self._cur_sig == value:
            return
        self._cur_sig = value
        event_on_signal_select.notify(value)
    
    def get_signal_info_by_signal_name(self, signal_name, msg_info = None):
        if not msg_info:
            msg_info = self.selected_message_info

        if msg_info:
            for signal in msg_info.signals:
                if signal.name == signal_name:
                    return signal
        return None

    def get_signal_index(self, signal_name):
        if self.selected_message_info:
            for index, signal in enumerate(self.selected_message_info.signals):
                if signal.name == signal_name:
                    return index
        return -1  # Return -1 or raise an exception if not found
    
    def update_selected_signal_info(self, signal_name):
        sig = self.get_signal_info_by_signal_name(signal_name)
        if not sig: 
            return
        raw = self.cur_sig.rawvalue
        msg = self.cur_sig.msg_info
        self.cur_sig = SignalFilter(sig, msg, raw)

    def clear_model(self):
        # Reset to initial state: keep a fresh 'All' mixed DB and select it
        self._cur_sel_db = MIXED_DB_KEY
        self.mixed_candb = CANDBInfo(type=CANDBInfoType.MIXED)
        self._candb_dict = {MIXED_DB_KEY: self.mixed_candb}
        self.event_on_db_list_changed.notify()

    def _is_mixed_info(self, candb_info) -> bool:
        candb_type = getattr(candb_info, "type", None)
        if candb_type == CANDBInfoType.MIXED:
            return True
        if isinstance(candb_type, str) and candb_type.upper() == CANDBInfoType.MIXED.value:
            return True
        return False

    def _normalize_candb_types(self):
        if MIXED_DB_KEY in self._candb_dict:
            mixed_entry = self._candb_dict[MIXED_DB_KEY]
            mixed_entry.type = CANDBInfoType.MIXED

        for path, candb in self._candb_dict.items():
            if path == MIXED_DB_KEY:
                continue
            if not self._is_mixed_info(candb):
                candb.type = CANDBInfoType.NONE

    def _resolve_message_obj(self, msg_obj):
        """ The is more than a Message for this can_id so we could not decide which, so let app handle that"""
        if isinstance(msg_obj, list):
            if len(msg_obj) == 1:
                return msg_obj[0]
            return None
        return msg_obj

    def _iter_message_objs(self, messages_dict):
        for msg_obj in messages_dict.values():
            if isinstance(msg_obj, list):
                for msg in msg_obj:
                    yield msg
            else:
                yield msg_obj

    def is_mixed_selected(self) -> bool:
        if not self.candb:
            return False
        if self._cur_sel_db == MIXED_DB_KEY:
            return True
        return self._is_mixed_info(self.candb)

    def get_duplicated_messages(self) -> dict[int, list[cantools.database.can.Message]]:
        if not self.candb or not self.is_mixed_selected():
            return {}
        return self.candb.get_duplicated_messages()

    def get_duplicated_messages_view(self) -> list[str]:
        lines: list[str] = []
        for can_id, msg_list in sorted(self.get_duplicated_messages().items(), key=lambda item: item[0]):
            for msg in msg_list:
                lines.append(f"[{can_id:03X}] {msg.name}")
        return lines

    def get_mixed_messages_view(self) -> list[str]:
        """Return all messages from mixed database view (including duplicates)."""
        return self.mixed_candb.comboboxs

    def get_all_signals_view(self) -> list[str]:
        """Return a global signal list across all messages in current selected DB.

        Format per item:
            "[CAN_ID] MessageName - SignalName"
        """
        if not self.candb:
            return []

        self.candb._ensure_message_index()
        items: list[str] = []

        for frm_id, msg_obj in sorted(self.candb.messages.items(), key=lambda it: it[0]):
            msg_list = msg_obj if isinstance(msg_obj, list) else [msg_obj]
            for message in msg_list:
                for signal in message.signals:
                    items.append(f"[{frm_id:03X}] {message.name} - {signal.name}")

        return items

    def get_mixed_can_id_collisions(self) -> dict[int, list[tuple[str, str]]]:
        """Return CAN-ID collisions across loaded (non-mixed) DBC files.

        Mapping format: {can_id: [(file_name, message_name), ...]}.
        """
        if not self.is_mixed_selected():
            return {}

        can_id_map: dict[int, list[tuple[str, str]]] = {}

        for path, candb in self.candb_dict.items():
            if path == MIXED_DB_KEY or self._is_mixed_info(candb):
                continue

            file_name = basename(path)
            for msg in sorted(candb.db.messages, key=lambda m: m.frame_id):
                can_id_map.setdefault(msg.frame_id, []).append((file_name, msg.name))

        collisions: dict[int, list[tuple[str, str]]] = {}
        for can_id, entries in can_id_map.items():
            if len(entries) > 1:
                collisions[can_id] = entries

        return collisions
    
    def _clean_database(self, original_path, temp_file) -> str:
        """ remove unexceptable line in database file"""
        with open(original_path, 'rb') as src, open(temp_file, 'wb') as dst:
            for line in src:
                if b"CAT_DEF_" not in line:
                    dst.write(line)
        return abspath(temp_file)

    def load_database(self, file_path):
        res = self.read_from_db(file_path)
        self.db_path_ogirin = MIXED_DB_KEY
        return res

    def read_from_db(self, file_path):
        LOG.debug(f"read_from_db: {file_path}")
        if file_path in self.candb_dict:
            LOG.debug(f"Load base database existed")
            return []
        try:
            candb = CANDBInfo()
            # Clear database unexceptable keyword: CAT_DEF_,...
            temp_file = f"{DATABASE_TEMP}"
            temp_file = self._clean_database(file_path, temp_file)
            
            # Add to mix
            self.mixed_candb.db.add_dbc_file(temp_file)

            # Collect Database
            db = cantools.database.load_file(temp_file)
            candb.db = db

            schema_path = os.path.join(_DBC_PKL_DIR, "schema.json")
            self.export_schema(schema_path, list(db.messages))

            candb.file_path = file_path
            candb.backup_file_path = temp_file
            self._candb_dict[file_path] = candb
            self.event_on_db_list_changed.notify()
            LOG.info(f"Load base database, total: {len(candb.messages)} messages")
            LOG.info(f"Load base database, files: {self.get_list_all_db_filename()} messages")

            # Dump individual pkl right after parse so child processes
            # can unpickle directly without re-parsing the .dbc file.
            self._dump_candb_pkl(file_path, candb)
            self._dump_candb_pkl(MIXED_DB_KEY, self.mixed_candb)

            return candb.messages
        except Exception:
            LOG.critical("Unhandled exception:\n%s", traceback.format_exc())

    @property
    def candb(self) -> Optional[CANDBInfo]:
        # If current selection is missing or invalid, fall back to first available
        if self._cur_sel_db is None or self._cur_sel_db not in self._candb_dict:
            first = next(iter(self._candb_dict), None)
            if first is None:
                return None
            # set without notifying to avoid extra events on lazy access
            self._cur_sel_db = first
        return self._candb_dict[self._cur_sel_db]

    def add_database(self, file_path:str) -> List[str]:
        return self.read_from_db(file_path)
    
    def get_db(self) -> cantools.database.can.Database:
        return self.candb.db
    
    def get_message(self, can_id) -> Optional[cantools.database.can.Message]:
        if not self.candb:
            return None
        return self.candb.get_message(can_id)

    def get_message_by_id_and_name(self, can_id: int, message_name: str) -> Optional[cantools.database.can.Message]:
        if not self.candb:
            return None

        self.candb._ensure_message_index()
        msg_obj = self.candb._message_index.get(can_id)
        if msg_obj is None:
            return None
        if isinstance(msg_obj, list):
            for message in msg_obj:
                if message.name == message_name:
                    return message
            return msg_obj[0] if msg_obj else None

        return msg_obj

    def is_message_multiplexed(self, message_name):
        message = self.candb.db.get_message_by_name(message_name)
        has_mux_switch = False
        has_muxed_signals = False
        for signal in message.signals:
            if signal.is_multiplexer:
                has_mux_switch = True
            elif signal.multiplexer_signal is not None:
                has_muxed_signals = True
        if has_mux_switch or has_muxed_signals:
            return True  # Message uses multiplexing
        else:
            return False  # No multiplexing
        
    def get_message_name(self, can_id) -> str:
        if not self.candb:
            return ""
        return self.candb.get_message_name(can_id)
        
    def get_signal_index_by_id(self, can_id, signal_name: str) -> int:
        """
        Returns index of signal inside the CAN message identified by can_id.
        Raises KeyError if message or signal is not found.
        """
        message = self.get_message(can_id)
        if message is None:
            raise KeyError(f"CAN message with id {can_id} not found")

        for idx, sig in enumerate(message.signals):
            if sig.name == signal_name:
                return idx
        raise KeyError(f"Signal '{signal_name}' not found in message '{message.name}'")

    def get_signal_name_by_id_and_index(self, can_id, signal_index: int) -> str:
        """
        Returns signal name by CAN ID and signal index inside that message.
        Raises KeyError if message is not found.
        Raises IndexError if signal index is out of range.
        """
        message = self.get_message(can_id)
        if message is None:
            raise KeyError(f"CAN message with id {can_id} not found")

        if signal_index < 0 or signal_index >= len(message.signals):
            raise IndexError(
                f"Signal index {signal_index} out of range for message '{message.name}'"
            )

        return message.signals[signal_index].name

    def get_message_and_signal_info_by_signal_id(
        self,
        can_id: int,
        signal_id: int,
    ) -> Tuple[Optional[cantools.database.can.Message], Optional[cantools.database.can.Signal]]:
        """Resolve (message, signal) from decoder IDs.

        ``signal_id`` follows native decoder ordering: message-local signal index.
        Returns ``(None, None)`` when CAN-ID is missing/ambiguous or signal_id is out of range.
        """
        message = self.get_message(can_id)
        if message is None:
            return None, None

        signals = list(message.signals)
        if signal_id < 0:
            return message, None

        if signal_id < len(signals):
            return message, signals[signal_id]

        sorted_signals = sorted(signals, key=lambda s: s.name)
        if signal_id < len(sorted_signals):
            return message, sorted_signals[signal_id]

        return message, None
    
    def get_signal(self, can_id, signal_name) -> cantools.database.can.Signal:
        try:
            message = self.get_message(can_id)
            if not message:
                return None
            return message.get_signal_by_name(signal_name)
        except:
            return None
    
    def decode_value(self, can_id, data) -> Optional[dict]:
        try:
            message = self.get_message(can_id)
            if not message:
                return None
            sigs = message.decode(data, decode_choices=False, scaling=False)
            return sigs
        except Exception as e:
            LOG.critical(f"Process decode data is wrong, CHECK NOW!!!")
    
    def get_length_from_dlc(self, dlc:int) -> int:
        dlc_to_len = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
        return dlc_to_len[min(dlc, len(dlc_to_len) - 1)]

    def get_dlc_from_length(self, length: int) -> int:
        """
        Convert data length (bytes) → DLC.
        Length is rounded UP to the nearest valid CAN-FD size.
        """
        if length <= 0:
            return 0
        elif length <= 8:
            return length
        elif length <= 12:
            return 9
        elif length <= 16:
            return 10
        elif length <= 20:
            return 11
        elif length <= 24:
            return 12
        elif length <= 32:
            return 13
        elif length <= 48:
            return 14
        else:
            return 15

    def get_main_db_file(self) -> str:
        if self._cur_sel_db is None or self._cur_sel_db not in self._candb_dict:
            return None
        return self._cur_sel_db
    
    def get_main_db_filename(self) -> str:
        if self._cur_sel_db is None or self._cur_sel_db not in self._candb_dict:
            return None
        return basename(self._cur_sel_db)
    
    def get_list_all_db_file(self) -> list[str]:
        return [path for path in self.candb_dict.keys()]
    
    def get_list_all_db_filename(self) -> list[str]:
        return [basename(path) for path in self.candb_dict.keys()]
    
    def get_messages_name(self) -> list[str]:
        if self._cur_sel_db is None or self._cur_sel_db not in self._candb_dict:
            return []
        return [msg.name for msg in self._iter_message_objs(self.candb_dict[self._cur_sel_db].messages)]
    
    def get_messages(self) -> list[cantools.database.can.Message]:
        if self._cur_sel_db is None or self._cur_sel_db not in self._candb_dict:
            return []
        return [msg for msg in self._iter_message_objs(self.candb_dict[self._cur_sel_db].messages)]

    def export_schema(self, output_path: str, messages: Optional[list[cantools.database.can.Message]] = None):
        if messages is None:
            messages = self.get_messages()

        schema = {"messages": []}

        for msg in messages:
            message_schema = {
                "id": msg.frame_id,
                "name": msg.name,
                "dlc": msg.length,
                "signals": [],
            }

            for sig in msg.signals:
                message_schema["signals"].append({
                    "name": sig.name,
                    "start": sig.start,
                    "length": sig.length,
                    "scale": sig.scale,
                    "offset": sig.offset,
                    "signed": sig.is_signed,
                    "byte_order": sig.byte_order,
                })

            schema["messages"].append(message_schema)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2)

        LOG.info(f"Exported schema to {output_path}")
    
    def get_messages_len(self, path: str = None) -> int:
        if not path:
            path = self._cur_sel_db
        if path is None or path not in self._candb_dict:
            return 0
        return len(self.candb_dict[path].messages)
    
    def get_message_ids(self):
        if self._cur_sel_db is None or self._cur_sel_db not in self._candb_dict:
            return []
        return [msg.frame_id for msg in self._iter_message_objs(self.candb_dict[self._cur_sel_db].messages)]

    def set_main_db_file(self, path):
        self.db_path_ogirin = path
    
    # ── Per-DBC pickle persistence ──────────────────────────────

    @staticmethod
    def get_candb_pkl_path(db_file_path: str) -> str:
        """Return the pickle file path for a given DBC file.

        For ``MIXED_DB_KEY`` the stem is ``"mix"``; otherwise it is the
        DBC filename without extension.
        """
        if db_file_path == MIXED_DB_KEY:
            stem = "mix"
        else:
            stem = os.path.splitext(basename(db_file_path))[0]
        return os.path.join(_DBC_PKL_DIR, stem + ".pkl")

    @staticmethod
    def _dump_candb_pkl(db_file_path: str, candb_info: "CANDBInfo"):
        """Pickle *candb_info* to ``dbc_pkl/<stem>.pkl``."""
        pkl_path = CANDBManager.get_candb_pkl_path(db_file_path)
        os.makedirs(os.path.dirname(pkl_path), exist_ok=True)
        try:
            with open(pkl_path, "wb") as f:
                pickle.dump(candb_info, f, protocol=pickle.HIGHEST_PROTOCOL)
            LOG.info("Dumped DBC pkl: %s", pkl_path)
        except Exception:
            LOG.exception("Failed to dump DBC pkl: %s", pkl_path)

    @staticmethod
    def load_candb_pkl(db_file_path: str) -> Optional["CANDBInfo"]:
        """Load a previously-pickled ``CANDBInfo`` for *db_file_path*."""
        pkl_path = CANDBManager.get_candb_pkl_path(db_file_path)
        if not os.path.exists(pkl_path):
            LOG.warning("DBC pkl not found: %s", pkl_path)
            return None
        try:
            with open(pkl_path, "rb") as f:
                candb_info = pickle.load(f)
            LOG.info("Loaded DBC pkl: %s", pkl_path)
            return candb_info
        except Exception:
            LOG.exception("Failed to load DBC pkl: %s", pkl_path)
            return None

    def _backup_database(self) -> str:
        backup_file = f"{DATABASE_BACKUP}"
        if self.candb.db:
            cantools.database.dump_file(self.candb.db, backup_file)
        return abspath(backup_file)
# endregion 