"""Step 10, part 1: the users of the discussions and what their flairs say about their groups.

The study users were found through comments that contain "OP" (steps 2-4). The discussions
(step 8) also contain comments by other people, who may refer to OP without writing "OP". If
their flairs place them in one of the study groups, their language is data too.

For every author whose comments appear in the rendered discussions (comment_ids of
../08-full-contexts/programmatic/work/discussions/), this script looks at all their extracted
comments (../08-full-contexts/programmatic/work/texts/RC/) in the group subreddits and applies the
rules of step 3a (../03a-grouping/grouping.py: subreddit lists, score filter, relevant flairs on
grouped_flairs.xlsx, the pre-2012 high-likelihood rule, user_group_overrides) and, for age, the
birth-year deduction of step 3b (../03b-age-grouping/deduce_birthyear.py).

Authors, by kind:
- additional: not a study user (no study comment), not a bot account;
- baseline: a study user whose study comments are all baseline (reported, not moved);
- study users of the groups and bot accounts (../08-full-contexts/programmatic/work/bots/) are
  left out.

Outputs:
- new_flairs.xlsx: the subreddit and flair combinations of additional and baseline users that
  grouped_flairs.xlsx does not have yet, one sheet per group with the same columns (to annotate),
  plus users, comments and an example comment link.
- baseline_users_with_group_flairs.tsv: baseline users whose flairs admit them to a group.
- data/discussion_users.parquet (gitignored): one row per additional or baseline author with the
  number of their comments in the discussions, the groups their flairs admit them to, the groups
  for which they only have flairs not annotated yet, and the birth-year ranges of their age flairs.
- data/admitted_comments.parquet (gitignored): the flaired comments that admit them (id, author,
  group, birth-year range for age).
- summary.txt: the counts.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
sys.path.insert(0, str(MAIN / "03a-grouping"))
sys.path.insert(0, str(MAIN / "03b-age-grouping"))
import deduce_birthyear as birthyear  # noqa: E402
import grouping  # noqa: E402

WORK = MAIN / "08-full-contexts" / "programmatic" / "work"
FAMILIES = {  # groups that exclude each other for one user
    "gender": ("gender_male", "gender_female"),
    "political": ("political_conservative", "political_progressive"),
    "gender_political": ("gender_political_conservative", "gender_political_progressive"),
}
EXTRA_COLUMNS = ["users", "comments", "example"]


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def to_base36(n: int) -> str:
    digits, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while n:
        n, digit = divmod(n, 36)
        out = digits[digit] + out
    return out or "0"


def sheet_columns(path: Path) -> dict[str, list[str]]:
    """The annotation columns of each sheet of grouped_flairs.xlsx, in its own order."""
    source = load_workbook(path, read_only=True)
    columns = {name: [str(c) for c in next(source[name].iter_rows(max_row=1, values_only=True)) if c]
               for name in source.sheetnames}
    source.close()
    return columns


def write_new_flairs(path: Path, new_pairs: dict, examples: dict, columns_of: dict[str, list[str]]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for group in grouping.GROUP_NAMES:
        if group == "baseline":
            continue
        sheet = workbook.create_sheet(group)
        columns = columns_of.get(group) or list(grouping.FLAIR_COLUMNS)
        sheet.append(columns + EXTRA_COLUMNS)
        blanks = [None] * (len(columns) - 2)
        ranked = sorted(new_pairs[group].items(), key=lambda item: (-len(item[1]["users"]), item[0]))
        for (subreddit, flair), seen in ranked:
            sheet.append([subreddit, flair, *blanks, len(seen["users"]), seen["comments"], examples[(subreddit, flair)]])
        sheet.freeze_panes = "C2"
        for index, width in enumerate([22, 40] + [14] * (len(columns) - 2) + [8, 10, 60], start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width
    workbook.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=WORK)
    parser.add_argument("--out-dir", type=Path, default=HERE)
    parser.add_argument("--memory-limit", default="6GB")
    args = parser.parse_args()
    w, out = args.work_dir, args.out_dir
    (out / "data").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute(f"SET temp_directory = '{w / 'duckdb_tmp'}'")
    texts = f"read_parquet('{w / 'texts' / 'RC' / '*.parquet'}')"

    log("study users, bots and the comments shown in the discussions")
    con.execute(f"""CREATE TABLE study AS
        SELECT lower(r.author) AS author, bool_and(s.groups = 'baseline') AS baseline_only
        FROM read_parquet('{w / 'seeds.parquet'}') s JOIN {texts} r ON r.id = s.id GROUP BY 1""")
    con.execute(f"""CREATE TABLE bots AS SELECT lower(author) AS author
                    FROM read_csv('{w / 'bots' / 'bot_accounts.tsv'}', delim='\t', header=true)""")
    # comment_ids are "t1_" + base 36; the texts have the integer ids
    con.execute(f"""CREATE TABLE shown_ids AS
        SELECT DISTINCT list_reduce(list_transform(string_split(substr(cid, 4), ''),
                   c -> CAST(strpos('0123456789abcdefghijklmnopqrstuvwxyz', c) - 1 AS BIGINT)),
                   (acc, d) -> acc * 36 + d) AS id
        FROM (SELECT unnest(comment_ids) AS cid FROM read_parquet('{w / 'discussions' / 'part-*.parquet'}'))""")
    con.execute(f"""CREATE TABLE authors AS
        SELECT lower(r.author) AS author, count(*) AS comments_shown
        FROM {texts} r JOIN shown_ids USING (id)
        WHERE r.author NOT IN ('', '[deleted]', '[removed]') GROUP BY 1""")
    con.execute("""CREATE TABLE candidates AS
        SELECT a.author, a.comments_shown,
               CASE WHEN s.author IS NULL THEN 'additional' ELSE 'baseline' END AS kind
        FROM authors a LEFT JOIN study s USING (author)
        WHERE a.author NOT IN (SELECT author FROM bots) AND (s.author IS NULL OR s.baseline_only)""")
    kinds = dict(con.execute("SELECT kind, count(*) FROM candidates GROUP BY 1").fetchall())
    log(f"{kinds.get('additional', 0):,} additional and {kinds.get('baseline', 0):,} baseline authors")

    con.execute("CREATE TABLE targets (subreddit VARCHAR)")
    con.executemany("INSERT INTO targets VALUES (?)", [(s,) for s in sorted(grouping.ALL_TARGET_SUBREDDITS)])
    rows = con.execute(f"""
        SELECT r.id, lower(r.author), r.subreddit, r.flair_text, r.score, r.created_utc, r.link_id
        FROM {texts} r JOIN candidates c ON c.author = lower(r.author)
        WHERE lower(r.subreddit) IN (SELECT subreddit FROM targets)""").fetchall()
    log(f"{len(rows):,} of their comments are in group subreddits")

    known, relevant = grouping.load_relevant_flairs(MAIN / "03a-grouping" / grouping.GROUPED_FLAIRS_NAME)
    exclusions, _inclusions = grouping.load_user_overrides(MAIN / "03a-grouping" / grouping.GROUPED_FLAIRS_NAME)
    annotations, _problems = birthyear.load_annotations(birthyear.GROUPED_FLAIRS_PATH)
    age_overrides = birthyear.load_user_overrides(birthyear.GROUPED_FLAIRS_PATH)

    admitted = defaultdict(set)      # author -> groups
    pending = defaultdict(set)       # author -> groups with only not yet annotated flairs
    ranges = defaultdict(set)        # author -> birth-year ranges from age flairs
    admitted_rows = []
    new_pairs = {group: defaultdict(lambda: {"users": set(), "comments": 0}) for group in grouping.GROUP_NAMES}
    examples = {}
    for cid, author, subreddit, flair_text, score, created, link in rows:
        comment = {"subreddit": subreddit, "score": score, "created_utc": created,
                   "author_flair_text": flair_text, "author": author}
        pair = (str(subreddit).casefold(), grouping.flair_of(comment))
        for group in grouping.matching_groups(comment):
            if group in exclusions.get(author, ()):
                continue
            if grouping.flair_admits(group, comment, pair, relevant):
                admitted[author].add(group)
                lo = hi = None
                if group == grouping.AGE_GROUP:
                    annotation = annotations.get(pair)
                    result = age_overrides.get(author) or (
                        birthyear.birth_range_for(annotation, birthyear.flair_year(comment)) if annotation else None)
                    if result:
                        lo, hi = result
                        ranges[author].add(f"{lo}-{hi}")
                admitted_rows.append({"id": cid, "author": author, "group": group, "birth_lo": lo, "birth_hi": hi})
            elif pair[1] and pair not in known[group]:
                pending[author].add(group)
                seen = new_pairs[group][pair]
                seen["users"].add(author)
                seen["comments"] += 1
                examples.setdefault(pair, f"https://www.reddit.com/comments/{to_base36(link)}/_/{to_base36(cid)}/")

    log("writing the outputs")
    write_new_flairs(out / "new_flairs.xlsx", new_pairs, examples,
                     sheet_columns(MAIN / "03a-grouping" / grouping.GROUPED_FLAIRS_NAME))
    users = con.execute("SELECT author, kind, comments_shown FROM candidates").fetchall()
    table = []
    for author, kind, shown in users:
        groups = sorted(admitted.get(author, ()))
        opposing = sorted(f for f, pair in FAMILIES.items() if set(pair) <= set(groups))
        table.append({"author": author, "kind": kind, "comments_shown": shown, "admitted_groups": groups,
                      "pending_groups": sorted(pending.get(author, set()) - set(groups)),
                      "opposing_families": opposing, "birth_ranges": sorted(ranges.get(author, ()))})
    pq.write_table(pa.Table.from_pylist(table), out / "data" / "discussion_users.parquet", compression="zstd")
    pq.write_table(pa.Table.from_pylist(admitted_rows, schema=pa.schema([
        ("id", pa.int64()), ("author", pa.string()), ("group", pa.string()),
        ("birth_lo", pa.int32()), ("birth_hi", pa.int32())])), out / "data" / "admitted_comments.parquet")
    with open(out / "baseline_users_with_group_flairs.tsv", "w") as handle:
        handle.write("author\tadmitted_groups\tcomments_shown\n")
        for row in sorted((r for r in table if r["kind"] == "baseline" and r["admitted_groups"]),
                          key=lambda r: -r["comments_shown"]):
            handle.write(f"{row['author']}\t{','.join(row['admitted_groups'])}\t{row['comments_shown']}\n")

    lines = [f"Authors in the discussions: {kinds.get('additional', 0):,} additional, {kinds.get('baseline', 0):,} baseline "
             f"(study users of the groups and bot accounts left out).",
             f"Their comments in group subreddits: {len(rows):,}.", "",
             "group\tadditional users admitted\tbaseline users admitted\tusers with only new flairs\tnew flair combinations"]
    for group in grouping.GROUP_NAMES:
        if group == "baseline":
            continue
        by_kind = Counter(r["kind"] for r in table if group in r["admitted_groups"])
        only_new = sum(1 for r in table if group in r["pending_groups"])
        lines.append(f"{group}\t{by_kind['additional']:,}\t{by_kind['baseline']:,}\t{only_new:,}\t{len(new_pairs[group]):,}")
    any_admitted = Counter(r["kind"] for r in table if r["admitted_groups"])
    all_new = set().union(*(set(p) for p in new_pairs.values()))
    lines += ["", f"Admitted to at least one group: {any_admitted['additional']:,} additional, {any_admitted['baseline']:,} baseline.",
              f"Admitted to opposing groups of one family: {sum(1 for r in table if r['opposing_families']):,}.",
              f"Age users without a deducible birth year: "
              f"{sum(1 for r in table if 'age' in r['admitted_groups'] and not r['birth_ranges']):,}.",
              f"Distinct new subreddit and flair combinations: {len(all_new):,} in {len({s for s, _ in all_new})} subreddits."]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
