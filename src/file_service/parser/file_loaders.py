from pathlib import Path
import re
from typing import Callable

import pandas as pd
from can import ASCReader, BLFReader

from canapp.data_object import CANLogFile
from lw.logger_setup import LOG

# from ..repository.record_repository import CANLogRepository
from .define import PAGE_SIZE, get_file_type

""" 20260707 NOTE: The python parse can log file is officialy deprecated. A can log file always being parse with the native C++ and with data base storage
    A CSV file will need to be convert to the normal parse-able file before passing to C++ native
"""
class FileLoaderMixin:
    _ws_re: re.Pattern[str]
    detect_pattern: Callable[[str], int]
    _create_log_entry: Callable[..., object]

    def load_log_file(self, canlf: CANLogFile) -> bool:
        file_path = canlf.file_path
        self.detected_parser = None
        self.last_raw_by_id = {}

        result = False
        file_type = get_file_type(file_path)
        if file_type in ("asc", "blf"):
            result = self._parse_from_asc(canlf)
        elif file_type == "csv":
            result = self._parse_from_csv(canlf)
        elif file_type == "excel":
            result = self._parse_from_excel(canlf)
        elif file_type in ("log", "txt"):
            result = self._parse_from_file(canlf)
        return result

    @DeprecationWarning
    def _parse_from_csv(self, canlf: CANLogFile):
        file_path = canlf.file_path
        parsed_count = 0
        try:
            df = pd.read_csv(file_path)
        except Exception as error:
            LOG.error(f"Failed to read CSV file: {error}")
            return False
        for index, row in df.iterrows():
            line = " ".join(str(cell) for cell in row if pd.notna(cell))
            line_norm = self._ws_re.sub(" ", line.strip())
            if not self.detect_pattern(line_norm):
                continue
            parsed_count += 1
        canlf.total_lines = parsed_count
        LOG.info(f"{parsed_count}")
        return True

    @DeprecationWarning
    def _parse_from_excel(self, canlf: CANLogFile):
        file_path = canlf.file_path
        parsed_count = 0
        try:
            df = pd.read_excel(file_path)
        except Exception as error:
            LOG.error(f"Failed to read Excel file: {error}")
            return False
        for index, row in df.iterrows():
            line = " ".join(str(cell) for cell in row if pd.notna(cell))
            line_norm = self._ws_re.sub(" ", line.strip())
            if not self.detect_pattern(line_norm):
                continue
            parsed_count += 1
        canlf.total_lines = parsed_count
        LOG.info(f"{parsed_count}")
        return True

    def format_channel(self, channel) -> str:
        if channel is None:
            return ""
        if isinstance(channel, (list, tuple)):
            return ",".join(str(item) for item in channel)
        return str(channel)


    def _parse_from_asc(self, canlf: CANLogFile):
        file_path = canlf.file_path
        parsed_count = 0
        reader = None
        try:
            file_type = get_file_type(file_path)
            if file_type == "asc":
                reader = ASCReader(file_path)
            elif file_type == "blf":
                reader = BLFReader(file_path)
            else:
                LOG.debug("Asc file is not in standard format, try to parse by Regex")
                return self._parse_from_file(canlf)

            for line_number, msg in enumerate(reader, start=1):
                timestamp = msg.timestamp
                channel = self.format_channel(msg.channel)
                can_id_hex = f"0x{msg.arbitration_id:03X}"
                direction = "Rx" if msg.is_rx else "Tx"
                dlc = msg.dlc if hasattr(msg, "dlc") else len(msg.data)
                hex_data_str = " ".join(f"{byte:02X}" for byte in msg.data)
                message_name = getattr(msg, "name", None)
                parsed = self._create_log_entry(
                    line_number=line_number,
                    timestamp=float(timestamp),
                    channel=channel,
                    can_id_hex=can_id_hex,
                    direction=direction,
                    data_len=int(dlc),
                    raw_data=hex_data_str,
                    message_name=message_name,
                )
                if not parsed:
                    continue
                parsed_count += 1
                if parsed_count % PAGE_SIZE == 0:
                    LOG.info(f"{parsed_count}")
            canlf.total_lines = parsed_count
            return True
        except Exception as error:
            LOG.error(f"Failed to read ASC log file: {error}")
            LOG.debug("Fallback to regex parser for non-standard ASC/BLF")
            return self._parse_from_file(canlf)
        finally:
            try:
                if reader is not None:
                    reader.stop()
            except Exception:
                pass

    @DeprecationWarning
    def _parse_from_file(self, canlf: CANLogFile):
        print("_parse_from_file")
        file_path = canlf.file_path
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                #batch = []
                for i, line in enumerate(f, start=1):
                    if i % PAGE_SIZE == 0:
                            pass
                    parsed =  self._parse_line(line.strip(), i)
                    if not parsed:
                        continue
                    last_time = self.last_timestamp_by_id.get(parsed.can_id)
                    self.last_timestamp_by_id[parsed.can_id] = parsed.timestamp
                    parsed.cal_message_obj(last_time if last_time else parsed.timestamp)
                    canlf.log_entries[i] = parsed
                return True
        except Exception as e:
            LOG.error(f"Failed to read text log file: {e}")
            return False