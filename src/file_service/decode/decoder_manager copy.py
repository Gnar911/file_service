import multiprocessing as mp
import queue
import shutil
import threading
import struct
import os
import pickle
from typing import Any, Optional, Dict, Sequence
from pathlib import Path
from enum import Enum, auto
from dataclasses import dataclass
from ..repository.record_repository import MMAP_DIR
from lw.logger_setup import LOG
from lw.observer import ObservableEvent
from can_sdk.dbc_manager import CANDBInfo, MIXED_DB_KEY
from can_sdk.data_object import SignalFilter, CANLogDecodedDiskFile, CANLogRawDiskFile
from native_sdk.can_decoder_api import (
    DecodeDB, SignalDirMmap,
    RowIndexMmap, ValueMmap, RawValueMmap,
    CanDecoderLib, estimate_sample_count,
)
from native_sdk.can_parser_api import MmapData, IndexMmapData

MMAP_DUMP_PATH = MMAP_DIR
_DBC_PKL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps", "dbc_pkl")


def _segment_paths(base_path: str) -> list[Path]:
    base = Path(base_path)
    if base.exists():
        return [base]
    stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
    return sorted(base.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))


def _get_candb_pkl_path(db_file_path: str) -> str:
    if db_file_path == MIXED_DB_KEY:
        stem = "mix"
    else:
        stem = os.path.splitext(os.path.basename(db_file_path))[0]
    return os.path.join(_DBC_PKL_DIR, stem + ".pkl")


def _load_candb_pkl(db_file_path: str) -> Optional[CANDBInfo]:
    pkl_path = _get_candb_pkl_path(db_file_path)
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

def _decode_one_file(
    result_queue: mp.Queue,
    decode_db: DecodeDB,
    db_file_path: str,
    file_path: str,
    decode_dir: str,
):
    decode_dir_p = Path(decode_dir)
    decode_dir_p.mkdir(parents=True, exist_ok=True)

    base = Path(file_path).name
    data_path = str(MMAP_DUMP_PATH / (base + ".data.mmap"))
    index_path = str(MMAP_DUMP_PATH / (base + ".index.mmap"))
    sig_dir_path = str(decode_dir_p / (base + ".signal_dir.mmap"))
    row_index_changed_path = str(decode_dir_p / (base + ".row_index_changed.mmap"))
    row_index_path = str(decode_dir_p / (base + ".row_index.mmap"))
    value_path = str(decode_dir_p / (base + ".value.mmap"))
    rawvalue_path = str(decode_dir_p / (base + ".rawvalue.mmap"))

    data_segments = _segment_paths(data_path)
    if not data_segments:
        LOG.warning("data.mmap not found: %s", data_path)
        result_queue.put(
            Response(
                rsp_type=ResponseType.DECODE_FILE_NOT_FOUND,
                file_path=file_path,
                payload={"db_file_path": db_file_path},
            )
        )
        return

    try:
        index_segments = _segment_paths(index_path)
        if index_segments:
            n_samples = 0
            for seg in index_segments:
                index_mm = IndexMmapData(str(seg))
                _, s = estimate_sample_count(index_mm, decode_db)
                index_mm.close()
                n_samples += s
        else:
            data_mm_tmp = MmapData(str(data_segments[0]))
            total_rows = 0
            for seg in data_segments:
                with open(seg, "rb") as f:
                    hdr = f.read(8)
                    if len(hdr) == 8:
                        total_rows += int(struct.unpack("<Q", hdr)[0])
            n_samples = total_rows * 20
            data_mm_tmp.close()
    except Exception:
        LOG.exception("Failed to estimate output sizes")
        result_queue.put(
            Response(
                rsp_type=ResponseType.DECODE_COMPLETE,
                file_path=file_path,
                payload={"db_file_path": db_file_path},
            )
        )
        return

    if n_samples == 0:
        total_rows = 0
        for seg in data_segments:
            try:
                with open(seg, "rb") as f:
                    hdr = f.read(8)
                    if len(hdr) == 8:
                        total_rows += int(struct.unpack("<Q", hdr)[0])
            except OSError:
                continue

        if total_rows == 0:
            LOG.info("No samples to decode: data mmap contains 0 rows")
            result_queue.put(
                Response(
                    rsp_type=ResponseType.DECODE_COMPLETE,
                    file_path=file_path,
                    payload={"db_file_path": db_file_path},
                )
            )
            return

        LOG.warning(
            "estimate_sample_count returned 0 with %d data rows; continue decode with unknown expected_samples",
            total_rows,
        )

    payload = None
    if n_samples > 0:
        payload = {"expected_samples": int(n_samples)}

    result_queue.put(
        Response(
            rsp_type=ResponseType.DECODE_START,
            file_path=file_path,
            payload={
                "db_file_path": db_file_path,
                "expected_samples": int(n_samples) if n_samples > 0 else 0,
            },
        )
    )

    data_segments_now = _segment_paths(data_path)
    if not data_segments_now:
        LOG.warning("Decode skipped: data mmap disappeared before decode: %s", data_path)
        result_queue.put(
            Response(
                rsp_type=ResponseType.DECODE_FILE_NOT_FOUND,
                file_path=file_path,
                payload={"db_file_path": db_file_path},
            )
        )
        return

    decode_data_path = str(MMAP_DUMP_PATH / (base + ".data"))
    if data_segments_now and not data_segments_now[0].name.endswith(".000.mmap"):
        LOG.warning(
            "Decode segmented input starts without .000: first=%s",
            data_segments_now[0],
        )

    LOG.info(
        "Decode input path: decode_data_path=%r file=%r segments=%d",
        decode_data_path,
        file_path,
        len(data_segments_now),
    )

    lib = CanDecoderLib.get()
    rc = lib.decode(
        decode_data_path,
        sig_dir_path,
        row_index_changed_path,
        row_index_path,
        value_path,
        rawvalue_path,
    )
    if rc in (-2, -6):
        for seg in data_segments_now:
            seg_size = -1
            write_count = -1
            status = -1
            try:
                seg_size = int(seg.stat().st_size)
                with open(seg, "rb") as f:
                    hdr = f.read(16)
                if len(hdr) == 16:
                    write_count = int(struct.unpack_from("<Q", hdr, 0)[0])
                    status = int(struct.unpack_from("<I", hdr, 12)[0])
            except Exception:
                pass
            LOG.error(
                "Decode input segment: path=%r size=%d write_count=%d status=%d",
                str(seg),
                seg_size,
                write_count,
                status,
            )

        LOG.warning(
            "Decode skipped: input mmap unavailable/not-ready (rc=%d) for %s [decode_data_path=%r]",
            rc,
            Path(file_path).name,
            decode_data_path,
        )
        result_queue.put(
            Response(
                rsp_type=ResponseType.DECODE_FILE_NOT_FOUND,
                file_path=file_path,
                payload={"db_file_path": db_file_path},
            )
        )
        return

    if rc != 0:
        LOG.error("C++ can_decoder_run returned error %d", rc)
        result_queue.put(
            Response(
                rsp_type=ResponseType.DECODE_COMPLETE,
                file_path=file_path,
                payload={"db_file_path": db_file_path},
            )
        )
        return

    LOG.debug("Decode completed: output is written to mmap only")
    result_queue.put(
        Response(
            rsp_type=ResponseType.DECODE_COMPLETE,
            file_path=file_path,
            payload={"db_file_path": db_file_path},
        )
    )


def decode_process(
    result_queue: mp.Queue,
    decode_jobs: Sequence[tuple[str, str]],
):
    """Child-process entry. Decodes (db_file_path, file_path) jobs sequentially."""

    if not MMAP_DUMP_PATH.exists():
        LOG.warning("mmap directory not found: %s", MMAP_DUMP_PATH)
        for db_file_path, file_path in list(decode_jobs):
            result_queue.put(
                Response(
                    rsp_type=ResponseType.DECODE_FILE_NOT_FOUND,
                    file_path=file_path,
                    payload={"db_file_path": db_file_path},
                )
            )
        return

    decode_db_cache: Dict[str, Optional[DecodeDB]] = {}

    for db_file_path, file_path in list(decode_jobs):
        decode_db = decode_db_cache.get(db_file_path)
        if decode_db is None:
            candb_info = _load_candb_pkl(db_file_path)
            if candb_info is None:
                LOG.warning("pkl not found for %s — cannot decode", db_file_path)
                result_queue.put(
                    Response(
                        rsp_type=ResponseType.DECODE_FILE_NOT_FOUND,
                        file_path=file_path,
                        payload={"db_file_path": db_file_path},
                    )
                )
                decode_db_cache[db_file_path] = None
                continue

            decode_db = DecodeDB.load(candb_info)
            decode_db_cache[db_file_path] = decode_db
            LOG.info("Decode DB loaded from pkl: %s", Path(db_file_path).stem)

        if decode_db is None:
            result_queue.put(
                Response(
                    rsp_type=ResponseType.DECODE_FILE_NOT_FOUND,
                    file_path=file_path,
                    payload={"db_file_path": db_file_path},
                )
            )
            continue

        decode_dir = str(MMAP_DUMP_PATH / Path(db_file_path).stem)
        _decode_one_file(
            result_queue=result_queue,
            decode_db=decode_db,
            db_file_path=db_file_path,
            file_path=file_path,
            decode_dir=decode_dir,
        )

FilePath = str
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

    def __init__(self):
        self._stop_event = mp.Event()
        self._stopped = False

        # --- Data ----#
        self.data: Dict[FilePath, CANLogDecodedDiskFile] = {}

        # ── Decode scheduling state (protected by _lock) ──────
        self._pending_jobs: list[tuple[str, str]] = []
        self._active_batch_jobs: list[tuple[str, str]] = []
        self._decoding_file: Optional[str] = None
        self._decoding_db_file: Optional[str] = None
        self._lock = threading.Lock()

        # ── IPC ───────────────────────────────────────────────
        self._result_queue: mp.Queue = mp.Queue()
        self._process: Optional[mp.Process] = None

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

        # ── Polling thread (main-process side) ────────────────
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="DecodeScheduler-poll",
            daemon=True,
        )
        self._poll_thread.start()

    # ═════════════════════════════════════════════════════════
    #  Mmap helpers
    # ═════════════════════════════════════════════════════════

    @staticmethod
    def _db_folder_key(db_file_path: str) -> str:
        """Return the subfolder name for a DBC file inside mmap/."""
        return Path(db_file_path).stem

    @staticmethod
    def _decode_dir_for_db(db_file_path: str) -> Path:
        """Return ``mmap/<db_key>/`` directory for decoded outputs."""
        return MMAP_DUMP_PATH / MessageDecoder._db_folder_key(db_file_path)

    @staticmethod
    def _data_mmap_exists(file_path: str) -> bool:
        base = Path(file_path).name
        p = MMAP_DUMP_PATH / (base + ".data.mmap")
        if p.exists():
            return True
        stem = p.name[:-5] if p.name.endswith(".mmap") else p.name
        return any(p.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))

    @staticmethod
    def _decode_mmaps_exist(file_path: str, db_file_path: str) -> bool:
        """signal_dir.mmap is finalised last by C++ — its presence means done.

        Also validates that the decode was produced from the *current*
        parsed data by comparing the source data.mmap write_count against
        the decode row_index.mmap sample_count.  If they disagree the
        decode is stale and the old mmaps are removed so decoding reruns.
        """
        decode_dir = MessageDecoder._decode_dir_for_db(db_file_path)
        base = Path(file_path).name
        sig = decode_dir / (base + ".signal_dir.mmap")
        sig_exists = sig.exists()
        if not sig_exists:
            stem = sig.name[:-5] if sig.name.endswith(".mmap") else sig.name
            sig_exists = any(sig.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))
        if not sig_exists:
            return False

        # ── Staleness check: compare source total_lines vs decode samples ──
        source_lines = MessageDecoder._source_total_lines(file_path)
        if source_lines > 0:
            decode_samples = MessageDecoder._decode_total_samples(file_path, db_file_path)
            if decode_samples >= 0 and decode_samples < source_lines:
                LOG.warning(
                    "Stale decode detected for %s (source=%d, decode_samples=%d) — removing old mmaps",
                    Path(file_path).name, source_lines, decode_samples,
                )
                MessageDecoder._remove_decode_mmaps(file_path, db_file_path)
                return False

        return True

    @staticmethod
    def _decode_total_samples(file_path: str, db_file_path: str) -> int:
        """Sum of sample_count across all decode row_index segments.

        Returns -1 if no segments could be read.
        """
        seg_paths = MessageDecoder._decode_row_index_segment_paths(file_path, db_file_path)
        if not seg_paths:
            return -1
        total = 0
        for p in seg_paths:
            try:
                with open(p, "rb") as f:
                    hdr = f.read(16)
                if len(hdr) < 16:
                    continue
                seg_count = int(struct.unpack_from("<Q", hdr, 0)[0])
                total += seg_count
            except OSError:
                continue
        return total

    @staticmethod
    def _remove_decode_mmaps(file_path: str, db_file_path: str):
        """Delete the four decode output mmaps for *file_path* under its DBC subfolder."""
        decode_dir = MessageDecoder._decode_dir_for_db(db_file_path)
        base = Path(file_path).name
        for suffix in (".signal_dir.mmap", ".row_index_changed.mmap", ".row_index.mmap",
                        ".value.mmap", ".rawvalue.mmap"):
            p = decode_dir / (base + suffix)
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
            stem = p.name[:-5] if p.name.endswith(".mmap") else p.name
            for seg in p.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"):
                try:
                    seg.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def remove_decode_folder(db_file_path: str):
        """Remove the entire decode-output folder for a specific DBC."""
        folder = MessageDecoder._decode_dir_for_db(db_file_path)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            LOG.info("Removed decode folder: %s", folder)

    @staticmethod
    def remove_log_decode_mmaps(file_path: str):
        """Remove decode mmaps for *file_path* from ALL DBC subfolders."""
        base = Path(file_path).name
        for sub in MMAP_DUMP_PATH.iterdir():
            if not sub.is_dir():
                continue
            for suffix in (".signal_dir.mmap", ".row_index_changed.mmap", ".row_index.mmap",
                            ".value.mmap", ".rawvalue.mmap"):
                p = sub / (base + suffix)
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
                stem = p.name[:-5] if p.name.endswith(".mmap") else p.name
                for seg in p.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"):
                    try:
                        seg.unlink(missing_ok=True)
                    except OSError:
                        pass

    # ═════════════════════════════════════════════════════════
    #  Process lifecycle
    # ═════════════════════════════════════════════════════════

    def _spawn_decode(self, decode_jobs: Sequence[tuple[str, str]]):
        """Spawn a child process that decodes jobs sequentially."""
        result_q = mp.Queue()
        self._result_queue = result_q      # polling thread picks this up
        self._process = mp.Process(
            target=decode_process,
            args=(result_q, list(decode_jobs)),
            daemon=True,
        )
        self._process.start()

    @staticmethod
    def _decode_row_index_segment_paths(file_path: str, db_file_path: str) -> list[Path]:
        decode_dir = MessageDecoder._decode_dir_for_db(db_file_path)
        base = Path(file_path).name
        row_idx = decode_dir / (base + ".row_index.mmap")
        if row_idx.exists():
            return [row_idx]
        stem = row_idx.name[:-5] if row_idx.name.endswith(".mmap") else row_idx.name
        return sorted(row_idx.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))

    @staticmethod
    def _source_total_lines(file_path: str) -> int:
        base = Path(file_path).name
        data_base = str(MMAP_DUMP_PATH / (base + ".data.mmap"))
        segs = _segment_paths(data_base)
        total_rows = 0
        for seg in segs:
            try:
                with open(seg, "rb") as f:
                    hdr = f.read(8)
                if len(hdr) == 8:
                    total_rows += int(struct.unpack("<Q", hdr)[0])
            except OSError:
                continue
        return max(0, int(total_rows))

    def _ensure_decoded_store(self, file_path: str, db_file_path: str) -> CANLogDecodedDiskFile:
        decoded = self.data.get(file_path)
        if decoded is None:
            decoded = CANLogDecodedDiskFile(file_path=file_path)
            self.data[file_path] = decoded

        decode_dir = self._decode_dir_for_db(db_file_path)
        base = Path(file_path).name

        decoded.file_path = file_path
        decoded.decode_signal_dir_mmap_path = str(decode_dir / (base + ".signal_dir.mmap"))
        decoded.decode_row_index_changed_mmap_path = str(decode_dir / (base + ".row_index_changed.mmap"))
        decoded.decode_row_index_mmap_path = str(decode_dir / (base + ".row_index.mmap"))
        decoded.decode_value_mmap_path = str(decode_dir / (base + ".value.mmap"))
        decoded.decode_rawvalue_mmap_path = str(decode_dir / (base + ".rawvalue.mmap"))
        return decoded

    def _read_decode_progress(self, file_path: str, db_file_path: str) -> Optional[DecodeStreamingData]:
        seg_paths = MessageDecoder._decode_row_index_segment_paths(file_path, db_file_path)
        if not seg_paths:
            return None

        sample_count = 0
        running = False
        for p in seg_paths:
            try:
                with open(p, "rb") as f:
                    hdr = f.read(16)  # sample_count(8) + capacity(4) + status(4)
                if len(hdr) < 16:
                    continue
                seg_count = int(struct.unpack_from("<Q", hdr, 0)[0])
                seg_status = int(struct.unpack_from("<I", hdr, 12)[0])
                sample_count += seg_count
                if seg_status == 0:
                    running = True
            except OSError:
                continue

        expected_samples = max(0, int(self._decode_expected_samples.get(file_path, 0)))
        source_total_lines = max(0, int(self._source_total_lines(file_path)))
        verified_lines = source_total_lines

        if verified_lines <= 0:
            current_lines = 0
            verified_lines = 0
        else:
            if running:
                if expected_samples > 0:
                    current_lines = int((sample_count * verified_lines) / expected_samples)
                    current_lines = max(0, min(current_lines, verified_lines))
                else:
                    current_lines = 0
            else:
                current_lines = verified_lines

        percent = int((current_lines / verified_lines) * 100) if verified_lines > 0 else 0
        percent = max(0, min(percent, 100))

        return DecodeStreamingData(
            file_path=file_path,
            current_size=current_lines,
            verified_size=verified_lines,
            mmap_file_count=len(seg_paths),
            is_loading=running,
            percent=percent,
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
        try:
            decoded = self._ensure_decoded_store(file_path=file_path, db_file_path=db_file_path)
            decoded.refresh_decode_mmap_runtime()
        except Exception:
            return

    def _update_decode_signal_list(self, file_path: str, db_file_path: str):
        decoded = self._ensure_decoded_store(file_path=file_path, db_file_path=db_file_path)
        cache_key = (file_path, db_file_path)
        decode_dir = self._decode_dir_for_db(db_file_path)
        sig_base = decode_dir / (Path(file_path).name + ".signal_dir.mmap")
        if not _segment_paths(str(sig_base)):
            prev_pairs = self._known_decode_signals.get(cache_key, set())
            decoded.decode_signal_list = []
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

        signal_pairs: set[tuple[int, int]] = set()

        try:
            with SignalDirMmap(str(sig_base)) as sig_dir_mm:
                for entry in sig_dir_mm.iter_entries():
                    can_id = int(entry.can_id)
                    signal_id = int(entry.signal_id)
                    signal_pairs.add((can_id, signal_id))
        except Exception:
            LOG.exception("emit_new_signal_come failed for %s", Path(file_path).name)
            return

        prev_pairs = self._known_decode_signals.get(cache_key, set())
        sorted_pairs = sorted(signal_pairs)
        decoded.decode_signal_list = sorted_pairs

        if signal_pairs != prev_pairs:
            self._known_decode_signals[cache_key] = set(signal_pairs)
            self.event_on_new_signal_come.notify(
                DecodeSignalListData(
                    file_path=file_path,
                    db_file_path=db_file_path,
                    signal_list=sorted_pairs,
                )
            )

    def _kill_process(self):
        """Terminate the worker process and clean up incomplete mmaps."""
        if self._process and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2.0)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=1.0)
        self._process = None
        # Remove partially-written decode mmaps from the killed decode
        if self._decoding_file and self._decoding_db_file:
            self._remove_decode_mmaps(self._decoding_file,
                                       self._decoding_db_file)
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
        return bool(self._process and self._process.is_alive())

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
        self._spawn_decode(runnable)
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
        with self._lock:
            self._kill_process()
            self._enqueue_jobs_locked(decode_jobs, replace=True)

    def cmd_submit_decode_jobs(self, decode_jobs: Sequence[tuple[str, str]]):
        """Append mandatory (db_file_path, file_path) pairs to queue."""
        LOG.debug("cmd_submit_decode_jobs")
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
        with self._lock:
            self._kill_process()
            self._pending_jobs = []

    # ═════════════════════════════════════════════════════════
    #  Polling thread
    # ═════════════════════════════════════════════════════════

    def _poll_loop(self):
        LOG.debug("Start _poll_loop thread")
        while not self._stop_event.is_set():
            try:
                rsp: Response = self._result_queue.get(timeout=0.1)
            except queue.Empty:
                self._emit_decode_progress()
                continue
            except OSError:
                continue

            if rsp is None:
                continue
            self._dispatch(rsp)

    def _dispatch(self, rsp: Response):
        if rsp.rsp_type == ResponseType.DECODE_START:
            LOG.info("Decode started: %s", Path(rsp.file_path).name)
            decode_db_file = None
            if isinstance(rsp.payload, dict):
                decode_db_file = rsp.payload.get("db_file_path")
            with self._lock:
                self._decoding_file = rsp.file_path
                self._decoding_db_file = decode_db_file
            if decode_db_file:
                decoded = self._ensure_decoded_store(rsp.file_path, decode_db_file)
                decoded.decode_is_loading = True
            if decode_db_file:
                self._known_decode_signals[(rsp.file_path, decode_db_file)] = set()
            if isinstance(rsp.payload, dict):
                try:
                    expected = int(rsp.payload.get("expected_samples", 0))
                except Exception:
                    expected = 0
                if expected > 0:
                    self._decode_expected_samples[rsp.file_path] = expected
            self.event_on_decode_start.notify(rsp.file_path)
            self._emit_decode_progress()
        elif rsp.rsp_type == ResponseType.DECODE_COMPLETE:
            decode_db_file = None
            if isinstance(rsp.payload, dict):
                decode_db_file = rsp.payload.get("db_file_path")
            with self._lock:
                if not decode_db_file:
                    decode_db_file = self._decoding_db_file
            if decode_db_file:
                self._sync_decode_state_to_store(file_path=rsp.file_path, db_file_path=decode_db_file)
                self._update_decode_signal_list(file_path=rsp.file_path, db_file_path=decode_db_file)

            with self._lock:
                if self._decoding_file == rsp.file_path:
                    self._decoding_file = None
                finished_job = (decode_db_file, rsp.file_path) if decode_db_file else None
                if finished_job in self._active_batch_jobs:
                    self._active_batch_jobs.remove(finished_job)
                if not self._active_batch_jobs:
                    self._decoding_db_file = None
            LOG.info("Decode complete: %s", Path(rsp.file_path).name)
            if decode_db_file:
                final_data = self._read_decode_progress(rsp.file_path, decode_db_file)
                if final_data is not None:
                    if final_data.verified_size > 0:
                        final_data.current_size = final_data.verified_size
                    final_data.is_loading = False
                    final_data.percent = 100 if final_data.verified_size > 0 else final_data.percent
                    self._last_progress[rsp.file_path] = (
                        final_data.current_size,
                        final_data.verified_size,
                        final_data.mmap_file_count,
                        final_data.is_loading,
                        final_data.percent,
                    )
                    self.event_on_decode_progress.notify(final_data)
                decoded = self._ensure_decoded_store(rsp.file_path, decode_db_file)
                decoded.decode_current_size = int(final_data.current_size) if final_data else 0
                decoded.decode_verified_size = int(final_data.verified_size) if final_data else decoded.decode_verified_size
                decoded.decode_mmap_file_count = int(final_data.mmap_file_count) if final_data else decoded.decode_mmap_file_count
                decoded.decode_percent = int(final_data.percent) if final_data else decoded.decode_percent
                decoded.decode_is_loading = False
            self._decode_expected_samples.pop(rsp.file_path, None)
            self.event_on_decode_complete.notify(rsp.file_path)
        elif rsp.rsp_type == ResponseType.DECODE_FILE_NOT_FOUND:
            decode_db_file = None
            if isinstance(rsp.payload, dict):
                decode_db_file = rsp.payload.get("db_file_path")
            with self._lock:
                if self._decoding_file == rsp.file_path:
                    self._decoding_file = None
                finished_job = (decode_db_file, rsp.file_path) if decode_db_file else None
                if finished_job in self._active_batch_jobs:
                    self._active_batch_jobs.remove(finished_job)
                if not self._active_batch_jobs:
                    self._decoding_db_file = None
            self._last_progress.pop(rsp.file_path, None)
            self._decode_expected_samples.pop(rsp.file_path, None)
            if decode_db_file:
                decoded = self._ensure_decoded_store(rsp.file_path, decode_db_file)
                decoded.decode_is_loading = False
            self.event_on_decode_file_not_found.notify(rsp.file_path)

        with self._lock:
            if self._process and not self._process.is_alive():
                self._process = None
            self._maybe_spawn_pending_locked()

    # ═════════════════════════════════════════════════════════
    #  Lifecycle
    # ═════════════════════════════════════════════════════════

    def stop(self, timeout: float = 2.0):
        """Gracefully shut down the background process and polling thread."""
        if self._stopped:
            return
        self._stopped = True
        self._stop_event.set()
        with self._lock:
            self._kill_process()
        if self._poll_thread.is_alive():
            self._poll_thread.join(timeout=timeout)

if __name__ == "__main__":
    import sys
    import time
    setup_logger(env="DEV", backup_count=30)
    DB_FILE = "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/EEA10_CANFD_R00c_withADAS_Main.dbc"
    #FILELOG = "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1.asc"
    FILELOG = "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x4000.asc"
    FILELOG = "/home/gnar911/Desktop/2025-02-11_11-14-53_仕様情報切替 1_x10.asc"
    DB_FILE = "/home/gnar911/Desktop/20260122 APP WEBSITE - CAN ANALYZER 3.0 CBCM TOOL APP ARC/CAN_Analyzer_MVVM/Database/EEA10_CANFD_R00c_withADAS_Main.dbc"

    def wait_until(predicate, timeout_sec: float = 180.0, poll_sec: float = 0.2) -> bool:
        end = time.time() + timeout_sec
        while time.time() < end:
            if predicate():
                return True
            time.sleep(poll_sec)
        return False

    def flatten_signal_count(batches: list[list[SignalFilter]]) -> int:
        return sum(len(batch) for batch in batches)

    def all_signal_filters_resolved(batches: list[list[SignalFilter]]) -> bool:
        all_filters = [sig for batch in batches for sig in batch]
        if not all_filters:
            return False
        return all(
            sf.msg_info is not None and sf.signal_info is not None and sf.rawvalue is None
            for sf in all_filters
        )

    def cleanup_all(lcm: Any, md: MessageDecoder, file_path: str):
        try:
            md.cmd_discard_decode()
            # md.remove_log_decode_mmaps(file_path)
        except Exception:
            pass
        try:
            if file_path in lcm.contexts:
                # lcm.delete_context(file_path)
                pass
            else:
                # lcm.mCLM.delete_log_mmap(file_path)
                # lcm.mCLM.delete_log_file(file_path)
                pass
        except Exception:
            pass

    ################## TEST 1 #####
    # Load DB -> pkl -> if parser mmap exists then decoder auto starts.
    LOG.info("=== DECODER TEST 1: DB loaded first; mmap existence gates decode ===")
    from can_sdk.src.can_sdk.vm.canlog_viewmodel import LogContextViewModel
    mclm1 = LogContextViewModel()
    md1 = MessageDecoder()

    ############### PARSE LOG ###################
    # req_parse = mclm1.request_verify_file(FILELOG)
    # wait_until(
    #     lambda:     LOG.info(
    #     "TEST1-PARSE => request_verify=%s parsed_done=%s"
    # ),
    #     timeout_sec=180.0,
    # )

    # sys.exit(1)

    # t1_start: list[str] = []
    # t1_complete: list[str] = []
    # t1_new_signal_batches: list[list[SignalFilter]] = []
    # t1_seen_signal_names: set[str] = set()

    # def on_t1_new_signal_come(ev: DecodeSignalListData):
    #     sigs = list(ev.signal_list) if ev else []
    #     t1_new_signal_batches.append(sigs)
    #     for can_id, signal_id in sigs:
    #         sig_name = f"{int(can_id)}:{int(signal_id)}"
    #         t1_seen_signal_names.add(sig_name)
    #         LOG.info("TEST1-EVENT-NAME => %s", sig_name)
    #     sample_names = sorted(t1_seen_signal_names)[:10]
    #     LOG.info(
    #         "TEST1-EVENT => batch=%d total_unique_signal_names=%d sample=%s",
    #         len(sigs), len(t1_seen_signal_names), sample_names,
    #     )

    # md1.event_on_decode_start.subscribe(lambda p: t1_start.append(p))
    # md1.event_on_decode_complete.subscribe(lambda p: t1_complete.append(p))
    # md1.event_on_new_signal_come.subscribe(on_t1_new_signal_come)

    # try:
    #     md1.cmd_decode(DB_FILE, FILELOG)
    #     wait_until(lambda: len(t1_start) > 0, timeout_sec=10.0)

    #     t1_new_signal_come = wait_until(
    #         lambda: flatten_signal_count(t1_new_signal_batches) > 0,
    #         timeout_sec=30.0,
    #     )
    # finally:
    #     md1.stop()


    # t1_new_signal_resolved = all_signal_filters_resolved(t1_new_signal_batches)
    # LOG.info(
    #     "TEST1-C => new_signal_batches=%d new_signals=%d resolved=%s event_ok=%s",
    #     len(t1_new_signal_batches),
    #     flatten_signal_count(t1_new_signal_batches),
    #     t1_new_signal_resolved,
    #     t1_new_signal_come,
    # )
    # if RUN_DELETE_MMAP_CHECK:
    #     start_before_delete = len(t1_start)
    #     mclm1.mCLM.delete_log_mmap(FILELOG)
    #     if FILELOG in mclm1.contexts:
    #         mclm1.event_on_canlog_data_available.notify(mclm1.contexts[FILELOG])
    #     else:
    #         mclm1.event_on_canlog_data_available.notify(FILELOG)
    #     time.sleep(2.0)
    #     no_decode_when_mmap_missing = len(t1_start) == start_before_delete
    #     LOG.info(
    #         "TEST1-B => mmap_deleted=True no_decode_when_missing=%s (start_before=%d start_after=%d)",
    #         no_decode_when_mmap_missing, start_before_delete, len(t1_start),
    #     )
    # else:
    #     no_decode_when_mmap_missing = True
    #     LOG.info("TEST1-B => skipped mmap delete check (RUN_DELETE_MMAP_CHECK=False)")

    ################## TEST 2 #####
    # Delete mmap and parse again:
    #   - DB missing  => event_on_canlog_data_available should do nothing.
    #   - Load DB file => decoder should auto start from current parsed file.
    # LOG.info("=== DECODER TEST 2: parse signal path with DB missing/existing ===")
    # legacy test scaffold removed
    # mclm2 = LogContextViewModel()
    # md2 = MessageDecoder()

    # t2_start: list[str] = []
    # t2_complete: list[str] = []
    # t2_new_signal_batches: list[list[SignalFilter]] = []
    # t2_seen_signal_names: set[str] = set()

    # def on_t2_new_signal_come(sigs: list[SignalFilter]):
    #     t2_new_signal_batches.append(sigs)
    #     for sf in sigs:
    #         if sf and sf.sig_name:
    #             t2_seen_signal_names.add(sf.sig_name)
    #             LOG.info("TEST2-EVENT-NAME => %s", sf.sig_name)
    #     sample_names = sorted(t2_seen_signal_names)[:10]
    #     LOG.info(
    #         "TEST2-EVENT => batch=%d total_unique_signal_names=%d sample=%s",
    #         len(sigs), len(t2_seen_signal_names), sample_names,
    #     )

    # md2.event_on_decode_start.subscribe(lambda p: t2_start.append(p))
    # md2.event_on_decode_complete.subscribe(lambda p: t2_complete.append(p))
    # md2.event_on_new_signal_come.subscribe(on_t2_new_signal_come)

    # cleanup_all(mclm2, md2, FILELOG)

    # req2 = mclm2.request_verify_file(FILELOG)
    # parsed2 = req2 and wait_until(lambda: parse_done(mclm2, FILELOG), timeout_sec=180.0)

    # md2.remove_log_decode_mmaps(FILELOG)

    # time.sleep(2.0)
    # no_decode_without_db = (len(t2_start) == 0 and len(t2_complete) == 0)
    # no_new_signal_without_db = flatten_signal_count(t2_new_signal_batches) == 0
    # LOG.info(
    #     "TEST2-A => request_verify=%s parsed=%s no_decode_without_db=%s no_new_signal_without_db=%s",
    #     req2, parsed2, no_decode_without_db, no_new_signal_without_db,
    # )

    # mdbc2.load_database(DB_FILE)
    # mdbc2.set_main_db_file(DB_FILE)
    # decode_after_db_loaded = wait_until(
    #         lambda: len(t2_complete) >= 1,
    #         timeout_sec=180.0,
    #     )
    # LOG.info(
    #     "TEST2-B => db_loaded=True decode_started=%d decode_completed=%d decode_after_db_loaded=%s",
    #     len(t2_start), len(t2_complete), decode_after_db_loaded,
    # )

    # t2_new_signal_come = wait_until(
    #     lambda: flatten_signal_count(t2_new_signal_batches) > 0,
    #     timeout_sec=30.0,
    # )
    # t2_new_signal_resolved = all_signal_filters_resolved(t2_new_signal_batches)
    # LOG.info(
    #     "TEST2-C => new_signal_batches=%d new_signals=%d resolved=%s event_ok=%s",
    #     len(t2_new_signal_batches),
    #     flatten_signal_count(t2_new_signal_batches),
    #     t2_new_signal_resolved,
    #     t2_new_signal_come,
    # )

    ################## TEST 3 ####################
    # Read signal_dir.mmap and collect all signal_id values.
    # Complexity: O(n) over directory entries.
    # LOG.info("=== DECODER TEST 3: collect all signal_id from signal_dir.mmap ===")
    # signal_ids_ok = False
    # try:
    #     decode_dir_t3 = MessageDecoder._decode_dir_for_db(DB_FILE)
    #     signal_dir_base = decode_dir_t3 / (Path(FILELOG).name + ".signal_dir.mmap")
    #     signal_dir_segments = _segment_paths(str(signal_dir_base))

    #     if not signal_dir_segments:
    #         LOG.warning("TEST3 => signal_dir mmap not found: %s", signal_dir_base)
    #     else:
    #         all_signal_ids: list[int] = []
    #         all_can_ids: list[int] = []
    #         total_samples = 0
    #         data_rows = 0

    #         data_base = MMAP_DUMP_PATH / (Path(FILELOG).name + ".data.mmap")
    #         for seg in _segment_paths(str(data_base)):
    #             try:
    #                 with open(seg, "rb") as f:
    #                     hdr = f.read(8)
    #                     if len(hdr) == 8:
    #                         data_rows += int(struct.unpack("<Q", hdr)[0])
    #             except OSError:
    #                 continue

    #         with SignalDirMmap(str(signal_dir_base)) as sig_dir_mm:
    #             for entry in sig_dir_mm.iter_entries():
    #                 all_signal_ids.append(int(entry.signal_id))
    #                 all_can_ids.append(int(entry.can_id))
    #                 total_samples += int(entry.sample_count)

    #         unique_signal_ids = sorted(set(all_signal_ids))
    #         unique_can_ids = sorted(set(all_can_ids))
    #         signal_ids_ok = len(all_signal_ids) > 0
    #         avg_samples_per_row = (total_samples / data_rows) if data_rows > 0 else 0.0

    #         LOG.info(
    #             "TEST3 => dir_entries=%d unique_signal_ids=%d unique_can_ids=%d",
    #             len(all_signal_ids), len(unique_signal_ids), len(unique_can_ids),
    #         )
    #         LOG.info(
    #             "TEST3 => data_rows=%d total_samples=%d avg_samples_per_row=%.4f",
    #             data_rows, total_samples, avg_samples_per_row,
    #         )
    #         LOG.info(
    #             "TEST3 => signal_id sample(first20)=%s",
    #             unique_signal_ids[:20],
    #         )
    # except Exception:
    #     LOG.exception("TEST3 => failed to read signal_dir mmap")

    # cleanup_all(mclm1, md1, FILELOG)
    # cleanup_all(mclm2, md2, FILELOG)
    # md1.stop()
    # md2.stop()

    ################## TEST 4: Test  ####################
    LOG.info("=== DECODER TEST 4: read raw/value list lengths for one decoded signal ===")
    from can_sdk.test_ultility import TEST_setup_file_decoder, TEST_set_up_1_basic_context

    target_can_id = 0x340
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
            row_indices = raw_filelog.get_row_indices_by_list_id([target_can_id])
            timestamps = raw_filelog.get_timestamps_by_can_id(target_can_id)
            LOG.info(
                "[TEST] can_id=0x%X row_index_len=%d timestamp_len=%d",
                target_can_id,
                len(row_indices),
                len(timestamps),
            )

    md4 = TEST_setup_file_decoder(FILELOG, DB_FILE)
    try:
        decode_done = wait_until(
            lambda: MessageDecoder._decode_mmaps_exist(FILELOG, DB_FILE),
            timeout_sec=180.0,
        )
        if not decode_done:
            LOG.warning("TEST4 => decode timeout or mmap not ready for %s", Path(FILELOG).name)
            sys.exit(1)

        decode_dir_t4 = MessageDecoder._decode_dir_for_db(DB_FILE)
        base_t4 = Path(FILELOG).name

        dd4 = CANLogDecodedDiskFile(file_path=FILELOG)
        dd4.decode_signal_dir_mmap_path = str(decode_dir_t4 / (base_t4 + ".signal_dir.mmap"))
        dd4.decode_row_index_changed_mmap_path = str(decode_dir_t4 / (base_t4 + ".row_index_changed.mmap"))
        dd4.decode_row_index_mmap_path = str(decode_dir_t4 / (base_t4 + ".row_index.mmap"))
        dd4.decode_value_mmap_path = str(decode_dir_t4 / (base_t4 + ".value.mmap"))
        dd4.decode_rawvalue_mmap_path = str(decode_dir_t4 / (base_t4 + ".rawvalue.mmap"))
        dd4.refresh_decode_mmap_runtime()

        entries_t4 = dd4._load_decode_signal_directory_entries()
        if not entries_t4:
            LOG.warning("TEST4 => no decoded signal entries found for %s", Path(FILELOG).name)
            sys.exit(1)

        target_entry_t4 = next((e for e in entries_t4 if int(e[0]) == target_can_id), None)
        if target_entry_t4 is None:
            LOG.warning(
                "TEST4 => target can_id=0x%X not found in decoded entries (total_entries=%d)",
                target_can_id,
                len(entries_t4),
            )
            sys.exit(1)

        target_entries_t4 = [e for e in entries_t4 if int(e[0]) == target_can_id]
        target_entries_t4 = sorted(target_entries_t4, key=lambda e: int(e[1]))

        LOG.info(
            "TEST4 => can_id=0x%X total_signal_ids=%d",
            target_can_id,
            len(target_entries_t4),
        )

        for can_id_t4, signal_id_t4, *_ in target_entries_t4:
            values_t4 = dd4.get_signal_value_list_by_key(can_id=int(can_id_t4), signal_id=int(signal_id_t4))
            raw_values_t4 = dd4.get_signal_rawvalue_list_by_key(can_id=int(can_id_t4), signal_id=int(signal_id_t4))

            LOG.info(
                "TEST4 => can_id=0x%X signal_id=%d value_len=%d rawvalue_len=%d",
                int(can_id_t4),
                int(signal_id_t4),
                len(values_t4),
                len(raw_values_t4),
            )
    finally:
        md4.stop()


