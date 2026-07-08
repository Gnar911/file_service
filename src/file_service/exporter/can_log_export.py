from __future__ import annotations

import csv
from datetime import datetime
from typing import Any
from pathlib import Path

from can_sdk.data_object import CANLogLine
from ..metadata_id import LogId


class CANLogExport:
    def __init__(self):
        self.db = None
        self.file_input = ""

    def write_log_csv(self, file_key: LogId | str, lines: list[CANLogLine], save_filepath: str | None = None) -> str | None:
        if not lines:
            return None

        file_path = str(file_key)
        if not save_filepath:
            save_filepath = file_path + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
        Path(save_filepath).parent.mkdir(parents=True, exist_ok=True)

        with open(save_filepath, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            msg_filt: dict[int, list[CANLogLine]] = {}
            for line in lines:
                msg_filt.setdefault(int(line.can_id), []).append(line)

            for _, msg_lines in msg_filt.items():
                writer.writerow([
                    "Time",
                    "Channel",
                    "CAN ID",
                    "Message Name",
                    "Direction",
                    "DLC",
                    "Data",
                ])

                sig_names = msg_lines[0].get_list_signal_name_fromline()
                writer.writerow(sig_names)

                for line in msg_lines:
                    writer.writerow([
                        f"{line.timestamp:.6f}",
                        line.channel,
                        f"0x{line.can_id:X}",
                        line.message_name or "",
                        line.direction,
                        line.data_len,
                        line.raw_data,
                    ])
                    writer.writerow([
                        str(line.message_obj.signals[sig].raw_value)
                        if sig in line.message_obj.signals else ""
                        for sig in sig_names
                    ])

                writer.writerow([])
                writer.writerow([])
                writer.writerow([])
                writer.writerow([])
                writer.writerow([])

        return save_filepath

    def write_log_filtered(
        self,
        input_path: LogId | str,
        output_path: str,
        time_range: tuple[float, float] | None = None,
        id_set: set[int] | None = None,
        signal_filters: dict[str, tuple[str, Any]] | None = None,
    ) -> str | None:
        _ = (time_range, id_set, signal_filters)
        return self.write_log_csv(input_path, [], save_filepath=output_path)

    def write_log_filterd_by_time(self):
        return None

    def write_log_filterd_by_msg(self):
        return None

    def write_log_filterd_by_signal(self):
        return None
