"""Step 2 of the forest recon: one scan of the index, keeping every comment of the seeds' threads.

Reads the by_id copy of the index (../../../../ index on rdisk2, see forest_recon_index.md) file by
file and keeps the rows whose link_id is the thread of at least one seed (seeds.parquet from
prepare_ids.py). Only the structure columns are read (id, parent_id, parent_is_submission,
link_id, created_utc); the subreddit column is skipped.

One output file per index file (thread_rows/part-NNN.parquet in the work directory). Finished
files are skipped, so an interrupted run can resume. After each file, the progress is logged with
the read rate and an estimate of the remaining time.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DEFAULT_INDEX = Path("/Volumes/rdisk2/redditdata/index")
COLUMNS = "id, parent_id, parent_is_submission, link_id, created_utc"


def parse_parts(spec: str | None, count: int) -> list[int]:
    if not spec:
        return list(range(count))
    parts: list[int] = []
    for piece in spec.split(","):
        low, _, high = piece.partition("-")
        parts.extend(range(int(low), int(high or low) + 1))
    return sorted(set(parts))


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--parts", help="Index files to scan, e.g. '29-31,109-111' (default: all)")
    parser.add_argument("--memory-limit", default="4GB")
    args = parser.parse_args()

    files = sorted((args.index / "by_id").glob("part-*.parquet"))
    chosen = [files[i] for i in parse_parts(args.parts, len(files))]
    out_dir = args.work_dir / "thread_rows"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute("SET threads = 1")  # one reader: parallel reads make the USB disk seek
    con.execute(
        f"CREATE TABLE threads AS SELECT DISTINCT link_id FROM '{args.work_dir / 'seeds.parquet'}'"
    )

    todo = [f for f in chosen if not (out_dir / f.name).exists()]
    total_bytes = sum(f.stat().st_size for f in todo)
    log(f"{len(chosen)} index files chosen, {len(chosen) - len(todo)} already done, "
        f"{len(todo)} to scan ({total_bytes / 1e9:.1f} GB on disk)")
    started, done_bytes, kept_total = time.monotonic(), 0, 0
    for number, path in enumerate(todo, start=1):
        temporary = out_dir / f".{path.name}.tmp"
        con.execute(
            f"""COPY (SELECT {COLUMNS} FROM read_parquet('{path}')
                      WHERE link_id IN (SELECT link_id FROM threads))
                TO '{temporary}' (FORMAT parquet, COMPRESSION zstd)"""
        )
        (kept,) = con.execute(f"SELECT count(*) FROM read_parquet('{temporary}')").fetchone()
        temporary.rename(out_dir / path.name)
        kept_total += kept
        done_bytes += path.stat().st_size
        elapsed = time.monotonic() - started
        rate = done_bytes / elapsed
        eta = timedelta(seconds=round((total_bytes - done_bytes) / rate))
        log(f"[{number}/{len(todo)}] {path.name}: {kept:,} rows kept ({kept_total:,} in total); "
            f"{done_bytes / 1e9:.1f}/{total_bytes / 1e9:.1f} GB, {rate / 1e6:.1f} MB/s (file size), "
            f"elapsed {timedelta(seconds=round(elapsed))}, ETA {eta} "
            f"(~{datetime.now() + eta:%a %H:%M})")
    log("done")


if __name__ == "__main__":
    main()
