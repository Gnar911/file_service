from pathlib import Path
import re
from typing import Callable

import pandas as pd
from can import ASCReader, BLFReader

from can_sdk.data_object import CANLogFile
from lw.logger_setup import LOG

from ..repository.record_repository import CANLogRepository
from .define import PAGE_SIZE, get_file_type


class FileLoaderMixin:
    _ws_re: re.Pattern[str]
    _various_parse_line_test: Callable[[str, int], bool]
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
            parsed = self._various_parse_line_test(line_norm, index)
            if not parsed:
                continue
            parsed_count += 1
        canlf.total_lines = parsed_count
        LOG.info(f"{parsed_count}")
        return True

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
            parsed = self._various_parse_line_test(line_norm, index)
            if not parsed:
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