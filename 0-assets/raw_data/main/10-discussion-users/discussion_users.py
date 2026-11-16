"""Step 10: the users of the discussions, what their flairs say about their groups, and the final
group of every user.

The study users were found through comments that contain "OP" and carry a relevant flair (steps
2-4). The discussions (step 8) also contain comments by other people, and comments without "OP"
by baseline users. If their flairs place them in one of the study groups, their language is data
too, and a baseline user with such a flair does not belong in the baseline.

For every author whose comments appear in the rendered discussions (comment_ids of
../08-full-contexts/programmatic/work/discussions/), this script looks at all their extracted
comments (../08-full-contexts/programmatic/work/texts/RC/) in the group subreddits and applies the
rules of step 3a (../03a-grouping/grouping.py: subreddit lists, score filter, relevant flairs, the
pre-2012 high-likelihood rule, user_group_overrides) and, for age, the birth-year deduction of
step 3b (../03b-age-grouping/deduce_birthyear.py).

Flair annotations are read from two workbooks: ../03a-grouping/grouped_flairs.xlsx and
new_flairs.xlsx in this folder, which lists the subreddit and flair combinations that the first
one does not have. new_flairs.xlsx is annotated by hand, bit by bit; the script can be rerun at
any time (about 1 min), keeps every annotation and only refreshes the other columns.

Authors, by kind:
- additional: not a study user (no study comment), not a bot account;
- baseline: a study user whose study comments are all baseline;
- study users of the groups keep the groups of steps 3-5; bot accounts
  (../08-full-contexts/programmatic/work/bots/) are left out everywhere.

Final groups (user_groups):
- A user admitted to both groups of a family (male/female, political or gender-political
  conservative/progressive) is put in neither.
- Age: the user's birth-year ranges are intersected (highest first year, lowest last year); the
  generation and young/old follow as in step 3b. Without a common year the user gets no age group.
- A baseline user with a relevant group flair leaves the baseline, also when a conflict leaves
  them without a group.

Outputs:
- new_flairs.xlsx: one sheet per group, with the annotation columns of grouped_flairs.xlsx, then
  checked (fill in anything to mark a combination as looked at and not relevant), gain (see
  notes.txt; the sheet is sorted by it), users, comments, first_year and an example comment link.
- baseline_users_moved.tsv, conflicts.tsv, summary.txt.
- data/user_groups.parquet (gitignored): every study user and every admitted additional user
  with their final groups and how they were found (route).
- data/admitted_comments.parquet (gitignored): the flaired comments that admit users.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
import group_by_age  # noqa: E402
import group_by_generation  # noqa: E402
import grouping  # noqa: E402

WORK = MAIN / "08-full-contexts" / "programmatic" / "work"
GROUPED_FLAIRS = MAIN / "03a-grouping" / grouping.GROUPED_FLAIRS_NAME
NEW_FLAIRS_NAME = "new_flairs.xlsx"
FAMILIES = {  # groups that exclude each other for one user
    "gender": ("gender_male", "gender_female"),
    "political": ("political_conservative", "political_progressive"),
    "gender_political": ("gender_political_conservative", "gender_political_progressive"),
}
CHECKED = "checked"
EXTRA_COLUMNS = [CHECKED, "gain", "users", "comments", "first_year", "example"]
SHEET_GROUPS = tuple(group for group in grouping.GROUP_NAMES if group != "baseline")


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def to_base36(n: int) -> str:
    digits, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while n:
        n, digit = divmod(n, 36)
        out = digits[digit] + out
    return out or "0"


def sheet_group(study_group: str) -> str:
    """The sheet (step 3a group) of a study group: the age groups share the age sheet."""
    return grouping.AGE_GROUP if study_group.startswith("age_") else study_group


def sheet_columns(path: Path) -> dict[str, list[str]]:
    """The annotation columns of each sheet of grouped_flairs.xlsx, in its own order."""
    source = load_workbook(path, read_only=True)
    columns = {name: [str(c) for c in next(source[name].iter_rows(max_row=1, values_only=True)) if c]
               for name in source.sheetnames}
    source.close()
    return columns


def load_new_annotations(path: Path) -> dict[str, dict[tuple[str, str], dict]]:
    """group -> (subreddit, flair) -> the filled-in annotation and checked cells of new_flairs.xlsx."""
    kept: dict[str, dict[tuple[str, str], dict]] = {group: {} for group in SHEET_GROUPS}
    if not path.exists():
        return kept
    workbook = load_workbook(path, read_only=True)
    for group in SHEET_GROUPS:
        if group not in workbook.sheetnames:
            continue
        rows = workbook[group].iter_rows(values_only=True)
        header = [str(name or "") for name in next(rows, ())]
        stop = min((header.index(name) for name in EXTRA_COLUMNS[1:] if name in header), default=len(header))
        for row in rows:
            cells = {name: value for name, value in zip(header[2:stop], row[2:stop])
                     if value is not None and str(value).strip()}
            if cells:
                kept[group][(str(row[0] or ""), str(row[1] or ""))] = cells
    workbook.close()
    return kept


def is_relevant(group: str, cells: dict) -> bool:
    """As in grouping.load_relevant_flairs: any annotation makes a combination relevant."""
    if group == grouping.AGE_GROUP:
        return any(name in cells and (name == "given_generation"
                                      or str(cells[name]).strip().upper() != grouping.LEGACY_RELEVANT_MARKER)
                   for name in grouping.AGE_ANNOTATION_COLUMNS)
    return "relevant" in cells


def write_new_flairs(path: Path, rows_of: dict[str, list[dict]], columns_of: dict[str, list[str]]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for group in SHEET_GROUPS:
        sheet = workbook.create_sheet(group)
        columns = columns_of.get(group) or list(grouping.FLAIR_COLUMNS)
        sheet.append(columns + EXTRA_COLUMNS)
        for row in rows_of[group]:
            cells = row["cells"]
            sheet.append([row["pair"][0], row["pair"][1], *[cells.get(name) for name in columns[2:]],
                          cells.get(CHECKED), round(row["gain"], 3), row["users"], row["comments"],
                          row["first_year"], row["example"]])
        sheet.freeze_panes = "C2"
        widths = [22, 40] + [14] * (len(columns) - 2) + [9, 9, 7, 10, 10, 55]
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width
    temporary = path.with_name(f".{path.name}.tmp")
    workbook.save(temporary)
    temporary.rename(path)


def age_groups(ranges: set[tuple[int, int]]) -> tuple[list[str], tuple[int, int] | None]:
    """The age groups of a user from all their birth-year ranges, or none without a common year."""
    low, high = max(lo for lo, _ in ranges), min(hi for _, hi in ranges)
    if low > high:
        return [], None
    names = [group_by_generation.classify((low, high)), group_by_age.classify((low, high))]
    return [f"age_{name}" for name in names if name], (low, high)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=WORK)
    parser.add_argument("--out-dir", type=Path, default=HERE)
    parser.add_argument("--memory-limit", default="4GB")
    args = parser.parse_args()
    w, out = args.work_dir, args.out_dir
    (out / "data").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute(f"SET temp_directory = '{w / 'duckdb_tmp'}'")
    con.execute("SET TimeZone = 'UTC'")
    texts = f"read_parquet('{w / 'texts' / 'RC' / '*.parquet'}')"

    log("study users, bots and the comments shown in the discussions")
    con.execute(f"""CREATE TABLE bots AS SELECT lower(author) AS author
                    FROM read_csv('{w / 'bots' / 'bot_accounts.tsv'}', delim='\t', header=true)""")
    con.execute(f"""CREATE TABLE study_groups AS
        SELECT DISTINCT lower(r.author) AS author, unnest(string_split(s.groups, ',')) AS study_group
        FROM read_parquet('{w / 'seeds.parquet'}') s JOIN {texts} r ON r.id = s.id
        WHERE lower(r.author) NOT IN (SELECT author FROM bots)""")
    # comment_ids are "t1_" + base 36; the texts have the integer ids
    con.execute(f"""CREATE TABLE shown AS
        SELECT lower(r.author) AS author, year(to_timestamp(r.created_utc)) AS year, count(*) AS comments,
               count(*) FILTER (WHERE o.id IS NOT NULL) AS with_op
        FROM (SELECT DISTINCT list_reduce(list_transform(string_split(substr(cid, 4), ''),
                         c -> CAST(strpos('0123456789abcdefghijklmnopqrstuvwxyz', c) - 1 AS BIGINT)),
                         (acc, d) -> acc * 36 + d) AS id
              FROM (SELECT unnest(comment_ids) AS cid FROM read_parquet('{w / 'discussions' / 'part-*.parquet'}'))) i
        JOIN {texts} r USING (id)
        LEFT JOIN read_parquet('{w / 'op_ids.parquet'}') o USING (id)
        WHERE r.author NOT IN ('', '[deleted]', '[removed]') AND lower(r.author) NOT IN (SELECT author FROM bots)
        GROUP BY 1, 2""")
    con.execute("""CREATE TABLE candidates AS
        SELECT a.author, CASE WHEN s.author IS NULL THEN 'additional' ELSE 'baseline' END AS kind
        FROM (SELECT DISTINCT author FROM shown) a
        LEFT JOIN (SELECT author, bool_and(study_group = 'baseline') AS baseline_only
                   FROM study_groups GROUP BY author) s USING (author)
        WHERE s.author IS NULL OR s.baseline_only""")
    kinds = dict(con.execute("SELECT kind, count(*) FROM candidates GROUP BY 1").fetchall())
    log(f"{kinds.get('additional', 0):,} additional and {kinds.get('baseline', 0):,} baseline authors")

    con.execute("CREATE TABLE targets (subreddit VARCHAR)")
    con.executemany("INSERT INTO targets VALUES (?)", [(s,) for s in sorted(grouping.ALL_TARGET_SUBREDDITS)])
    rows = con.execute(f"""
        SELECT r.id, lower(r.author), r.subreddit, r.flair_text, r.score, r.created_utc, r.link_id
        FROM {texts} r JOIN candidates c ON c.author = lower(r.author)
        WHERE lower(r.subreddit) IN (SELECT subreddit FROM targets)""").fetchall()
    log(f"{len(rows):,} of their comments are in group subreddits")
    shown: dict[str, Counter] = defaultdict(Counter)  # author -> year -> comments shown
    with_op: Counter = Counter()
    for author, year, comments, mentions in con.execute("SELECT * FROM shown").fetchall():
        shown[author][year] = comments
        with_op[author] = with_op[author] + mentions
    # The yardstick for the gain: each group's study users with comments in the discussions, per year.
    volume: dict[str, Counter] = defaultdict(Counter)
    for study_group, year, users in con.execute("""
            SELECT g.study_group, s.year, count(DISTINCT author) FROM study_groups g JOIN shown s USING (author)
            WHERE g.study_group <> 'baseline' GROUP BY 1, 2""").fetchall():
        if study_group in ("age_young", "age_old"):  # the same users as the generations
            volume[grouping.AGE_GROUP][year] += users
        elif not study_group.startswith("age_"):
            volume[study_group][year] += users

    known, relevant = grouping.load_relevant_flairs(GROUPED_FLAIRS)
    exclusions, _inclusions = grouping.load_user_overrides(GROUPED_FLAIRS)
    annotations, _problems = birthyear.load_annotations(GROUPED_FLAIRS)
    age_overrides = birthyear.load_user_overrides(GROUPED_FLAIRS)
    new_path = out / NEW_FLAIRS_NAME
    kept = load_new_annotations(new_path)
    base_relevant = {group: set(pairs) for group, pairs in relevant.items()}
    for group in SHEET_GROUPS:
        relevant[group] |= {pair for pair, cells in kept[group].items()
                            if pair not in known[group] and is_relevant(group, cells)}
    if new_path.exists():
        new_age, _problems = birthyear.load_annotations(new_path)
        for pair, annotation in new_age.items():
            annotations.setdefault(pair, annotation)

    admitted = defaultdict(set)   # author -> step 3a groups, with all annotations
    ranges = defaultdict(set)     # author -> birth-year ranges from age flairs
    admitted_rows = []
    carriers = {group: defaultdict(set) for group in SHEET_GROUPS}  # new combination -> its users
    examples = {}
    for cid, author, subreddit, flair_text, score, created, link in rows:
        comment = {"subreddit": subreddit, "score": score, "created_utc": created,
                   "author_flair_text": flair_text, "author": author}
        pair = (str(subreddit).casefold(), grouping.flair_of(comment))
        for group in grouping.matching_groups(comment):
            if group in exclusions.get(author, ()):
                continue
            if pair[1] and pair not in known[group]:
                carriers[group][pair].add(author)
                examples.setdefault(pair, f"https://www.reddit.com/comments/{to_base36(link)}/_/{to_base36(cid)}/")
            if grouping.flair_admits(group, comment, pair, relevant):
                admitted[author].add(group)
                lo = hi = None
                if group == grouping.AGE_GROUP:
                    annotation = annotations.get(pair)
                    result = age_overrides.get(author) or (
                        birthyear.birth_range_for(annotation, birthyear.flair_year(comment)) if annotation else None)
                    if result:
                        lo, hi = result
                        ranges[author].add((lo, hi))
                admitted_rows.append({"id": cid, "author": author, "group": group, "birth_lo": lo, "birth_hi": hi,
                                      "by_new_flair": not grouping.flair_admits(group, comment, pair, base_relevant)})

    log("writing new_flairs.xlsx")
    rows_of = {}
    for group in SHEET_GROUPS:
        sheet_rows = []
        for pair in set(carriers[group]) | set(kept[group]):
            users = carriers[group].get(pair, set())
            years, active = Counter(), Counter()  # comments and users per year
            for author in users:
                years.update(shown[author])
                active.update(shown[author].keys())
            sheet_rows.append({
                "pair": pair, "cells": kept[group].get(pair, {}), "users": len(users),
                "comments": sum(years.values()), "first_year": min(years) if years else None,
                "gain": 100 * sum(count / max(volume[group][year], 1) for year, count in active.items()),
                "example": examples.get(pair)})
        sheet_rows.sort(key=lambda row: (-row["gain"], row["pair"]))
        rows_of[group] = sheet_rows
    if any(kept.values()):
        (out / "annotation_backup").mkdir(exist_ok=True)
        shutil.copy2(new_path, out / "annotation_backup" / f"new_flairs_{datetime.now():%Y-%m-%d_%Hh-%Mm-%Ss}.xlsx")
    write_new_flairs(new_path, rows_of, sheet_columns(GROUPED_FLAIRS))

    log("final groups")
    kind_of = dict(con.execute("SELECT author, kind FROM candidates").fetchall())
    final: dict[str, tuple[list[str], str, tuple[int, int] | None]] = {}
    conflicts, moved = [], []
    for author, groups in admitted.items():
        result = set(groups) - {grouping.AGE_GROUP}
        for family, members in FAMILIES.items():
            if set(members) <= result:
                result -= set(members)
                conflicts.append((author, kind_of[author], family, "admitted to both groups"))
        birth_range = None
        if grouping.AGE_GROUP in groups and ranges[author]:
            age, birth_range = age_groups(ranges[author])
            result |= set(age)
            if not age:
                spans = ", ".join(f"{lo}-{hi}" for lo, hi in sorted(ranges[author]))
                conflicts.append((author, kind_of[author], "age", f"no common birth year ({spans})"))
        if kind_of[author] == "baseline":
            moved.append((author, sorted(result), sum(shown[author].values())))
        if result:
            route = "baseline_moved" if kind_of[author] == "baseline" else "discussion"
            final[author] = (sorted(result), route, birth_range)
    moved_authors = {author for author, _groups, _comments in moved}
    study = defaultdict(set)
    for author, study_group in con.execute("SELECT author, study_group FROM study_groups").fetchall():
        study[author].add(study_group)
    for author, groups in study.items():
        if groups == {"baseline"}:
            if author not in moved_authors:
                final[author] = (["baseline"], "baseline", None)
        else:
            final[author] = (sorted(groups - {"baseline"}), "op_comment", None)
    table = [{"author": author, "groups": groups, "route": route,
              "birth_lo": birth[0] if birth else None, "birth_hi": birth[1] if birth else None,
              "comments_shown": sum(shown[author].values()), "comments_shown_with_op": with_op[author]}
             for author, (groups, route, birth) in sorted(final.items())]
    pq.write_table(pa.Table.from_pylist(table, schema=pa.schema([
        ("author", pa.string()), ("groups", pa.list_(pa.string())), ("route", pa.string()),
        ("birth_lo", pa.int32()), ("birth_hi", pa.int32()), ("comments_shown", pa.int64()),
        ("comments_shown_with_op", pa.int64())])), out / "data" / "user_groups.parquet", compression="zstd")
    pq.write_table(pa.Table.from_pylist(admitted_rows, schema=pa.schema([
        ("id", pa.int64()), ("author", pa.string()), ("group", pa.string()), ("birth_lo", pa.int32()),
        ("birth_hi", pa.int32()), ("by_new_flair", pa.bool_())])), out / "data" / "admitted_comments.parquet")
    with open(out / "baseline_users_moved.tsv", "w") as handle:
        handle.write("author\tgroups\tcomments_shown\n")
        for author, groups, comments in sorted(moved, key=lambda item: (-item[2], item[0])):
            handle.write(f"{author}\t{','.join(groups) or '(none)'}\t{comments}\n")
    with open(out / "conflicts.tsv", "w") as handle:
        handle.write("author\tkind\tfamily\tconflict\n")
        handle.writelines("\t".join(conflict) + "\n" for conflict in sorted(conflicts))

    lines = [f"Run of {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
             f"Authors in the discussions: {kinds.get('additional', 0):,} additional, {kinds.get('baseline', 0):,} baseline "
             f"(study users of the groups and bot accounts left out).",
             f"Their comments in group subreddits: {len(rows):,}.", "",
             "Final groups: users, and their comments shown in the discussions (of which with \"OP\")",
             "group\tfound through an OP comment\tmoved from the baseline\tfound in the discussions"]
    by_group: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for row in table:
        for group in row["groups"]:
            cell = by_group[group][row["route"]]
            cell[0] += 1
            cell[1] += row["comments_shown"]
            cell[2] += row["comments_shown_with_op"]
    def cell_text(cell: list[int]) -> str:
        return f"{cell[0]:,} users, {cell[1]:,} comments ({cell[2]:,})"
    for group in sorted(by_group):
        routes = by_group[group]
        if group == "baseline":
            lines.append(f"baseline\t{cell_text(routes['baseline'])}\t-\t-")
        else:
            lines.append(f"{group}\t{cell_text(routes['op_comment'])}\t{cell_text(routes['baseline_moved'])}\t"
                         f"{cell_text(routes['discussion'])}")
    lines += ["", f"Baseline users moved out of the baseline: {len(moved):,} "
                  f"({sum(1 for _a, groups, _c in moved if not groups):,} of them into no group: a conflict or no birth year).",
              f"Conflicts: {len(conflicts):,} (conflicts.tsv).", "",
              "Comments shown in the discussions by users of the groups, per year",
              "year\tfound through an OP comment\tadded by this step\tincrease"]
    per_year: dict[int, Counter] = defaultdict(Counter)
    for row in table:
        if row["route"] != "baseline":
            for year, count in shown[row["author"]].items():
                per_year[year]["old" if row["route"] == "op_comment" else "new"] += count
    for year in sorted(per_year):
        old, new = per_year[year]["old"], per_year[year]["new"]
        lines.append(f"{year}\t{old:,}\t{new:,}\t{100 * new / max(old, 1):.1f}%")
    lines += ["", "Annotation of new_flairs.xlsx",
              "sheet\tcombinations\trelevant\tchecked, not relevant\tannotated without a gap down to gain"]
    for group in SHEET_GROUPS:
        states = ["relevant" if is_relevant(group, row["cells"]) else "checked" if row["cells"] else None
                  for row in rows_of[group]]
        first_open = next((i for i, state in enumerate(states) if state is None), len(states))
        frontier = f"{rows_of[group][first_open - 1]['gain']:.3f} (first {first_open:,} rows)" if first_open else "-"
        lines.append(f"{group}\t{len(states):,}\t{states.count('relevant'):,}\t{states.count('checked'):,}\t{frontier}")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
