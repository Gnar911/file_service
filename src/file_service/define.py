from pathlib import Path
import tempfile

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MMAP_TEMP_STORAGE_DIR = Path(tempfile.gettempdir()) / "file_service" / "mmap"
MMAP_LOCAL_STORAGE_DIR = DATA_DIR