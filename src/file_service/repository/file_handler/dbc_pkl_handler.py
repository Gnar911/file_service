from __future__ import annotations

import os
import pickle
from pathlib import Path

from can_sdk.dbc_manager import CANDBInfo, MIXED_DB_KEY
from lw.logger_setup import LOG


class DBCPklHandler:
	def __init__(self, pkl_dir: str | Path):
		self._pkl_dir = Path(pkl_dir)

	@property
	def pkl_dir(self) -> Path:
		return self._pkl_dir

	def set_pkl_dir(self, pkl_dir: str | Path) -> None:
		self._pkl_dir = Path(pkl_dir)

	def ensure_dir(self) -> Path:
		self._pkl_dir.mkdir(parents=True, exist_ok=True)
		return self._pkl_dir

	@staticmethod
	def _stem_from_db_file_path(db_file_path: str) -> str:
		if db_file_path == MIXED_DB_KEY:
			return "mix"
		return os.path.splitext(os.path.basename(db_file_path))[0]

	def get_pkl_path(self, db_file_path: str) -> Path:
		stem = self._stem_from_db_file_path(str(db_file_path))
		return self._pkl_dir / f"{stem}.pkl"

	def exists(self, db_file_path: str) -> bool:
		return self.get_pkl_path(db_file_path).exists()

	def list_pkl_files(self) -> list[Path]:
		if not self._pkl_dir.exists():
			return []
		return sorted(self._pkl_dir.glob("*.pkl"))

	def load(self, db_file_path: str) -> CANDBInfo | None:
		pkl_path = self.get_pkl_path(db_file_path)
		if not pkl_path.exists():
			LOG.warning("DBC pkl not found: %s", pkl_path)
			return None

		try:
			with pkl_path.open("rb") as pkl_file:
				candb_info = pickle.load(pkl_file)
			LOG.info("Loaded DBC pkl: %s", pkl_path)
			return candb_info
		except Exception:
			LOG.exception("Failed to load DBC pkl: %s", pkl_path)
			return None

	def save(self, db_file_path: str, candb_info: CANDBInfo) -> Path:
		self.ensure_dir()
		pkl_path = self.get_pkl_path(db_file_path)
		with pkl_path.open("wb") as pkl_file:
			pickle.dump(candb_info, pkl_file)
		return pkl_path

	def remove(self, db_file_path: str) -> bool:
		return self.remove_path(self.get_pkl_path(db_file_path))

	def remove_path(self, pkl_path: str | Path) -> bool:
		path = Path(pkl_path)
		if not path.exists():
			return False

		try:
			path.unlink()
			return True
		except Exception as error:
			LOG.error("Failed to remove DBC pkl %s: %s", path, error)
			return False

	def clear(self) -> int:
		removed = 0
		for pkl_file in self.list_pkl_files():
			if self.remove_path(pkl_file):
				removed += 1
		return removed
