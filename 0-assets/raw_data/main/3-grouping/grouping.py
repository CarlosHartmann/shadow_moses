"""Split filtered Reddit comments into demographic and political groups."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE.parent / "2-filtering" / "filtered_comments.jsonl"
DEFAULT_OUTPUT_DIR = HERE
STATISTICS_START_YEAR = 2010
MINIMUM_QUARTERLY_COMMENTS = 100
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_CELL_CHARS = 32_767
PROGRESS_EVERY = 1_000_000
RUNTIME_LOG_NAME = "grouping_runtime.log"
GROUPED_FLAIRS_NAME = "grouped_flairs.xlsx"
FLAIR_COLUMNS = ["subreddit", "flair", "relevant"]
DEFAULT_RELEVANT = "X"
# age_young keeps its own FLAIR_RULES; every other group follows grouped_flairs.xlsx.
FLAIR_RELEVANCE_GROUPS = frozenset(
    {
        "age_old",
        "gender_male",
        "gender_female_or_diverse",
        "political_conservative",
        "political_progressive",
        "gender_political_conservative",
        "gender_political_progressive",
    }
)

GROUP_SUBREDDITS = {
    "age_young": {
        "askredditteenagers",
        "askteengirls",
        "askteenboys",
        "bisexualteens",
        "highschool",
        "indianteenagers",
        "indianteens",
        "lgbteens",
        "mtfteens",
        "teenager",
        "teenagerschat",
        "teenagerscirclejerk",
        "teenagerssupportteens",
        "teenagersbutcool",
        "teenagersbutpog",
        "teenagers",
        "teenagersnew",
        "teenagenation",
        "teenrelationships",
        "teensdaily",
        "tallteenagers",
        "youngadults",
        "youngpeoplereddit",
        "genz",
    },
    "age_old": {
        "askoldpeople",
        "over30reddit",
        "askmenover30",
        "askmenover40",
        "50something",
        "genx",
        "overfifty",
        "daddit",
        "askgaybrosover30",
        "40something",
        "askwomenover30",
        "ftmover30",
        "makefriendsover30",
        "relationshipsover35",
        "wellnessover30",
        "olderlesbians",
        "retirement",
    },
    "gender_male": {
        "askmen",
        "askmenover30",
        "askgaymen",
        "askgaybrosover30",
        "askmenadvice",
        "asianmasculinity",
        "bisexualmen",
        "divorce_men",
        "ftmmen",
        "gaymen",
        "leftwingmaleadvocates",
        "maledatingstrategy",
        "malefashionadvice",
        "malegrooming",
        "malementalhealth",
        "men2men",
        "menslib",
        "mensrights",
        "askgaybros",
        "gaybros",
        "truegaymen",
    },
    "gender_female_or_diverse": {
        "askwomen",
        "twoxchromosomes",
        "actualwomen",
        "askfeminists",
        "asklesbians",
        "asklgbt",
        "askwomenadvice",
        "autisminwomen",
        "blacklgbt",
        "blackwomens",
        "femaleaccountability",
        "femaledatingstrategy",
        "femalelevelupstrategy",
        "femalefashionadvice",
        "foreveralonewomen",
        "godlesswomen",
        "lgbt_muslims",
        "menopause",
        "srswomen",
        "womenofcolor",
        "womenshealth",
        "women",
        "adhdwomen",
        "agender",
        "askalesbian",
        "actuellesbians",
        "bigender",
        "genderqueer",
        "lesbiangamers",
        "lesbians",
        "trans",
        "transsupport",
        "transgender",
        "honesttransgender",
        "asktransgender",
    },
    "political_conservative": {
        "conservative",
        "apexconservative",
        "asktrumpsupporters",
        "conservativekiwi",
        "conservativenewsweb",
        "nevertrump",
        "ohioconservatives",
        "republican",
        "right_wing_politics",
        "rightlibertarian",
        "true_aska_conservative",
        "askaconservative",
        "republicans",
        "askconservatives",
    },
    "political_progressive": {
        "socialism",
        "politics",
        "askaliberal",
        "askdemocrats",
        "ask_politics",
        "askpolitics",
        "americanpolitics",
        "centerleftpolitics",
        "democrat",
        "democraticsocialism",
        "democraticparty",
        "democrats",
        "justicedemocrats",
        "kossacks_for_sanders",
        "liberal",
        "liberalgunowners",
        "newyorkforsanders",
        "politicsdebate",
        "progressive",
        "socialistra",
        "socialist_",
        "socialism_101",
        "uspolitics",
        "unionsforsanders",
        "sandersforpresident",
    },
    "gender_political_conservative": {
        "gendercritical",
        "jordanpeterson",
        "jbpforwomen",
        "superstraight",
        "lesbiangang",
        "lesbianactually",
        "detrains",
        "tradwives",
        "gendercriticalguys",
        "maledatingstrategy",
        "marriedredpill",
        "redpillparenting",
        "redpillretention",
        "redpillwives",
        "redpillwomen",
        "rightwinglgbt",
        "womenfortrump",
        "antifeminists",
        "theredpill",
    },
    "gender_political_progressive": {
        "asktransgender",
        "feminism",
        "feminismuncensored",
        "gendercynical",
        "menslib",
        "pinkpillfeminism",
        "srsfeminism",
        "transpositive",
        "transspace",
        "transytalk",
        "lgbtaww",
        "lgbtmemes",
        "lgbtnews",
        "lgbt",
    },
}
SCORE_FILTERED_GROUPS = {
    "political_conservative",
    "political_progressive",
    "gender_political_conservative",
    "gender_political_progressive",
}
# (group, subreddit) -> author_flair_text values that qualify; enforced only from FLAIR_START.
FLAIR_RULES = {
    ("age_young", "teenagers"): {"13", "14", "15", "16", "17", "18", "19"},
}
FLAIR_START = datetime(2012, 1, 1, tzinfo=timezone.utc).timestamp()
ALL_TARGET_SUBREDDITS = set().union(*GROUP_SUBREDDITS.values())
GROUP_NAMES = tuple(GROUP_SUBREDDITS)


def iter_comments(path: Path) -> Iterator[tuple[int, str, dict]]:
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                comment = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON on line {line_number}: {error}") from error
            if not isinstance(comment, dict):
                raise ValueError(f"Expected a JSON object on line {line_number}.")
            yield line_number, line if line.endswith("\n") else line + "\n", comment


def quarter_for(comment: dict, line_number: int) -> str:
    try:
        timestamp = float(comment["created_utc"])
        created_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
        raise ValueError(f"Missing or invalid created_utc on line {line_number}.") from error
    quarter = (created_at.month - 1) // 3 + 1
    return f"{created_at.year}-Q{quarter}"


def matching_groups(comment: dict) -> list[str]:
    subreddit = str(comment.get("subreddit", "")).casefold()
    groups = [
        group
        for group, subreddits in GROUP_SUBREDDITS.items()
        if subreddit in subreddits
    ]

    if float(comment["created_utc"]) >= FLAIR_START:
        flair = str(comment.get("author_flair_text") or "").strip()
        groups = [
            group
            for group in groups
            if (group, subreddit) not in FLAIR_RULES
            or flair in FLAIR_RULES[(group, subreddit)]
        ]

    try:
        score_qualifies = float(comment.get("score", 0)) >= 5
    except (TypeError, ValueError):
        score_qualifies = False
    return [
        group
        for group in groups
        if group not in SCORE_FILTERED_GROUPS or score_qualifies
    ]


def excel_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)[:EXCEL_MAX_CELL_CHARS]
    return value


def quarter_sequence(first: str, last: str) -> list[str]:
    year, quarter = map(int, first.replace("-Q", " ").split())
    last_year, last_quarter = map(int, last.replace("-Q", " ").split())
    quarters = []
    while (year, quarter) <= (last_year, last_quarter):
        quarters.append(f"{year}-Q{quarter}")
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return quarters


def write_statistics(
    output_path: Path, quarters: list[str], counts: dict, run_info: list[tuple[str, str]]
) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Quarterly counts")
    sheet.append(["quarter", *GROUP_NAMES])
    for quarter in quarters:
        sheet.append([quarter, *(counts[quarter][group] for group in GROUP_NAMES)])
    info_sheet = workbook.create_sheet("Run info")
    for row in run_info:
        info_sheet.append(list(row))
    workbook.save(output_path)


def flair_of(comment: dict) -> str:
    return excel_value(str(comment.get("author_flair_text") or "").strip())


def update_grouped_flairs(
    path: Path, combos: dict[str, set[tuple[str, str]]]
) -> dict[str, int]:
    """Create grouped_flairs.xlsx or append unseen combinations, keeping existing 'relevant' values."""
    if path.exists():
        workbook = load_workbook(path)
    else:
        workbook = Workbook()
        del workbook[workbook.sheetnames[0]]

    added = {}
    for group in GROUP_NAMES:
        if group in workbook.sheetnames:
            sheet = workbook[group]
        else:
            sheet = workbook.create_sheet(group)
            sheet.append(FLAIR_COLUMNS)
        known = {
            (str(row[0] or ""), str(row[1] or ""))
            for row in sheet.iter_rows(min_row=2, max_col=2, values_only=True)
        }
        new_combos = sorted(combos[group] - known)
        for subreddit, flair in new_combos:
            sheet.append([subreddit, flair, DEFAULT_RELEVANT])
        added[group] = len(new_combos)
    workbook.save(path)
    return added


def load_relevant_flairs(path: Path) -> dict[str, set[tuple[str, str]]] | None:
    """Return the (subreddit, flair) pairs with a non-empty 'relevant' cell, or None without a file."""
    if not path.exists():
        return None
    workbook = load_workbook(path, read_only=True)
    relevant: dict[str, set[tuple[str, str]]] = {group: set() for group in GROUP_NAMES}
    for group in GROUP_NAMES:
        if group not in workbook.sheetnames:
            continue
        for row in workbook[group].iter_rows(min_row=2, max_col=3, values_only=True):
            if str(row[2] or "").strip():
                relevant[group].add((str(row[0] or ""), str(row[1] or "")))
    workbook.close()
    return relevant


def format_duration(seconds: float) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def write_group_workbook(spill_path: Path, columns: list[str], output_path: Path) -> int:
    """Stream a spill file into XLSX, starting a new sheet at Excel's row limit."""
    workbook = Workbook(write_only=True)
    sheet = None
    rows_in_sheet = 0
    sheet_count = 0
    total_rows = 0
    with spill_path.open("r", encoding="utf-8") as spill_file:
        for line in spill_file:
            if sheet is None or rows_in_sheet >= EXCEL_MAX_ROWS:
                sheet_count += 1
                sheet = workbook.create_sheet(f"Comments_{sheet_count}")
                sheet.append(columns)
                rows_in_sheet = 1
            comment = json.loads(line)
            sheet.append([excel_value(comment.get(column)) for column in columns])
            rows_in_sheet += 1
            total_rows += 1
    if sheet is None:
        workbook.create_sheet("Comments_1").append(columns)
    workbook.save(output_path)
    return total_rows


def group_comments(input_path: Path, output_dir: Path) -> None:
    run_started = time.monotonic()
    run_started_at = datetime.now().isoformat(timespec="seconds")
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = output_dir / "baseline.jsonl"
    spill_paths = {group: output_dir / f".{group}.spill.jsonl" for group in GROUP_NAMES}
    columns: dict[str, set[str]] = {group: set() for group in GROUP_NAMES}
    counts = defaultdict(lambda: defaultdict(int))
    flair_combos: dict[str, set[tuple[str, str]]] = {group: set() for group in GROUP_NAMES}
    last_quarter = None
    total_comments = 0

    print(f"Reading {input_path}", flush=True)
    relevant_flairs = load_relevant_flairs(output_dir / GROUPED_FLAIRS_NAME)
    if relevant_flairs is None:
        print(f"No {GROUPED_FLAIRS_NAME} yet; flair relevance is not applied.", flush=True)
    try:
        with ExitStack() as stack:
            baseline_file = stack.enter_context(baseline_path.open("w", encoding="utf-8"))
            spill_files = {
                group: stack.enter_context(path.open("w", encoding="utf-8"))
                for group, path in spill_paths.items()
            }
            for line_number, line, comment in iter_comments(input_path):
                total_comments += 1
                quarter = quarter_for(comment, line_number)
                if last_quarter is None or quarter > last_quarter:
                    last_quarter = quarter
                for group in matching_groups(comment):
                    subreddit = str(comment.get("subreddit", "")).casefold()
                    pair = (subreddit, flair_of(comment))
                    flair_combos[group].add(pair)
                    if (
                        relevant_flairs is not None
                        and group in FLAIR_RELEVANCE_GROUPS
                        and float(comment["created_utc"]) >= FLAIR_START
                        and pair not in relevant_flairs[group]
                    ):
                        continue
                    spill_files[group].write(line)
                    columns[group].update(comment)
                    if int(quarter[:4]) >= STATISTICS_START_YEAR:
                        counts[quarter][group] += 1
                subreddit = str(comment.get("subreddit", "")).casefold()
                if subreddit not in ALL_TARGET_SUBREDDITS:
                    baseline_file.write(line)
                if total_comments % PROGRESS_EVERY == 0:
                    elapsed = time.monotonic() - run_started
                    print(
                        f"  {total_comments:,} comments, {format_duration(elapsed)} elapsed, "
                        f"{total_comments / elapsed:,.0f} comments/s",
                        flush=True,
                    )
        pass_seconds = time.monotonic() - run_started
        print(
            f"Pass over input finished: {total_comments:,} comments in "
            f"{format_duration(pass_seconds)}",
            flush=True,
        )

        quarters = (
            quarter_sequence(f"{STATISTICS_START_YEAR}-Q1", last_quarter)
            if last_quarter and int(last_quarter[:4]) >= STATISTICS_START_YEAR
            else []
        )
        flairs_path = output_dir / GROUPED_FLAIRS_NAME
        flairs_existed = flairs_path.exists()
        added = update_grouped_flairs(flairs_path, flair_combos)
        print(
            f"{GROUPED_FLAIRS_NAME} {'updated' if flairs_existed else 'created'}: "
            + ", ".join(f"{group}={count:,} new" for group, count in added.items()),
            flush=True,
        )
        statistics_path = output_dir / "quarterly_statistics.xlsx"

        xlsx_started = time.monotonic()
        for group in GROUP_NAMES:
            group_started = time.monotonic()
            rows = write_group_workbook(
                spill_paths[group], sorted(columns[group]), output_dir / f"{group}.xlsx"
            )
            print(
                f"  {group}.xlsx: {rows:,} comments in "
                f"{format_duration(time.monotonic() - group_started)}",
                flush=True,
            )
        xlsx_seconds = time.monotonic() - xlsx_started
    finally:
        for path in spill_paths.values():
            path.unlink(missing_ok=True)

    print("Quarterly comment counts:")
    print("quarter\t" + "\t".join(GROUP_NAMES))
    for quarter in quarters:
        quarter_counts = [counts[quarter][group] for group in GROUP_NAMES]
        print(quarter + "\t" + "\t".join(map(str, quarter_counts)))
        for group, count in zip(GROUP_NAMES, quarter_counts):
            if count < MINIMUM_QUARTERLY_COMMENTS:
                print(
                    f"WARNING: {quarter} has {count} comments in {group} "
                    f"(fewer than {MINIMUM_QUARTERLY_COMMENTS})."
                )
    print(f"Baseline comments -> {baseline_path}")
    print(f"Quarterly statistics -> {statistics_path}")
    print(f"Group workbooks -> {output_dir}")

    total_seconds = time.monotonic() - run_started
    run_info = [
        ("run started", run_started_at),
        ("input", str(input_path)),
        ("comments processed", f"{total_comments:,}"),
        ("input pass", format_duration(pass_seconds)),
        ("xlsx writing", format_duration(xlsx_seconds)),
        ("total runtime", format_duration(total_seconds)),
    ]
    write_statistics(statistics_path, quarters, counts, run_info)
    print(f"Total runtime: {format_duration(total_seconds)}")
    with (output_dir / RUNTIME_LOG_NAME).open("a", encoding="utf-8") as log_file:
        log_file.write(
            f"{datetime.now().isoformat(timespec='seconds')} | input={input_path} | "
            f"comments={total_comments} | pass={format_duration(pass_seconds)} | "
            f"xlsx={format_duration(xlsx_seconds)} | total={format_duration(total_seconds)}\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    group_comments(args.input, args.output_dir)


if __name__ == "__main__":
    main()