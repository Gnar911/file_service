from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading

from lw.logger_setup import LOG

from file_service.recorder.mmap_batch_writer import recorder_staging_bytes_written, recorder_staging_path


_COPY_CHUNK_BYTES = 1024 * 1024


def _snapshot_file_path(destination_dir: str | Path) -> Path:
	dst_dir = Path(destination_dir)
	dst_dir.mkdir(parents=True, exist_ok=True)
	timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
	return dst_dir / f"record_{timestamp}.mmap"

def _copy_snapshot_bytes(destination_path: Path, bytes_to_copy: int) -> None:
	source_path = recorder_staging_path()
	remaining = max(0, int(bytes_to_copy))
	try:
		destination_path.parent.mkdir(parents=True, exist_ok=True)
		with open(destination_path, "wb") as dst:
			if source_path.exists() and remaining > 0:
				with open(source_path, "rb") as src:
					while remaining > 0:
						chunk = src.read(min(_COPY_CHUNK_BYTES, remaining))
						if not chunk:
							break
						dst.write(chunk)
						remaining -= len(chunk)
			dst.flush()
		LOG.info(
			"[RECORDER][SAVE] snapshot_saved dst=%s bytes_requested=%d bytes_remaining=%d",
			str(destination_path),
			int(bytes_to_copy),
			int(remaining),
		)
	except Exception:
		LOG.exception("[RECORDER][SAVE] snapshot copy failed dst=%s", str(destination_path))


def save_record_snapshot_async(destination_dir: str | Path) -> Path:
	destination_path = _snapshot_file_path(destination_dir)
	bytes_to_copy = recorder_staging_bytes_written()
	copy_thread = threading.Thread(
		target=_copy_snapshot_bytes,
		args=(destination_path, int(bytes_to_copy)),
		name="CBCM-record-save",
		daemon=True,
	)
	copy_thread.start()
	return destination_path


__all__ = ["save_record_snapshot_async"]