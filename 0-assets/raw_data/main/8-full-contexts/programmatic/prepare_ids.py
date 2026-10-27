"""Step 1 of the forest recon: collect the ids that the tree building starts from.

- op_ids.parquet: every comment that contains "OP" (all of ../../2-filtering/filtered_comments.jsonl),
  as integer ids (base 36 decoded, as in the index), sorted. These decide which comment of a path is
  the initial mention.
- seeds.parquet: every comment of the finalised study datasets (as in step 7), with its thread
  (link_id), creation time and the study groups whose files contain it.

Both are written to the work directory (default: work/ next to this script, gitignored).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[1]
FILTERED = MAIN / "2-filtering" / "filtered_comments.jsonl"
AGE_GROUPS = ("age_silent", "age_boomer", "age_X", "age_millennial", "age_Z", "age_young", "age_old")
OTHER_GROUPS = (
    "gender_male",
    "gender_female",
    "political_conservative",
    "political_progressive",
    "gender_political_conservative",
    "gender_political_progressive",
)
# group -> files (original and cross-subreddit comments), the same finalised datasets as in step 7
STUDY_FILES = {
    **{g: [MAIN / "3b-age-grouping" / f"{g}.jsonl"] for g in AGE_GROUPS},
    **{g: [MAIN / "3a-grouping" / "grouped_subreddit_data" / f"{g}.jsonl"] for g in OTHER_GROUPS},
    "baseline": [MAIN / "6-baseline-reduction" / "reduced_baseline.jsonl"],
}
for group in (*AGE_GROUPS, *OTHER_GROUPS):
    STUDY_FILES[group].append(MAIN / "4-cross-subreddit-data" / "cross_subreddit_data" / f"{group}.jsonl")

JSON_OPTIONS = "format = 'newline_delimited', maximum_object_size = 16777216"
# Reddit ids are base 36; DuckDB has no base-36 cast, so it is spelled out (ids are at most 13 digits).
BASE36 = """list_reduce(
    list_transform(string_split(lower({col}), ''),
                   c -> CAST(strpos('0123456789abcdefghijklmnopqrstuvwxyz', c) - 1 AS BIGINT)),
    (acc, d) -> acc * 36 + d)"""


def base36(col: str) -> str:
    return BASE36.format(col=col)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--memory-limit", default="5GB")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute(f"SET temp_directory = '{args.work_dir / 'duckdb_tmp'}'")
    con.execute("SET threads = 4")
    con.execute("SET preserve_insertion_order = false")

    started = time.monotonic()
    print(f"Seeds from {sum(len(f) for f in STUDY_FILES.values())} study files ...", flush=True)
    selects = []
    for group, files in STUDY_FILES.items():
        for path in files:
            selects.append(
                f"SELECT id, link_id, CAST(created_utc AS BIGINT) AS created_utc, subreddit, '{group}' AS grp "
                f"FROM read_json('{path}', columns = {{id: 'VARCHAR', link_id: 'VARCHAR', "
                f"created_utc: 'VARCHAR', subreddit: 'VARCHAR'}}, {JSON_OPTIONS})"
            )
    con.execute(
        f"""COPY (
            SELECT {base36('id')} AS id, id AS id36,
                   {base36("substr(any_value(link_id), 4)")} AS link_id,
                   any_value(created_utc) AS created_utc, any_value(subreddit) AS subreddit,
                   string_agg(DISTINCT grp, ',' ORDER BY grp) AS groups
            FROM ({' UNION ALL '.join(selects)})
            GROUP BY id ORDER BY id
        ) TO '{args.work_dir / 'seeds.parquet'}' (FORMAT parquet)"""
    )
    seeds, threads = con.execute(
        f"SELECT count(*), count(DISTINCT link_id) FROM '{args.work_dir / 'seeds.parquet'}'"
    ).fetchone()
    print(f"  {seeds:,} seeds in {threads:,} threads ({time.monotonic() - started:.0f} s)", flush=True)

    started = time.monotonic()
    print(f"OP comment ids from {FILTERED.name} ({FILTERED.stat().st_size / 1e9:.0f} GB) ...", flush=True)
    con.execute(
        f"""COPY (
            SELECT DISTINCT {base36('id')} AS id
            FROM read_json('{FILTERED}', columns = {{id: 'VARCHAR'}}, {JSON_OPTIONS})
            ORDER BY id
        ) TO '{args.work_dir / 'op_ids.parquet'}' (FORMAT parquet)"""
    )
    (ops,) = con.execute(f"SELECT count(*) FROM '{args.work_dir / 'op_ids.parquet'}'").fetchone()
    (missing,) = con.execute(
        f"SELECT count(*) FROM '{args.work_dir / 'seeds.parquet'}' s ANTI JOIN "
        f"'{args.work_dir / 'op_ids.parquet'}' o USING (id)"
    ).fetchone()
    print(f"  {ops:,} OP comments ({time.monotonic() - started:.0f} s); seeds not among them: {missing:,}")


if __name__ == "__main__":
    main()
