"""Optional: compress the large files of the pipeline to save disk space.

This is not a step of the pipeline, and nothing depends on it. Every script that reads one of the
files below opens it through ../shared/large_files.py, which takes the uncompressed file or its
.zst archive, whichever is there. The scripts that write them always write the uncompressed file.
So this script can be run at any point (before, between or after the steps), any number of times,
or never.

Files (relative to 0-assets/raw_data/main/), each turned into NAME.zst:
- 01-extraction_stage/second-attempt.jsonl                       read by step 2
- 02-filtering/filtered_comments.jsonl                           read by steps 3a, 4 and 8
- 03a-grouping/grouped_subreddit_data/baseline.jsonl             read by step 4
- 04-cross-subreddit-data/cross_subreddit_data/baseline.jsonl    read by step 6
- 08-full-contexts/programmatic/otacon_output/RC|RS/*.ndjson     read by step 8 (texts_to_parquet.py)

Files that are not there (a step not run yet, or already compressed) are skipped. If a file and
its archive both exist, a step has written the file anew: it is compressed again and replaces the
old archive.

Each file is compressed to a temporary name, the archive is decompressed again and compared with
the original (SHA-256), and only then the archive gets its name and the original is removed. A
file that changes meanwhile (a step still writing it) is left alone.

Run: poetry run python 0-assets/raw_data/main/optional/compress_large_files.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path

import zstandard

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
sys.path.insert(0, str(MAIN / "shared"))
import large_files  # noqa: E402

TARGETS = (
    "01-extraction_stage/second-attempt.jsonl",
    "02-filtering/filtered_comments.jsonl",
    "03a-grouping/grouped_subreddit_data/baseline.jsonl",
    "04-cross-subreddit-data/cross_subreddit_data/baseline.jsonl",
    "08-full-contexts/programmatic/otacon_output/RC/*.ndjson",
    "08-full-contexts/programmatic/otacon_output/RS/*.ndjson",
)
LEVEL = 12  # run of 2026-11-17: 14% of the size at about 100 MB/s, check included; level 19 gains little more
CHUNK = 16 * 1024 * 1024
PROGRESS_EVERY = 10 * 1024**3


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def find_sources(main: Path, targets: tuple[str, ...] = TARGETS) -> list[Path]:
    """The uncompressed files that are there, in the order of the targets."""
    return [source for target in targets for source in sorted(main.glob(target)) if source.is_file()]


def state(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def compress(source: Path, level: int = LEVEL, threads: int = 4) -> int | None:
    """Replace source by its verified archive. Returns the archive's size, or None if source changed."""
    archive = large_files.archive_of(source)
    temporary = source.with_name(f".{source.name}{large_files.SUFFIX}.tmp")
    before = state(source)
    original, restored = hashlib.sha256(), hashlib.sha256()
    try:
        compressor = zstandard.ZstdCompressor(level=level, threads=threads, write_checksum=True)
        with open(source, "rb") as raw, open(temporary, "wb") as out:
            done = 0
            with compressor.stream_writer(out, size=before[0], closefd=False) as writer:
                while chunk := raw.read(CHUNK):
                    original.update(chunk)
                    writer.write(chunk)
                    if (done + len(chunk)) // PROGRESS_EVERY > done // PROGRESS_EVERY:
                        log(f"  {source.name}: {(done + len(chunk)) / 1e9:.0f} of {before[0] / 1e9:.0f} GB")
                    done += len(chunk)
        with open(temporary, "rb") as packed:
            reader = zstandard.ZstdDecompressor(max_window_size=large_files.MAX_WINDOW).stream_reader(packed)
            while chunk := reader.read(CHUNK):
                restored.update(chunk)
        if state(source) != before:
            log(f"  {source.name} changed while it was compressed; left as it is")
            return None
        if restored.digest() != original.digest():
            raise RuntimeError(f"The archive of {source} does not give back the original; nothing was removed.")
        temporary.replace(archive)
        source.unlink()
        return archive.stat().st_size
    except zstandard.ZstdError as error:
        if state(source) != before:  # e.g. the file grew, so the announced size was wrong
            log(f"  {source.name} changed while it was compressed; left as it is")
            return None
        raise RuntimeError(f"Compressing {source} failed: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--level", type=int, default=LEVEL, help="zstd level (default: %(default)s)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true", help="only list what would be compressed")
    args = parser.parse_args()
    sources = find_sources(MAIN)
    total = sum(source.stat().st_size for source in sources)
    log(f"{len(sources)} uncompressed files, {total / 1e9:.1f} GB" + ("" if sources else "; nothing to do"))
    saved, started = 0, time.monotonic()
    for source in sources:
        size = source.stat().st_size
        name = source.relative_to(MAIN)
        if args.dry_run:
            log(f"would compress {name} ({size / 1e9:.2f} GB)")
            continue
        file_started = time.monotonic()
        packed = compress(source, args.level, args.threads)
        if packed is not None:
            saved += size - packed
            log(f"{name}: {size / 1e9:.2f} GB -> {packed / 1e9:.2f} GB in {(time.monotonic() - file_started) / 60:.1f} min")
    if sources and not args.dry_run:
        log(f"done: {saved / 1e9:.1f} GB saved in {(time.monotonic() - started) / 60:.0f} min")


if __name__ == "__main__":
    main()
