import re
from typing import Callable, Optional, cast

from can_sdk.data_object import CANLogLine
from lw.logger_setup import LOG

from ..define import CANFD_DLC_MAP


class LineParserMixin:
    pattern_parsers: list[tuple[re.Pattern[str], Callable]]
    detected_parser: Optional[Callable]
    last_raw_by_id: dict[int, str]
    last_timestamp_by_id: dict[int, float]
    _ws_re: re.Pattern[str]
    _HEX_CHARS: frozenset[str]
    _VALID_DLC: frozenset[int]

    def _find_dlc_with_hex_payload(self, tokens: list[str]) -> tuple[Optional[int], Optional[int]]:
        for index, token in enumerate(tokens[:-1]):
            if len(token) <= 2 and token.isdigit():
                value = int(token)
                if value in self._VALID_DLC:
                    next_token = tokens[index + 1]
                    if len(next_token) == 2 and next_token[0] in self._HEX_CHARS and next_token[1] in self._HEX_CHARS:
                        return value, index
        return None, None

    def _various_parse_line_test(self, line: str, line_number: int):
        raw_line = line.rstrip("\n")
        normalized = self._ws_re.sub(" ", raw_line.strip())
        for pattern, func in self.pattern_parsers:
            match = pattern.match(normalized)
            if match:
                result = func(raw_line, line_number)
                self.detected_parser = func
                return result
        return None

    def _is_detected_parser(self, line: str, line_number: int) -> bool:
        normalized = self._ws_re.sub(" ", line.strip())
        for pattern, func in self.pattern_parsers:
            match = pattern.match(normalized)
            if match:
                LOG.info(f"[Line {line_number}] Detected parser: {func.__name__}")
                return True
        return False

    def _create_log_entry(
        self,
        line_number: int,
        timestamp: float,
        can_id_hex: str,
        direction: str,
        data_len: int,
        raw_data: str,
        channel: str | int | None = "",
        message_name: Optional[str] = "",
    ) -> Optional[CANLogLine]:
        try:
            can_id_token = can_id_hex.strip()
            if can_id_token.endswith(("x", "X")):
                can_id_token = can_id_token[:-1]
            can_id = int(can_id_token, 16)
        except Exception:
            return None

        prev_raw = self.last_raw_by_id.get(can_id)
        if not prev_raw:
            prev_raw = ""
            changed = False
        else:
            changed = prev_raw != raw_data
        self.last_raw_by_id[can_id] = raw_data

        last_time = self.last_timestamp_by_id.get(can_id)
        self.last_timestamp_by_id[can_id] = timestamp

        return CANLogLine(
            line_number=line_number,
            timestamp=timestamp,
            channel=cast(str, channel),
            can_id=can_id,
            _user_message_name=cast(str, message_name),
            direction=direction,
            data_len=data_len,
            raw_data=raw_data,
            changed=changed,
            last_raw_data=prev_raw,
            last_timestamp=last_time if last_time else timestamp,
        )

    def _parse_filter_log(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[0])
            direction = "Tx" if "Tx" in tokens else "Rx"
            dir_idx = tokens.index(direction)
            can_id = tokens[dir_idx + 1]
            message_name = tokens[dir_idx + 2] if len(tokens) > dir_idx + 2 else ""
            dlc, dlc_idx = self._find_dlc_with_hex_payload(tokens)
            if dlc_idx is None or dlc is None:
                return None
            data_list = tokens[dlc_idx + 1:dlc_idx + 1 + dlc]
            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id,
                direction=direction,
                data_len=dlc,
                raw_data=" ".join(data_list),
                message_name=message_name,
            )
        except Exception:
            return None

    def _parse_canoe(self, line: str, line_number: int) -> Optional[CANLogLine]:
        parts = line.split(None, 5)
        if len(parts) < 6:
            return None

        try:
            timestamp = float(parts[0])
        except Exception:
            return None

        channel = parts[2]
        direction = parts[3]
        can_id_hex = parts[4]
        tokens = parts[5].split()
        dlc, dlc_idx = self._find_dlc_with_hex_payload(tokens)
        if dlc_idx is None or dlc is None:
            return None

        end = min(dlc_idx + 1 + dlc, len(tokens))
        raw_data = " ".join(tokens[dlc_idx + 1:end])

        message_name = None
        if dlc_idx >= 4:
            name_end = dlc_idx - 3
            if name_end > 0:
                message_name = " ".join(tokens[:name_end])

        return self._create_log_entry(
            line_number=line_number,
            timestamp=timestamp,
            can_id_hex=can_id_hex,
            direction=direction,
            data_len=dlc,
            raw_data=raw_data,
            message_name=message_name,
            channel=channel,
        )

    def _parse_canoe_compact(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[0])
            channel = tokens[1]
            can_id_hex = tokens[2]
            direction = tokens[3]
            dlc = int(tokens[5])
            raw_data_tokens = tokens[6: 6 + dlc]

            if len(raw_data_tokens) != dlc:
                raise ValueError(f"Payload length mismatch: expected {dlc}, got {len(raw_data_tokens)}")

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                channel=channel,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=" ".join(raw_data_tokens),
                message_name=None,
            )
        except Exception:
            return None

    def _parse_canoe_full(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[2])
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"
            can_id_hex = tokens[dir_idx - 1]
            channel = int(tokens[5])

            dlc, dlc_idx = self._find_dlc_with_hex_payload(tokens)
            if dlc_idx is None or dlc is None:
                raise ValueError("Cannot determine DLC")

            raw_data_tokens = tokens[dlc_idx + 1: dlc_idx + 1 + dlc]

            message_name = None
            name_tokens = []
            for token in tokens[dir_idx + 1:]:
                if token.isdigit() or re.fullmatch(r"[0-9A-Fa-f]{2}", token):
                    break
                name_tokens.append(token)
            if name_tokens:
                message_name = " ".join(name_tokens)

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=" ".join(raw_data_tokens),
                message_name=message_name,
                channel=channel,
            )
        except Exception:
            return None

    def _parse_cansuke(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[0])
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"

            can_id_hex = tokens[dir_idx - 1]
            dlc = None
            dlc_idx = None
            for index in range(dir_idx + 1, len(tokens)):
                if tokens[index].isdigit():
                    dlc = int(tokens[index])
                    dlc_idx = index
                    break
            if dlc is None or dlc_idx is None:
                raise ValueError("DLC not found")

            raw_data_tokens = tokens[dlc_idx + 1: dlc_idx + 1 + dlc]
            if len(raw_data_tokens) != dlc:
                raise ValueError(f"Incomplete payload: expected {dlc}, got {len(raw_data_tokens)}")

            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=" ".join(raw_data_tokens),
            )
        except Exception:
            return None

    def _parse_cancommander(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[2])
            try:
                dir_idx = tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                dir_idx = tokens.index("Rx")
                direction = "Rx"

            can_id_hex = tokens[dir_idx - 1]
            message_name = tokens[dir_idx + 1]
            dlc, dlc_idx = self._find_dlc_with_hex_payload(tokens)
            if dlc_idx is None or dlc is None:
                raise ValueError("Cannot determine DLC")

            raw_data_tokens = tokens[dlc_idx + 1: dlc_idx + 1 + dlc]
            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=" ".join(raw_data_tokens),
                message_name=message_name,
            )
        except Exception:
            return None

    def _parse_cancommander_type2(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            cols = [cell.strip() for cell in line.split("\t")]
            if len(cols) < 7:
                return None
            ts_raw = cols[0]
            timestamp = float(ts_raw) / 1000.0 if ts_raw.isdigit() else float(ts_raw)
            channel = cols[1]
            can_id = cols[2]
            message_name = cols[3] or None
            dlc_token = cols[4].upper()
            data_tokens = cols[5].split()
            direction = cols[6]
            if dlc_token.isdigit():
                length = int(dlc_token)
            else:
                length = CANFD_DLC_MAP.get(dlc_token, len(data_tokens))
            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id,
                direction=direction,
                data_len=length,
                raw_data=" ".join(data_tokens[:length]),
                message_name=message_name,
                channel=channel,
            )
        except Exception:
            return None

    def _parse_cancommander_type3(self, line: str, line_number: int) -> Optional[CANLogLine]:
        try:
            tokens = line.split()
            timestamp = float(tokens[0]) * 0.001
            channel = tokens[1]
            can_id_hex = tokens[2]
            try:
                tokens.index("Tx")
                direction = "Tx"
            except ValueError:
                direction = "Rx"

            dlc, dlc_idx = self._find_dlc_with_hex_payload(tokens)
            if dlc_idx is None or dlc is None:
                raise ValueError("Cannot determine DLC")

            raw_data_tokens = tokens[dlc_idx + 1: dlc_idx + 1 + dlc]
            return self._create_log_entry(
                line_number=line_number,
                timestamp=timestamp,
                can_id_hex=can_id_hex,
                direction=direction,
                data_len=dlc,
                raw_data=" ".join(raw_data_tokens),
                channel=channel,
                message_name=None,
            )
        except Exception:
            return None

    def _entry_to_log_line(self, entry) -> Optional[CANLogLine]:
        raw_data = " ".join(f"{entry.data[j]:02X}" for j in range(entry.data_len))
        return self._create_log_entry(
            line_number=int(entry.line_number),
            timestamp=float(entry.timestamp),
            can_id_hex=f"{entry.can_id:X}",
            direction="Tx" if entry.direction == 1 else "Rx",
            data_len=int(entry.data_len),
            raw_data=raw_data,
            channel=entry.channel.decode("ascii", errors="ignore"),
            message_name="",
        )