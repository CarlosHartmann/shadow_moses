"""Reading the large files of the pipeline, compressed or not.

A few inputs and outputs of the pipeline are tens of GB each (e.g.
02-filtering/filtered_comments.jsonl). ../optional/compress_large_files.py can turn each of them
into NAME.zst to save disk space. The scripts that read these files open them through this module,
so they work with either form and nothing has to be decompressed first. The scripts that write
them always write the uncompressed file.

If both forms exist, the uncompressed file is read: it is the one a step has just written, and the
archive next to it may be older.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TextIO

import zstandard

SUFFIX = ".zst"
FRAME_HEADER_BYTES = 18  # the longest zstd frame header
MAX_WINDOW = 2**31  # also reads archives made with the zstd command's --long option


def archive_of(path: Path) -> Path:
    return path.with_name(path.name + SUFFIX)


def existing(path: Path | str) -> Path:
    """The file to read for path: path itself, or path + ".zst" if only that exists."""
    path = Path(path)
    if path.exists():
        return path
    if archive_of(path).exists():
        return archive_of(path)
    raise FileNotFoundError(f"Neither {path} nor {archive_of(path).name} exists.")


def open_text(path: Path | str, buffering: int = -1) -> TextIO:
    """Open path (or its archive) for reading UTF-8 text, as open(path, encoding="utf-8") would."""
    source = existing(path)
    if source.suffix != SUFFIX:
        return open(source, "r", encoding="utf-8", buffering=buffering)
    reader = zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW).stream_reader(
        open(source, "rb"), read_across_frames=True)
    return io.TextIOWrapper(reader, encoding="utf-8")


def uncompressed_size(path: Path | str) -> int:
    """Bytes of the uncompressed content; 0 if an archive does not state it.

    For progress reports: the buffer.tell() of a file from open_text() counts uncompressed bytes
    in both forms.
    """
    source = existing(path)
    if source.suffix != SUFFIX:
        return source.stat().st_size
    with open(source, "rb") as handle:
        return max(zstandard.frame_content_size(handle.read(FRAME_HEADER_BYTES)), 0)
