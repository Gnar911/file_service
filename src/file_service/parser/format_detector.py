import re
from typing import Callable, Optional
from lw.logger_setup import LOG

class FormatDetector:
    pattern_parsers: list[tuple[re.Pattern[str], Callable]]
    detected_parser: Optional[Callable]
    _FMT_MAP: dict[Callable, int]
    _ws_re: re.Pattern[str]
    _various_parse_line_test: Callable[[str, int], object]
    _parse_canoe: Callable[[str, int], object]
    _parse_canoe_full: Callable[[str, int], object]
    _parse_canoe_compact: Callable[[str, int], object]
    _parse_cancommander: Callable[[str, int], object]
    _parse_filter_log: Callable[[str, int], object]
    _parse_cansuke: Callable[[str, int], object]
    _parse_cancommander_type2: Callable[[str, int], object]
    _parse_cancommander_type3: Callable[[str, int], object]

    def _detect_format(self, file_path: str) -> Optional[int]:
        self.detected_parser = None
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
                for line_number, line in enumerate(file_handle, start=1):
                    self._various_parse_line_test(line, line_number)
                    if self.detected_parser:
                        return self._FMT_MAP.get(self.detected_parser)
        except Exception:
            pass
        return None

    def _detect_field_layout(self, file_path: str):
        from file_service.parser.native.can_parser_api import FieldLayout, FL_DLC_IS_HEX, FL_IS_TAB, FL_TS_IS_MS

        valid_dlc = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64})
        hex_chars = frozenset("0123456789abcdefABCDEF")

        def _find_dlc_data(tokens, start):
            for i in range(start, len(tokens) - 1):
                token = tokens[i]
                if len(token) <= 2 and token.isdigit():
                    value = int(token)
                    if value in valid_dlc:
                        next_token = tokens[i + 1]
                        if len(next_token) == 2 and next_token[0] in hex_chars and next_token[1] in hex_chars:
                            return i, i + 1
            return None, None

        detected_func = None
        matched_raw = None
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
                for line in file_handle:
                    raw = line.rstrip("\n")
                    normalized = self._ws_re.sub(" ", raw.strip())
                    for pattern, func in self.pattern_parsers:
                        if pattern.match(normalized):
                            detected_func = func
                            matched_raw = raw
                            break
                    if detected_func:
                        break
        except Exception:
            return None

        if not detected_func or not matched_raw:
            return None

        tokens = matched_raw.split()

        if detected_func == self._parse_canoe:
            dlc_idx, data_idx = _find_dlc_data(tokens, 5)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANOE: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=4, idx_dir=3, idx_dlc=dlc_idx, idx_data=data_idx, idx_chan=2, flags=0)

        if detected_func == self._parse_canoe_full:
            dlc_idx, data_idx = _find_dlc_data(tokens, 8)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANOE_FULL: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=2, idx_id=6, idx_dir=7, idx_dlc=dlc_idx, idx_data=data_idx, idx_chan=5, flags=0)

        if detected_func == self._parse_canoe_compact:
            dlc_idx, data_idx = _find_dlc_data(tokens, 4)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANOE_CMP: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=3, idx_dlc=dlc_idx, idx_data=data_idx, idx_chan=1, flags=0)

        if detected_func == self._parse_cancommander:
            dlc_idx, data_idx = _find_dlc_data(tokens, 8)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANCMD: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=2, idx_id=6, idx_dir=7, idx_dlc=dlc_idx, idx_data=data_idx, idx_chan=-1, flags=0)

        if detected_func == self._parse_filter_log:
            dlc_idx, data_idx = _find_dlc_data(tokens, 5)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout FILTER: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=4, idx_dir=3, idx_dlc=dlc_idx, idx_data=data_idx, idx_chan=-1, flags=0)

        if detected_func == self._parse_cansuke:
            dlc_idx, data_idx = _find_dlc_data(tokens, 4)
            if dlc_idx is None:
                return None
            LOG.info(f"FieldLayout CANSUKE: dlc@{dlc_idx} data@{data_idx}")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=3, idx_dlc=dlc_idx, idx_data=data_idx, idx_chan=-1, flags=0)

        if detected_func == self._parse_cancommander_type2:
            LOG.info("FieldLayout CANCMD_T2 (TAB)")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=6, idx_dlc=4, idx_data=5, idx_chan=1, flags=FL_TS_IS_MS | FL_DLC_IS_HEX | FL_IS_TAB)

        if detected_func == self._parse_cancommander_type3:
            LOG.info("FieldLayout CANCMD_T3")
            return FieldLayout(idx_ts=0, idx_id=2, idx_dir=-3, idx_dlc=3, idx_data=4, idx_chan=1, flags=FL_TS_IS_MS | FL_DLC_IS_HEX)

        return None