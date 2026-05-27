from __future__ import annotations

import csv
from datetime import datetime
from typing import Any

from can_sdk.data_object import CANLogLine
from ..record_id import RecordId
from ..repository.record_repository import RecordRepository


class CANLogExport:
    def __init__(self, repository: RecordRepository):
        self.repository = repository
        self.db = None
        self.file_input = ""

    def write_log_csv(self, file_key: RecordId | str, lines: list[CANLogLine], save_filepath: str | None = None) -> str | None:
        canlf = self.repository.get_logfile_data(file_key)
        if canlf is None:
            return None

        file_path = canlf.file_path
        if not save_filepath:
            save_filepath = file_path + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"

        with open(save_filepath, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            msg_filt = canlf.group_messages_by_can_id(lines)

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
        input_path: RecordId | str,
        output_path: str,
        time_range: tuple[float, float] | None = None,
        id_set: set[int] | None = None,
        signal_filters: dict[str, tuple[str, Any]] | None = None,
    ) -> str | None:
        lines = self.repository.get_all_log_data(input_path)
        if not lines:
            return None

        if time_range is not None:
            lines = list(self.repository.get_messages_by_timestamp_range(input_path, time_range[0], time_range[1]))

        if id_set:
            lines = [line for line in lines if line.can_id in id_set]

        if signal_filters:
            def _match_signal_filters(line: CANLogLine) -> bool:
                for sig_name, (op, expected) in signal_filters.items():
                    signal = line.message_obj.signals.get(sig_name)
                    if signal is None:
                        return False
                    actual = signal.raw_value
                    if op == "==" and str(actual) != str(expected):
                        return False
                return True

            lines = [line for line in lines if _match_signal_filters(line)]

        return self.write_log_csv(input_path, lines, save_filepath=output_path)

    def write_log_filterd_by_time(self):
        return None

    def write_log_filterd_by_msg(self):
        return None

    def write_log_filterd_by_signal(self):
        return None
