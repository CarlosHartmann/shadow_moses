"""Step 5 of the forest recon: the extracted dump lines as Parquet, with the fields the rendering needs.

The otacon extraction (otacon_output/, see ../../../../otacon past_commands/forest_text_extraction/
REPORT.md) holds the original JSON lines of all discussion comments, the submissions and the
submitters' comments, one NDJSON file per dump file. Their fields differ between years (e.g.
created_utc as a string in early dumps, author_flair_richtext only from 2019), so they are
normalised here into one schema per kind:

- work/texts/RC/RC_YYYY-MM.parquet: id, author, flair_text, flair_richtext, body, created_utc,
  parent_id, link_id, is_submitter, distinguished, score, subreddit
- work/texts/RS/RS_YYYY-MM.parquet: id, author, flair_text, flair_richtext, title, selftext, url,
  is_self, link_flair_text, over_18, distinguished, score, subreddit, created_utc

Ids are integers (base 36 decoded); parent_id keeps its t1_/t3_ prefix. flair_richtext is the
richtext flair as plain text (text parts, and emoji codes such as :flag-us:). One worker per
file; finished files are skipped on a rerun.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROWS_PER_GROUP = 200_000

COMMON = [("id", pa.int64()), ("author", pa.string()), ("flair_text", pa.string()),
          ("flair_richtext", pa.string())]
SCHEMAS = {
    "RC": pa.schema(COMMON + [
        ("body", pa.string()), ("created_utc", pa.int64()), ("parent_id", pa.string()),
        ("link_id", pa.int64()), ("is_submitter", pa.bool_()), ("distinguished", pa.string()),
        ("score", pa.int64()), ("subreddit", pa.string()),
    ]),
    "RS": pa.schema(COMMON + [
        ("title", pa.string()), ("selftext", pa.string()), ("url", pa.string()), ("is_self", pa.bool_()),
        ("link_flair_text", pa.string()), ("over_18", pa.bool_()), ("distinguished", pa.string()),
        ("score", pa.int64()), ("subreddit", pa.string()), ("created_utc", pa.int64()),
    ]),
}


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def as_int(value):
    return None if value in (None, "") else int(float(value))


def richtext(value) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    return "".join(part.get("t") or part.get("a") or "" for part in value if isinstance(part, dict)) or None


def normalise(kind: str, d: dict) -> dict:
    row = {
        "id": int(d["id"], 36), "author": d.get("author"), "flair_text": d.get("author_flair_text"),
        "flair_richtext": richtext(d.get("author_flair_richtext")),
        "distinguished": d.get("distinguished"), "score": as_int(d.get("score")),
        "subreddit": d.get("subreddit"), "created_utc": as_int(d.get("created_utc")),
    }
    if kind == "RC":
        link = d.get("link_id") or ""
        row.update(body=d.get("body"), parent_id=d.get("parent_id"),
                   link_id=int(link[3:], 36) if link.startswith("t3_") else None,
                   is_submitter=d.get("is_submitter"))
    else:
        row.update(title=d.get("title"), selftext=d.get("selftext"), url=d.get("url"),
                   is_self=d.get("is_self"), link_flair_text=d.get("link_flair_text"),
                   over_18=d.get("over_18"))
    return row


def convert(source: Path, target: Path) -> int:
    kind = source.name[:2]
    schema = SCHEMAS[kind]
    temporary = target.with_name(f".{target.name}.tmp")
    rows, count = [], 0
    with open(source, encoding="utf-8") as lines, pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
        for line in lines:
            rows.append(normalise(kind, json.loads(line)))
            if len(rows) == ROWS_PER_GROUP:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                count += len(rows)
                rows = []
        writer.write_table(pa.Table.from_pylist(rows, schema=schema))
        count += len(rows)
    temporary.rename(target)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=HERE / "otacon_output")
    parser.add_argument("--out-dir", type=Path, default=HERE / "work" / "texts")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--files", default="*", help="glob within RC/ and RS/, e.g. 'R?_2012-03'")
    args = parser.parse_args()
    jobs = []
    for kind in ("RS", "RC"):
        (args.out_dir / kind).mkdir(parents=True, exist_ok=True)
        for source in sorted((args.source / kind).glob(f"{args.files}.ndjson")):
            target = args.out_dir / kind / f"{source.stem}.parquet"
            if not target.exists():
                jobs.append((source, target))
    jobs.sort(key=lambda job: -job[0].stat().st_size)  # largest first, for an even finish
    total = sum(source.stat().st_size for source, _ in jobs)
    log(f"{len(jobs)} files to convert ({total / 1e9:.1f} GB), {args.workers} workers")
    started, done, rows = time.monotonic(), 0, 0
    with ProcessPoolExecutor(args.workers) as pool:
        futures = {pool.submit(convert, source, target): source for source, target in jobs}
        for number, future in enumerate(as_completed(futures), start=1):
            source = futures[future]
            rows += future.result()
            done += source.stat().st_size
            elapsed = time.monotonic() - started
            eta = timedelta(seconds=round(elapsed / done * (total - done))) if done else timedelta()
            log(f"[{number}/{len(jobs)}] {source.name}: {rows:,} rows so far; {done / 1e9:.1f}/{total / 1e9:.1f} GB, "
                f"elapsed {timedelta(seconds=round(elapsed))}, ETA {eta} (~{datetime.now() + eta:%a %H:%M})")
    log("done")


if __name__ == "__main__":
    main()
