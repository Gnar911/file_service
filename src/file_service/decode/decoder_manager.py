import multiprocessing as mp
import queue
import threading
import struct
import os
import pickle
from typing import Any, Optional, Dict, Sequence
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass
from ..repository.can_decode_repository import CANDecodeRepository
from ..repository.record_repository import CANLogRepository, MMAP_DIR
from lw.logger_setup import LOG
from lw.observer import ObservableEvent
from can_sdk.dbc_manager import CANDBInfo
from can_sdk.data_object import SignalFilter, CANLogDecodedDiskFile, CANLogRawDiskFile
from file_service.decode.native.can_decoder_api import (
    DecodeDB,
    RowIndexMmap, ValueMmap, RawValueMmap,
    CanDecoderLib, estimate_sample_count,
)
from file_service.parser.native.can_parser_api import MmapData, IndexMmapData

MMAP_DUMP_PATH = MMAP_DIR
_DBC_PKL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps", "dbc_pkl")



# ── TEST imports (only used by __main__ block) ────────────────────────────
try:
    from lw.logger_setup import setup_logger
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════════
#  Response Protocol  (child → parent via result_queue)
# ═══════════════════════════════════════════════════════════════════
class ResponseType(Enum):
    DECODE_START = auto()
    DECODE_COMPLETE = auto()
    DECODE_FILE_NOT_FOUND = auto()


@dataclass
class Response:
    rsp_type: ResponseType
    file_path: str = ""
    payload: Any = None


@dataclass
class DecodeStreamingData:
    file_path: str
    current_size: int = 0
    verified_size: int = 0
    mmap_file_count: int = 0
    is_loading: bool = False
    percent: int = 0


@dataclass
class DecodeSignalListData:
    file_path: str
    db_file_path: str
    signal_list: list[tuple[int, int]]


# ═══════════════════════════════════════════════════════════════════
#  decode_process — child-process entry point (linear, no cmd queue)
#
#  Each invocation:
#    1. Load DBC from its pre-pickled .pkl
#    2. Decode exactly one log file
#    3. Exit
#  The scheduler kills & re-spawns whenever the task changes.
# ═══════════════════════════════════════════════════════════════════

class MessageDecoder:
    """
    Schedules CAN signal decoding across DBC switches and log file loads.

    Decode outputs are stored in per-DBC subfolders under ``mmap/``:
    ``mmap/<db_stem>/``, ``mmap/mix/``, etc.  This allows instant re-use
    when the user switches back to a previously-used DBC.

    Invariants
    ----------
    1. A file is decoded at most once per DBC (decode mmaps on disk → skip).
    2. At most one decode runs at any time (single worker process).
    3. Switching file while a decode is in-flight discards the old decode,
       cleans up its partial output, and starts a fresh decode for the new file.
    4. Switching DBC checks if a cached decode exists for the new DBC first;
       only triggers a new decode if no cache is found.
    """

    def __init__(
        self,
        log_repository: CANLogRepository | None = None,
        decode_repository: CANDecodeRepository | None = None,
    ):
        self._started = False

        self._log_repository = log_repository or CANLogRepository()
        self._decode_repository = decode_repository or CANDecodeRepository(self._log_repository)
        self._runtime = _DecodeWorkerRuntime()

        # --- Data ----#
        self.data = self._decode_repository.data

        # ── Decode scheduling state (protected by _lock) ──────
        self._pending_jobs: list[tuple[str, str]] = []
        self._active_batch_jobs: list[tuple[str, str]] = []
        self._decoding_file: Optional[str] = None
        self._decoding_db_file: Optional[str] = None
        self._lock = threading.Lock()

        # ── Observable events for the application layer ───────
        self.event_on_decode_complete = ObservableEvent(str)
        self.event_on_decode_start = ObservableEvent(str)
        self.event_on_decode_file_not_found = ObservableEvent(str)
        self.event_on_decode_progress = ObservableEvent(DecodeStreamingData)
        self.event_on_new_signal_come = ObservableEvent(DecodeSignalListData)

        # ── Progress cache for UI throttling ───────────────────
        self._last_progress: dict[str, tuple[int, int, int, bool, int]] = {}
        self._decode_expected_samples: dict[str, int] = {}
        self._known_decode_signals: dict[tuple[str, str], set[tuple[int, int]]] = {}

    def _decode_mmaps_exist(self, file_path: str, db_file_path: str) -> bool:
        return self._decode_repository.decode_mmaps_exist(file_path, db_file_path)

    def remove_decode_folder(self, db_file_path: str) -> None:
        self._decode_repository.remove_decode_folder(db_file_path)

    def remove_log_decode_mmaps(self, file_path: str) -> None:
        self._decode_repository.remove_log_decode_mmaps(file_path)

    def start(self) -> None:
        if self._started:
            return
        self._started = True

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("MessageDecoder must be started before scheduling decode work")

    @property
    def worker_status_queue(self) -> mp.Queue:
        return self._runtime.result_queue

    def emit_decode_progress(self) -> None:
        self._emit_decode_progress()

    def _ensure_decoded_store(self, file_path: str, db_file_path: str) -> Optional[CANLogDecodedDiskFile]:
        return self._decode_repository.ensure_decoded_store(file_path, db_file_path)

    def _read_decode_progress(self, file_path: str, db_file_path: str) -> Optional[DecodeStreamingData]:
        expected_samples = max(0, int(self._decode_expected_samples.get(file_path, 0)))
        snapshot = self._decode_repository.read_decode_progress(
            file_path,
            db_file_path,
            expected_samples=expected_samples,
        )
        if snapshot is None:
            return None

        return DecodeStreamingData(
            file_path=file_path,
            current_size=int(snapshot.current_size),
            verified_size=int(snapshot.verified_size),
            mmap_file_count=int(snapshot.mmap_file_count),
            is_loading=bool(snapshot.is_loading),
            percent=int(snapshot.percent),
        )

    def _emit_decode_progress(self):
        with self._lock:
            file_path = self._decoding_file
            db_file_path = self._decoding_db_file

        if not file_path or not db_file_path:
            return

        self._sync_decode_state_to_store(file_path=file_path, db_file_path=db_file_path)

        self._update_decode_signal_list(file_path=file_path, db_file_path=db_file_path)

        data = self._read_decode_progress(file_path, db_file_path)
        if data is None:
            return

        snap = (data.current_size, data.verified_size, data.mmap_file_count, data.is_loading, data.percent)
        if self._last_progress.get(file_path) == snap:
            return

        self._last_progress[file_path] = snap
        decoded = self._ensure_decoded_store(file_path=file_path, db_file_path=db_file_path)
        decoded.decode_current_size = int(data.current_size)
        decoded.decode_verified_size = int(data.verified_size)
        decoded.decode_mmap_file_count = int(data.mmap_file_count)
        decoded.decode_percent = int(data.percent)
        decoded.decode_is_loading = bool(data.is_loading)
        self.event_on_decode_progress.notify(data)

    def _sync_decode_state_to_store(self, file_path: str, db_file_path: str):
        self._decode_repository.sync_decode_state_to_store(file_path, db_file_path)

    def _update_decode_signal_list(self, file_path: str, db_file_path: str):
        decoded = self._ensure_decoded_store(file_path=file_path, db_file_path=db_file_path)
        if decoded is None:
            return
        cache_key = (file_path, db_file_path)
        sorted_pairs = self._decode_repository.read_signal_pairs(file_path, db_file_path)
        prev_pairs = self._known_decode_signals.get(cache_key, set())
        signal_pairs = set(sorted_pairs)

        if not sorted_pairs:
            if prev_pairs:
                self._known_decode_signals[cache_key] = set()
                self.event_on_new_signal_come.notify(
                    DecodeSignalListData(
                        file_path=file_path,
                        db_file_path=db_file_path,
                        signal_list=[],
                    )
                )
            return

        if signal_pairs != prev_pairs:
            self._known_decode_signals[cache_key] = set(signal_pairs)
            self.event_on_new_signal_come.notify(
                DecodeSignalListData(
                    file_path=file_path,
                    db_file_path=db_file_path,
                    signal_list=sorted_pairs,
                )
            )

    def _reset_active_decode_state(self, remove_partial_decode: bool) -> None:
        if self._decoding_file and self._decoding_db_file:
            if remove_partial_decode:
                self._decode_repository.remove_decode_mmaps(self._decoding_file, self._decoding_db_file)
            self._last_progress.pop(self._decoding_file, None)
            self._decode_expected_samples.pop(self._decoding_file, None)
            self._known_decode_signals.pop((self._decoding_file, self._decoding_db_file), None)
        self._decoding_file = None
        self._decoding_db_file = None
        self._active_batch_jobs = []

    # ═════════════════════════════════════════════════════════
    #  Scheduling  (caller MUST hold self._lock)
    # ═════════════════════════════════════════════════════════

    def _normalize_jobs(self, decode_jobs: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
        normalized: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for db_file_path, file_path in decode_jobs:
            if not db_file_path or not file_path:
                continue
            key = (str(db_file_path), str(file_path))
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized

    def _is_busy(self) -> bool:
        return self._runtime.is_busy()

    def _maybe_spawn_pending_locked(self):
        if self._is_busy():
            return

        if not self._pending_jobs:
            return

        runnable: list[tuple[str, str]] = []
        for db_file_path, file_path in self._pending_jobs:
            if self._decode_mmaps_exist(file_path, db_file_path):
                LOG.debug("schedule: decode already exists for %s — skip", Path(file_path).name)
                self._sync_decode_state_to_store(file_path=file_path, db_file_path=db_file_path)
                self._update_decode_signal_list(file_path=file_path, db_file_path=db_file_path)
                self.event_on_decode_complete.notify(file_path)
            else:
                runnable.append((db_file_path, file_path))

        self._pending_jobs = []
        if not runnable:
            return

        self._active_batch_jobs = list(runnable)
        self._decoding_file = None
        self._decoding_db_file = None
        self._runtime.spawn_decode(runnable)
        LOG.info("Spawned decode batch: jobs=%d", len(runnable))

    def _enqueue_jobs_locked(self, decode_jobs: Sequence[tuple[str, str]], replace: bool):
        jobs = self._normalize_jobs(decode_jobs)
        if not jobs:
            return

        if replace:
            self._pending_jobs = []

        in_flight = set(self._active_batch_jobs)
        if self._decoding_db_file and self._decoding_file:
            in_flight.add((self._decoding_db_file, self._decoding_file))

        for job in jobs:
            if job in in_flight:
                continue
            if job in self._pending_jobs:
                continue
            self._pending_jobs.append(job)

        self._maybe_spawn_pending_locked()

    # ═════════════════════════════════════════════════════════
    #  Command API  (public, for manual / external use)
    # ═════════════════════════════════════════════════════════

    def cmd_set_decode_jobs(self, decode_jobs: Sequence[tuple[str, str]]):
        """Replace queue with mandatory (db_file_path, file_path) pairs and decode sequentially."""
        LOG.debug("cmd_set_decode_jobs")
        self._require_started()
        with self._lock:
            self._runtime.kill()
            self._reset_active_decode_state(remove_partial_decode=True)
            self._enqueue_jobs_locked(decode_jobs, replace=True)

    def cmd_submit_decode_jobs(self, decode_jobs: Sequence[tuple[str, str]]):
        """Append mandatory (db_file_path, file_path) pairs to queue."""
        LOG.debug("cmd_submit_decode_jobs")
        self._require_started()
        with self._lock:
            self._enqueue_jobs_locked(decode_jobs, replace=False)

    def cmd_decode(self, db_file_path: str, file_path: str):
        """Submit one mandatory decode pair (db_file_path, file_path)."""
        LOG.debug("cmd_decode")
        if not db_file_path or not file_path:
            LOG.warning("cmd_decode skipped: invalid db or file path")
            return
        self.cmd_submit_decode_jobs([(db_file_path, file_path)])

    def cmd_manual_decode(self, db_file_path: str, file_path: str):
        """
        Manually trigger decode for an explicit (DBC, log file) pair.

        This method updates the active decode DB and current file path,
        discards any in-flight decode if needed, then schedules decode.
        """
        LOG.debug("cmd_manual_decode")

        if not db_file_path or not file_path:
            LOG.warning("cmd_manual_decode skipped: invalid db or file path")
            return

        self.cmd_decode(db_file_path, file_path)

    def cmd_discard_decode(self):
        """Discard any in-flight decode immediately."""
        LOG.info("cmd_discard_decode")
        self._require_started()
        with self._lock:
            self._runtime.kill()
            self._reset_active_decode_state(remove_partial_decode=True)
            self._pending_jobs = []

    def stop(self, timeout: float = 2.0):
        if not self._started:
            return
        with self._lock:
            self._runtime.kill()
            self._reset_active_decode_state(remove_partial_decode=True)
            self._pending_jobs = []
        self._started = False