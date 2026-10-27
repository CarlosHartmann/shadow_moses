"""Step 3 of the forest recon: build the discussions from the thread rows (structure only, no text).

Definitions (see notes.txt):
- A mention is a comment that contains "OP" (op_ids.parquet). Comments by the post's author are
  not known yet at this stage (no authors in the index); they are sorted out later.
- For each seed (study comment), the initial mention is the topmost mention on its path from the
  post down to the seed (the seed itself if there is none above). It names the discussion:
  t1_<initial mention id>. Mentions in other branches start their own discussions.
- A discussion holds the context (the comments from the one replying to the post down to the
  parent of the initial mention) and the body (the initial mention and everything below it).
  Discussions are only built if they contain a seed.

Inputs (work directory): thread_rows/*.parquet (scan_threads.py), seeds.parquet, op_ids.parquet.
Outputs (output directory):
- discussions.parquet: one row per discussion, with size, depth and branching statistics
- members.parquet: every comment of every discussion (role context/body, depth, parent, flags)
- seed_map.parquet: every seed with its discussion, or why it has none
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROWS_PER_BUCKET = 5_000_000
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

DISCUSSION_SCHEMA = pa.schema([
    ("discussion_id", pa.string()), ("initial_id", pa.int64()), ("link_id", pa.int64()),
    ("top_id", pa.int64()), ("upward_complete", pa.bool_()), ("context_size", pa.int32()),
    ("body_size", pa.int32()), ("body_depth", pa.int32()), ("max_replies", pa.int32()),
    ("mentions_in_body", pa.int32()), ("seeds", pa.int32()), ("initial_depth", pa.int32()),
])
MEMBER_SCHEMA = pa.schema([
    ("discussion_id", pa.string()), ("id", pa.int64()), ("role", pa.string()), ("depth", pa.int32()),
    ("parent_id", pa.int64()), ("parent_is_submission", pa.bool_()), ("created_utc", pa.uint32()),
    ("is_mention", pa.bool_()), ("is_seed", pa.bool_()),
])
SEED_SCHEMA = pa.schema([
    ("id", pa.int64()), ("discussion_id", pa.string()), ("is_initial", pa.bool_()), ("status", pa.string()),
])


def to_base36(n: int) -> str:
    out = ""
    while n:
        n, digit = divmod(n, 36)
        out = DIGITS[digit] + out
    return out or "0"


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def build_thread(link, rows, seeds, discussions, members, seed_rows):
    """rows: id -> (parent_id, parent_is_submission, created_utc, is_mention, is_seed)."""
    children = defaultdict(list)
    for comment, (parent, is_sub, *_rest) in rows.items():
        if not is_sub:
            children[parent].append(comment)
    by_initial: dict[int, dict] = {}
    for seed in seeds:
        if seed not in rows:
            seed_rows.append((seed, None, False, "not in index"))
            continue
        chain, current = [], seed
        while True:
            chain.append(current)
            parent, is_sub = rows[current][0], rows[current][1]
            if is_sub:
                complete = True
                break
            if parent not in rows:
                complete = False  # ancestor before 2010 or in a gap of the dumps
                break
            current = parent
        chain.reverse()
        position = next(i for i, c in enumerate(chain) if rows[c][3])
        initial = chain[position]
        entry = by_initial.setdefault(
            initial, {"context": chain[:position], "complete": complete, "seeds": set()}
        )
        entry["seeds"].add(seed)
    for initial, entry in by_initial.items():
        discussion_id = "t1_" + to_base36(initial)
        context = entry["context"]
        top_depth = 1 if entry["complete"] else None  # absolute depth unknown if the walk stopped
        base = len(context) + 1
        for offset, comment in enumerate(context):
            parent, is_sub, created, mention, is_seed = rows[comment]
            members.append((discussion_id, comment, "context", offset + 1 if top_depth else None,
                            parent, is_sub, created, mention, is_seed))
        body_size, body_depth, max_replies, mentions = 0, 0, 0, 0
        stack = [(initial, 0)]
        while stack:
            comment, level = stack.pop()
            parent, is_sub, created, mention, is_seed = rows[comment]
            replies = children.get(comment, ())
            body_size += 1
            body_depth = max(body_depth, level)
            max_replies = max(max_replies, len(replies))
            mentions += mention
            members.append((discussion_id, comment, "body", base + level if top_depth else None,
                            parent, is_sub, created, mention, is_seed))
            stack.extend((reply, level + 1) for reply in replies)
        discussions.append((
            discussion_id, initial, link, context[0] if context else initial, entry["complete"],
            len(context), body_size, body_depth, max_replies, mentions, len(entry["seeds"]),
            base if top_depth else None,
        ))
        for seed in entry["seeds"]:
            seed_rows.append((seed, discussion_id, seed == initial, "ok"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--out-dir", type=Path, default=HERE / "work" / "forest")
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--seed-filter", default="",
                        help="SQL condition on seeds (s.id, s.link_id), e.g. to restrict a pilot")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute("SET preserve_insertion_order = false")
    w = args.work_dir
    where = f"WHERE {args.seed_filter}" if args.seed_filter else ""
    con.execute(f"CREATE TABLE seeds AS SELECT s.id, s.link_id FROM '{w / 'seeds.parquet'}' s {where}")
    # Duplicates in the dumps are possible (forest_recon_index.md, Q4): keep one row per id.
    con.execute(f"""CREATE VIEW rows AS
        SELECT r.id, any_value(r.parent_id) AS parent_id, any_value(r.parent_is_submission) AS is_sub,
               any_value(r.link_id) AS link_id, min(r.created_utc) AS created_utc
        FROM read_parquet('{w / 'thread_rows' / '*.parquet'}') r
        WHERE r.link_id IN (SELECT link_id FROM seeds) GROUP BY r.id""")
    (total,) = con.execute("SELECT count(*) FROM rows").fetchone()
    buckets = max(1, -(-total // ROWS_PER_BUCKET))
    log(f"{total:,} thread rows, {buckets} buckets")

    writers = {name: pq.ParquetWriter(args.out_dir / f"{name}.parquet", schema)
               for name, schema in (("discussions", DISCUSSION_SCHEMA), ("members", MEMBER_SCHEMA),
                                    ("seed_map", SEED_SCHEMA))}
    started = time.monotonic()
    for bucket in range(buckets):
        table = con.execute(f"""
            SELECT r.*, (o.id IS NOT NULL) AS is_mention, (s.id IS NOT NULL) AS is_seed
            FROM rows r
            LEFT JOIN '{w / 'op_ids.parquet'}' o ON o.id = r.id
            LEFT JOIN seeds s ON s.id = r.id
            WHERE r.link_id % {buckets} = {bucket}""").to_arrow_table()
        threads = defaultdict(dict)
        cols = [table.column(n).to_pylist() for n in
                ("id", "parent_id", "is_sub", "link_id", "created_utc", "is_mention", "is_seed")]
        for cid, parent, is_sub, link, created, mention, is_seed in zip(*cols):
            threads[link][cid] = (parent, is_sub, created, mention, is_seed)
        seed_table = con.execute(f"SELECT id, link_id FROM seeds WHERE link_id % {buckets} = {bucket}").fetchall()
        seeds_by_thread = defaultdict(list)
        for sid, link in seed_table:
            seeds_by_thread[link].append(sid)
        discussions, members, seed_rows = [], [], []
        for link, seeds in seeds_by_thread.items():
            build_thread(link, threads.get(link, {}), seeds, discussions, members, seed_rows)
        for name, rows in (("discussions", discussions), ("members", members), ("seed_map", seed_rows)):
            if rows:
                schema = writers[name].schema
                writers[name].write_table(pa.Table.from_pylist(
                    [dict(zip(schema.names, row)) for row in rows], schema=schema))
        elapsed = time.monotonic() - started
        eta = timedelta(seconds=round(elapsed / (bucket + 1) * (buckets - bucket - 1)))
        log(f"[{bucket + 1}/{buckets}] {len(discussions):,} discussions, {len(members):,} members, "
            f"elapsed {timedelta(seconds=round(elapsed))}, ETA {eta}")
    for writer in writers.values():
        writer.close()
    log("done")


if __name__ == "__main__":
    main()
