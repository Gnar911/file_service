import os
import os.path
import logging
import re
import struct
import mmap as _mmap
import sys
import time
import functools
import threading
import json
import csv
from collections import deque
from os.path import basename, isfile
from collections import defaultdict
from multiprocessing import Process
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
from pathlib import Path
from datetime import datetime
import pandas as pd
from can import ASCReader, LogReader, BLFReader
from can.io import Logger
from lw.logger_setup import setup_logger, LOG
from .parser.parser import PAGE_SIZE, LogParser
from can_sdk.data_object import Signal, Message, CANLogLine, DataLogState, CANLogRawDiskFile
from lw.observer import ObservableEvent

LOG = logging.getLogger()
# CONSTS
DB = 'Database'
BASE_DIR = Path(__file__).resolve().parent
SignalName = str
RawValue = int
value = str
DUMP_PATH = BASE_DIR / "dumps"
MMAP_DIR  = DUMP_PATH / "mmap"

# ── mmap header constants ─────────────────────────────────────────────────────
DATA_STATUS_RUNNING = 0
DATA_STATUS_DONE    = 1
DATA_STATUS_ERROR   = 2

@dataclass
class CANInfo:
    message: Message
    signals: Dict[str, Signal] = field(default_factory=dict)


# ── Worker function — runs in a child Process ─────────────────────────────────
def worker_process(file_path: str, data_path: str, index_path: str):
    """Child process: detect format + C++ 2-pass parse → mmap.
    Exit code 0 = success, 1 = verification/parse failed."""
    canlf = CANLogRawDiskFile(file_path = file_path)
    parser_instance = LogParser()
    ok = parser_instance._parse_from_file(
        canlf,
        data_path=data_path,
        index_path=index_path,
    )
    if not ok:
        sys.exit(1)
    # Process exits with code 0; the mmap files stay on disk.
    
class CanLogEvent(Enum):
    EVENT_ON_CONTEXT_LOG_READ_ABORTED = 3
    EVENT_ON_CONTEXT_LOG_PICKLE_COMPLETE = 4
    EVENT_ON_RESTORE_FILE_DONE = 7

@dataclass
class CanLogStreamingData:
    file_path: str
    file_paths: List[str] = field(default_factory=list)
    current_size: int = 0
    verified_size: int = 0
    verified_file_num: int = 0

""" CANLog Model """
class CANLogManager:
    def __init__(self):
        self.data: Dict[str, CANLogRawDiskFile] = {}
        self._active_proc: Process | None = None
        self._active_file: str | None = None
        self._pending_files: deque[str] = deque()
        self._stop_event: threading.Event = threading.Event()
        self.data_lock = threading.Lock()
        self.parser = LogParser()
        self.event_on_property_changed = ObservableEvent()

    @staticmethod
    def _channel_index_path_from_index(index_path: str) -> str:
        if not index_path:
            return ""
        base = index_path[:-5] if index_path.endswith(".mmap") else index_path
        return base + ".channel.mmap"

    @staticmethod
    def _new_raw_disk_file(file_path: str, data_path: str, index_path: str) -> CANLogRawDiskFile:
        canlf = CANLogRawDiskFile(file_path = file_path)
        canlf.data_mmap_path = data_path
        canlf.index_mmap_path = index_path
        canlf.channel_index_mmap_path = CANLogManager._channel_index_path_from_index(index_path)
        canlf.state = DataLogState.UNAVAILABLE
        canlf.is_loading = True
        canlf.can_ids = []
        return canlf

    def _start_worker_locked(self, file_path: str):
        data_path, index_path = self._mmap_paths(file_path)

        self._stop_event.clear()
        proc = Process(
            target=worker_process,
            args=(file_path, data_path, index_path),
            daemon=True,
        )
        self._active_proc = proc
        self._active_file = file_path
        proc.start()

        poll_t = threading.Thread(
            target=self._poll_mmap_progress,
            args=(file_path, data_path, self._stop_event),
            daemon=True,
        )
        poll_t.start()

        watcher_t = threading.Thread(
            target=self._watch_process,
            args=(file_path, proc, self._stop_event, data_path, index_path),
            daemon=True,
        )
        watcher_t.start()

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _mmap_paths(file_path: str):
        """Return (data_mmap_path, index_mmap_path) for a given log file."""
        MMAP_DIR.mkdir(parents=True, exist_ok=True)
        name = Path(file_path).name
        return (str(MMAP_DIR / (name + ".data.mmap")),
                str(MMAP_DIR / (name + ".index.mmap")))

    # ══════════════════════════════════════════════════════════════════════
    #  1. Verify + Parse — validate file, spawn C++ 2-pass parse process.
    #     Slot in self.data is created only on success (in _on_task_done).
    # ══════════════════════════════════════════════════════════════════════
    def start_log_verification(self, file_path: str) -> bool:
        if not isfile(file_path):
            LOG.info(f"Input file path is invalid: {file_path}")
            return False

        with self.data_lock:
            if file_path in self.data:
                LOG.info(f"Already loaded in memory cache: {file_path}")
                return True

            if self._active_proc is not None:
                if file_path == self._active_file or file_path in self._pending_files:
                    LOG.info(f"Already scheduled: {file_path}")
                    return True
                self._pending_files.append(file_path)
                LOG.info(f"Queued for verification: {file_path} (pending={len(self._pending_files)})")
                return True

            self._start_worker_locked(file_path)
        return True

    def _poll_mmap_progress(self, file_path: str, data_path: str,
                            stop_event: threading.Event,
                            interval: float = 0.5):
        """Background thread: track segmented mmap creation and runtime progress."""
        base = Path(data_path)
        stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
        parent = base.parent
        last_page_fire = 0
        try:
            while not stop_event.is_set():
                data_segs = sorted(parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))

                with self.data_lock:
                    if file_path not in self.data and data_segs:
                        _, index_path = self._mmap_paths(file_path)
                        canlf = self._new_raw_disk_file(file_path, data_path, index_path)
                        self.data[file_path] = canlf

                    canlf = self.data.get(file_path)
                    if canlf is not None:
                        canlf.refresh_mmap_runtime()
                        canlf.refresh_can_ids_runtime()
                        current_lines = canlf.total_lines
                    else:
                        current_lines = 0

                if current_lines // PAGE_SIZE > last_page_fire // PAGE_SIZE:
                    last_page_fire = current_lines
                    sd = CanLogStreamingData(file_path=file_path)
                    sd.current_size = current_lines
                    self.event_on_property_changed.notify("page", sd)

                time.sleep(interval)
        except Exception as e:
            LOG.debug(f"poll_mmap_progress ended: {e}")

    def _watch_process(self, file_path: str, proc: Process,
                       stop_event: threading.Event,
                       data_path: str, index_path: str):
        """Wait for the child process to exit, then fire _on_task_done."""
        proc.join()
        stop_event.set()
        success = proc.exitcode == 0
        self._on_task_done(file_path, success, data_path, index_path)

    def _on_task_done(self, file_path: str, success: bool,
                      data_path: str, index_path: str):
        next_file: str | None = None
        with self.data_lock:
            self._active_proc = None
            self._active_file = None
            if self._pending_files:
                next_file = self._pending_files.popleft()

        sd = CanLogStreamingData(file_path=file_path)
        if success:
            with self.data_lock:
                canlf = self.data.get(file_path)
                if canlf is None:
                    canlf = self._new_raw_disk_file(file_path, data_path, index_path)
                    self.data[file_path] = canlf
                canlf.refresh_mmap_runtime()
                canlf.refresh_can_ids_runtime()
                canlf.verified_size = canlf.total_lines
                canlf.state = DataLogState.AVAILABLE
                canlf.is_loading = False

            LOG.debug(f"_on_task_done successful: {file_path}  total={canlf.total_lines}")
            self.event_on_property_changed.notify("verify", sd)
        else:
            with self.data_lock:
                canlf = self.data.get(file_path)
                if canlf is not None:
                    canlf.is_loading = False
            LOG.debug(f"_on_task_done failed: {file_path}")
            self.event_on_property_changed.notify("verify", None)

        if next_file is not None:
            if isfile(next_file):
                LOG.info(f"Start queued verification: {next_file}")
                with self.data_lock:
                    self._start_worker_locked(next_file)
            else:
                LOG.warning(f"Skip queued file (missing): {next_file}")


    # ══════════════════════════════════════════════════════════════════════
    #  3. Stop / cancel
    # ══════════════════════════════════════════════════════════════════════
    def request_stop_parse_log_async(self, file_path: str):
        with self.data_lock:
            proc = self._active_proc
            active_file = self._active_file
            if proc is not None and active_file == file_path:
                self._stop_event.set()
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=3)
                return

            if file_path in self._pending_files:
                try:
                    self._pending_files.remove(file_path)
                    LOG.info(f"Removed queued file: {file_path}")
                except ValueError:
                    pass
                return

        LOG.error("Worker not found")

    # ══════════════════════════════════════════════════════════════════════
    #  6. Decorators & query methods (unchanged)
    # ══════════════════════════════════════════════════════════════════════
    def file_name_exist(method):
        @functools.wraps(method)
        def wrapper(self, file_name, *args, **kwargs):
            if file_name not in self.data:
                LOG.debug(f"{file_name} not in self.data: {self.data.keys()}")
                return []
            result = method(self, file_name, *args, **kwargs)
            return result
        return wrapper

    def file_path_exist(method):
        @functools.wraps(method)
        def wrapper(self, file_path, *args, **kwargs):
            if file_path not in self.data:
                LOG.debug(f"{file_path} not in self.data: {self.data.keys()}")
                return []
            result = method(self, file_path, *args, **kwargs)
            return result
        return wrapper

    def write_log_csv(self, filepath, lines: list[CANLogLine], save_filepath: str = None):
        if not save_filepath:
            save_filepath = filepath + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
        with open(save_filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            msg_filt = self.data[filepath].group_messages_by_can_id(lines)

            for can_id, msg_lines in msg_filt.items():
                # ---- Message header ----
                writer.writerow([
                    "Time",
                    "Channel",
                    "CAN ID",
                    "Message Name",
                    "Direction",
                    "DLC",
                    "Data",
                ])

                # ---- Signal header (from first message) ----
                sig_names = msg_lines[0].get_list_signal_name_fromline()
                writer.writerow(sig_names)

                # ---- Message + signal rows ----
                for l in msg_lines:
                    # message row
                    writer.writerow([
                        f"{l.timestamp:.6f}",
                        l.channel,
                        f"0x{l.can_id:X}",
                        l.message_name or "",
                        l.direction,
                        l.data_len,
                        l.raw_data,
                    ])

                    # signal row (aligned with sig_names)
                    writer.writerow([
                        str(l.message_obj.signals[sig].raw_value)
                        if sig in l.message_obj.signals else ""
                        for sig in sig_names
                    ])

                # ---- Empty row between CAN ID groups ----
                writer.writerow([])
                writer.writerow([])
                writer.writerow([])
                writer.writerow([])
                writer.writerow([])

    def write_log_filterd_by_time(self):
        pass

    def write_log_filterd_by_msg(self):
        pass

    def write_log_filterd_by_signal(self):
        pass

    def _delete_mmap_family(self, mmap_path: str) -> int:
        target = Path(mmap_path)
        parent = target.parent
        stem = target.name[:-5] if target.name.endswith(".mmap") else target.name

        removed = 0
        candidates = [target] + list(parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))
        for path in candidates:
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except Exception as e:
                LOG.debug(f"Failed to delete mmap file {path}: {e}")
        return removed

    def delete_log_mmap(self, file_path: str) -> int:
        data_path, index_path = self._mmap_paths(file_path)
        removed = self._delete_mmap_family(data_path)
        removed += self._delete_mmap_family(index_path)
        removed += self._delete_mmap_family(self._channel_index_path_from_index(index_path))
        return removed

    @file_name_exist
    def delete_log_file(self, file_name):
        if file_name in self.data:
            del self.data[file_name]
        else:
            LOG.debug(f"{file_name} not existed in self.data: {self.data.keys()}")

    def delete_all_log_file(self):
        for file_path in list(self.data.keys()):
            self.delete_log_mmap(file_path)
        self.data.clear()
    
    @file_name_exist
    def get_logfile_data(self, file_name: str) -> Optional[CANLogRawDiskFile]:
        return self.data[file_name]
    
    @file_name_exist
    def get_all_log_data(self, file_name: str) -> List[CANLogLine]:
        return list(self.data[file_name].log_entries.values())

    @file_name_exist
    def get_timestamps_of_target_log_line(self, file_name: str, target_log_lines: List[CANLogLine]) -> List[float]:
        if len(target_log_lines) == 0:
            return []
        return self.data[file_name].get_timestamps_of_target_log_line(target_log_lines)

    @file_name_exist
    def get_messages_by_timestamp_range(
        self, 
        file_name: str,         
        from_t: float, 
        to_t: float, 
        search_region: List[CANLogLine] = None):
        return self.data[file_name].get_messages_by_timestamp_range(from_t, to_t, search_region)

    @file_name_exist
    def get_log_data_by_channel(self, file_name: str, ch: str, line_search:List[CANLogLine] = None) -> List[CANLogLine]:
        if not line_search:
            return self.get_log_data_by_channel(file_name, ch, self.data[file_name])

        return self.data[file_name].get_messages_by_channel(ch, line_search)

    def get_current_logfile_list(self) -> List:
        return list(self.data.keys())

    def format_log_entry(self, msg, time_base) -> str:
        timestamp = f"{(msg.timestamp-time_base):.6f}".ljust(12)
        cantype = ("CANFD" if msg.is_fd else "CAN").ljust(8)
        channel = "1" if msg.channel == 0 else "2"
        direction = ("Rx" if msg.is_rx else "Tx").ljust(3)
        can_id_str = f"{msg.arbitration_id:X}".rjust(10)
        try: 
            if self.db != None:
                message = self.db.get_message_by_frame_id(msg.arbitration_id)
                message_name = message.name
            else:
                message_name = "UNKNOW"
        except:
            message_name = "NOT_FOUND"
        message_name = message_name.ljust(40)
        dlc = str(msg.dlc).ljust(3)
        raw_data_bytes = ' '.join(f"{byte:02X}" for byte in msg.data).upper()
        return f"{timestamp} {cantype} {channel} {direction} {can_id_str}    {message_name} {dlc} {raw_data_bytes}\n"

    def get_output_filename(self, output_dir, index):
        base = os.path.splitext(os.path.basename(self.file_input))[0]
        return os.path.join(output_dir, f"{base}_part{index}.asc")

    def convert_blf_file(self):
        output_dir = os.path.join(os.path.dirname(self.file_input), "output")
        os.makedirs(output_dir, exist_ok=True)

        size_limit_bytes = 9999 * 1024 * 1024 # Maximum size

        writer = None
        file_index = 1
        file_size = 0
        time_base = 0.0
        percent = 0
        last_percent = -1
        self.calculating_total_msgs = True
        with LogReader(self.file_input) as log_reader:
            LOG.info("Load log complete")
            output_path = self.get_output_filename(output_dir, file_index)
            writer = open(output_path, "w", encoding="utf-8")
            LOG.info("Start create log")
            for i, msg in enumerate(log_reader):
                if time_base == 0.0:
                    time_base = msg.timestamp
                line = self.format_log_entry(msg, time_base)
                writer.write(line)
                file_size += len(line.encode())
                if file_size >= size_limit_bytes:
                    writer.close()
                    file_index += 1
                    file_size = 0
                    output_path = self.get_output_filename(output_dir, file_index)
                    writer = open(output_path, "w", encoding="utf-8") 
                
                if last_percent == -1 and not self.calculating_total_msgs:
                    def update_ui():
                        self.progress.stop()
                        self.progress.config(mode="determinate", maximum=100, value=0)
                    self.root.after(0, update_ui)
                if not self.calculating_total_msgs and self.total_msgs > 0:
                    percent = int((i/self.total_msgs) * 100)
                    if percent != last_percent:
                        last_percent = percent
                        def update_ui():
                            self.progress["value"] = percent
                        self.root.after(0, update_ui)
            if writer:
                writer.close()
        return output_path

    def _cal_signal_all_messages(self, file_path):
        return # Performance optimization - skip cal all
        return
        
if __name__ == "__main__":
    # setup logger
    from can_sdk.test_ultility import TEST_set_up_1_basic_context
    setup_logger(env="DEV", backup_count=30)
    #filelog = "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x4000.asc"
    FILELOG = "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x10.asc"
    manager = CANLogManager()

    ################################## TEST PARSE A LOG FILE ###############################
    # done = threading.Event()
    # result_ok = {"value": False}

    # def on_manager_event(event_type, data: CanLogStreamingData = None):
    #     if event_type == "page" and data is not None:
    #         LOG.info(f"[CANLogManager][page] current_size={data.current_size}")
    #         return

    #     if event_type == "verify":
    #         if data is None:
    #             LOG.error("[CANLogManager][verify] failed")
    #             result_ok["value"] = False
    #             done.set()
    #             return

    #         with manager.data_lock:
    #             canlf = manager.data.get(data.file_path)
    #         if not isinstance(canlf, CANLogRawDiskFile):
    #             LOG.error("[CANLogManager][verify] done but CANLogRawDiskFile missing")
    #             result_ok["value"] = False
    #         else:
    #             LOG.info(
    #                 f"[CANLogManager][verify] success file={canlf.file_name} "
    #                 f"total_lines={canlf.total_lines} verified_size={canlf.verified_size} "
    #                 f"mmap_files={canlf.mmap_file_count} can_ids={len(canlf.can_ids)}"
    #             )
    #             result_ok["value"] = True
    #         done.set()

    # manager.event_on_property_changed.subscribe(on_manager_event)

    # LOG.info(f"[CANLogManager][test] start verify: {filelog}")
    # started = manager.start_log_verification(filelog)
    # if not started:
    #     LOG.error("[CANLogManager][test] start_log_verification() returned False")
    #     sys.exit(1)

    # while not done.wait(0.5):
    #     with manager.data_lock:
    #         canlf = manager.data.get(filelog)
    #     if isinstance(canlf, CANLogRawDiskFile):
    #         is_done = (not canlf.is_loading) and (canlf.state == DataLogState.AVAILABLE)
    #         LOG.info(
    #             f"[CANLogManager][wait] loading={canlf.is_loading} "
    #             f"done={is_done} total_lines={canlf.total_lines} can_ids={len(canlf.can_ids)}"
    #         )

    ctx = TEST_set_up_1_basic_context(FILELOG=FILELOG)
    # TEST: fetch row indices and timestamps by one CAN ID, then log list lengths
    raw_filelog = getattr(ctx, "d_filelog", None)
    if not isinstance(raw_filelog, CANLogRawDiskFile):
        LOG.error("[TEST] d_filelog is unavailable in context")
    else:
        if not raw_filelog.can_ids:
            raw_filelog.refresh_can_ids_runtime()

        if not raw_filelog.can_ids:
            LOG.warning("[TEST] No CAN IDs found in parsed mmap for %s", FILELOG)
        else:
            target_can_id = int(raw_filelog.can_ids[0])
            row_indices = raw_filelog.get_row_indices_by_list_id([target_can_id])
            timestamps = raw_filelog.get_timestamps_by_can_id(target_can_id)
            LOG.info(
                "[TEST] can_id=0x%X row_index_len=%d timestamp_len=%d",
                target_can_id,
                len(row_indices),
                len(timestamps),
            )







    ################################## TEST PARSE MANY LOG FILES ###############################

    sys.exit(0)






