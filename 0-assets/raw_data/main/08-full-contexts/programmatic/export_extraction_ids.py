"""Step 4 of the forest recon: the id lists for the text extraction from the dumps.

Writes, in base 36 without prefix, one id per line, sorted:
- comment_ids/RC_YYYY-MM.txt: every comment of every discussion (members/), split by the month of
  its created_utc, i.e. by the dump file that holds it
- submission_ids.txt: the submissions (threads) of all discussions, without months (the index has
  no submission times)
- manifest.tsv: the number of ids per file, to check the extraction against

Input: the forest output of build_forest.py (work/forest/).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

from build_forest import to_base36

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forest-dir", type=Path, default=HERE / "work" / "forest")
    parser.add_argument("--out-dir", type=Path, default=HERE / "work" / "extraction_ids")
    args = parser.parse_args()
    (args.out_dir / "comment_ids").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit = '2GB'")
    con.execute("SET TimeZone = 'UTC'")  # the dumps are split by UTC month
    members = f"read_parquet('{args.forest_dir / 'members' / '*.parquet'}')"
    discussions = f"read_parquet('{args.forest_dir / 'discussions' / '*.parquet'}')"

    manifest = []
    months = con.execute(f"""
        SELECT strftime(to_timestamp(created_utc), '%Y-%m') AS month, list(DISTINCT id ORDER BY id)
        FROM {members} GROUP BY month ORDER BY month""").fetchall()
    for month, ids in months:
        name = f"comment_ids/RC_{month}.txt"
        (args.out_dir / name).write_text("".join(to_base36(i) + "\n" for i in ids))
        manifest.append((name, len(ids)))
    (links,) = con.execute(f"SELECT list(DISTINCT link_id ORDER BY link_id) FROM {discussions}").fetchone()
    (args.out_dir / "submission_ids.txt").write_text("".join(to_base36(i) + "\n" for i in links))
    manifest.append(("submission_ids.txt", len(links)))
    (args.out_dir / "manifest.tsv").write_text(
        "file\tids\n" + "".join(f"{name}\t{count}\n" for name, count in manifest))
    print(f"{sum(c for n, c in manifest if n.startswith('comment'))} comment ids in {len(months)} months "
          f"({months[0][0]} to {months[-1][0]}), {len(links)} submission ids")


if __name__ == "__main__":
    main()
