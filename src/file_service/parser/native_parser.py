from file_service.module.fs_core import *
from lw.logger_setup import LOG

### RUN on child process, binding class for native C++ parser
class NativeParser:
    file_path: str

    @classmethod
    def run_native_to_mmap(cls, file_path: str, token_id: str) -> bool:
        cls.file_path = file_path
        rc = run_worker_segmented(file_path, str(token_id))
        try:
            return int(rc) == 0
        except Exception:
            return False

    @classmethod
    def parse(cls, file_path: str, token_id: str) -> bool:
        return cls.run_native_to_mmap(file_path, str(token_id))
    
