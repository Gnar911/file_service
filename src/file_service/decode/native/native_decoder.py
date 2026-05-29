from __future__ import annotations

import struct
from pathlib import Path

from lw.logger_setup import LOG
from native_sdk.can_decoder_api import (
    CanDecoderLib,
    DecodeDB,
    RowIndexMmap,
    DECODE_STATUS_ERROR,
    estimate_sample_count,
)
from native_sdk.can_parser_api import IndexMmapData, MmapData


class NativeDecoder:
    @classmethod
    def get_status(cls, record_id=None, row_index_mmap_path: str | None = None) -> int:
        if not row_index_mmap_path:
            return int(DECODE_STATUS_ERROR)

        try:
            with RowIndexMmap(str(row_index_mmap_path)) as row_index_mmap:
                return int(row_index_mmap.status)
        except Exception as error:
            LOG.error("Decode mmap status read failed for %s: %s", record_id, error)
            return int(DECODE_STATUS_ERROR)


def _segment_paths(base_path: str) -> list[Path]:
    base = Path(base_path)
    if base.exists():
        return [base]
    stem = base.name[:-5] if base.name.endswith(".mmap") else base.name
    return sorted(base.parent.glob(f"{stem}.[0-9][0-9][0-9].mmap"))


def decode_one_file(
    decode_db: DecodeDB,
    db_file_path: str,
    record_mmap_path: Path,
) -> bool:
    base = Path(record_mmap_path)
    # Keep decode outputs flat in the same temp folder as parse mmaps.
    decode_dir_p = base.parent

    data_path = str(base.with_name(base.name + ".data.mmap"))
    index_path = str(base.with_name(base.name + ".index.mmap"))
    base_name = base.name
    sig_dir_path = str(decode_dir_p / (base_name + ".signal_dir.mmap"))
    row_index_changed_path = str(decode_dir_p / (base_name + ".row_index_changed.mmap"))
    row_index_path = str(decode_dir_p / (base_name + ".row_index.mmap"))
    value_path = str(decode_dir_p / (base_name + ".value.mmap"))
    rawvalue_path = str(decode_dir_p / (base_name + ".rawvalue.mmap"))

    data_segments = _segment_paths(data_path)
    if not data_segments:
        LOG.warning("data.mmap not found: %s", data_path)
        return False

    try:
        index_segments = _segment_paths(index_path)
        if index_segments:
            n_samples = 0
            for seg in index_segments:
                index_mm = IndexMmapData(str(seg))
                _, sample_count = estimate_sample_count(index_mm, decode_db)
                index_mm.close()
                n_samples += sample_count
        else:
            data_mm_tmp = MmapData(str(data_segments[0]))
            total_rows = 0
            for seg in data_segments:
                with open(seg, "rb") as f:
                    hdr = f.read(8)
                    if len(hdr) == 8:
                        total_rows += int(struct.unpack("<Q", hdr)[0])
            n_samples = total_rows * 20
            data_mm_tmp.close()
    except Exception:
        LOG.exception("Failed to estimate output sizes")
        return False

    data_segments_now = _segment_paths(data_path)
    if not data_segments_now:
        LOG.warning("Decode skipped: data mmap disappeared before decode: %s", data_path)
        return False

    decode_data_path = str(base.with_name(base.name + ".data"))

    lib = CanDecoderLib.get()
    rc = lib.decode(
        decode_data_path,
        sig_dir_path,
        row_index_changed_path,
        row_index_path,
        value_path,
        rawvalue_path,
    )

    if rc in (-2, -6):
        for seg in data_segments_now:
            seg_size = -1
            write_count = -1
            status = -1
            try:
                seg_size = int(seg.stat().st_size)
                with open(seg, "rb") as f:
                    hdr = f.read(16)
                if len(hdr) == 16:
                    write_count = int(struct.unpack_from("<Q", hdr, 0)[0])
                    status = int(struct.unpack_from("<I", hdr, 12)[0])
            except Exception:
                pass
            LOG.error(
                "Decode input segment: path=%r size=%d write_count=%d status=%d",
                str(seg),
                seg_size,
                write_count,
                status,
            )

        return False

    if rc != 0:
        LOG.error("C++ can_decoder_run returned error %d", rc)
        return False

    return True