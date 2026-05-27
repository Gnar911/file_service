from __future__ import annotations

import os
from typing import Any

from can import LogReader
from lw.logger_setup import LOG


class BLFConverter:
    def __init__(
        self,
        file_input: str,
        db: Any = None,
        size_limit_bytes: int = 9999 * 1024 * 1024,
    ):
        self.db = db
        self.file_input = str(file_input)
        self.size_limit_bytes = int(size_limit_bytes)

    def format_log_entry(self, msg, time_base: float) -> str:
        timestamp = f"{(msg.timestamp - time_base):.6f}".ljust(12)
        cantype = ("CANFD" if msg.is_fd else "CAN").ljust(8)
        channel = "1" if msg.channel == 0 else "2"
        direction = ("Rx" if msg.is_rx else "Tx").ljust(3)
        can_id_str = f"{msg.arbitration_id:X}".rjust(10)
        try:
            if self.db is not None:
                message = self.db.get_message_by_frame_id(msg.arbitration_id)
                message_name = message.name
            else:
                message_name = "UNKNOW"
        except Exception:
            message_name = "NOT_FOUND"
        message_name = message_name.ljust(40)
        dlc = str(msg.dlc).ljust(3)
        raw_data_bytes = " ".join(f"{byte:02X}" for byte in msg.data).upper()
        return f"{timestamp} {cantype} {channel} {direction} {can_id_str}    {message_name} {dlc} {raw_data_bytes}\n"

    def get_output_filename(self, output_dir: str, index: int) -> str:
        base = os.path.splitext(os.path.basename(self.file_input))[0]
        return os.path.join(output_dir, f"{base}_part{index}.asc")

    def convert_blf_file(self) -> str:
        output_dir = os.path.join(os.path.dirname(self.file_input), "output")
        os.makedirs(output_dir, exist_ok=True)

        writer = None
        output_path = ""
        file_index = 1
        file_size = 0
        time_base = 0.0
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
                if file_size >= self.size_limit_bytes:
                    writer.close()
                    file_index += 1
                    file_size = 0
                    output_path = self.get_output_filename(output_dir, file_index)
                    writer = open(output_path, "w", encoding="utf-8")

            if writer is not None:
                writer.close()
        return output_path


__all__ = ["BLFConverter"]