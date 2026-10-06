"""Split filtered Reddit comments into demographic and political groups."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE.parent / "2-filtering" / "filtered_comments.jsonl"
DEFAULT_OUTPUT_DIR = HERE
STATISTICS_START_YEAR = 2010
MINIMUM_ANNUAL_COMMENTS = 100

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
        "askconservatives",
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
        "neutralpolitics",
        "politicsdebate",
        "progressive",
        "socialistra",
        "socialist_",
        "socialism_101",
        "uspolitics",
        "unionsforsanders",
    },
    "political_progressive": {"socialism", "politics", "sandersforpresident"},
    "gender_political_conservative": {
        "gendercritical",
        "jordanpeterson",
        "jbpforwomen",
        "superstraight",
        "lesbiangang",
        "lesbianactually",
        "detrains",
        "tradwives",
        "apexconservative",
        "asktrumpsupporters",
        "conservativekiwi",
        "conservativenewsweb",
        "nevertrump",
        "ohioconservatives",
        "republican",
        "right_wing_politics",
        "rightlibertarian",
        "true_ask_a_conservative",
        "askaconservative",
        "republicans",
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
AGE_YOUNG_FLAIRS = {"13", "14", "15", "16", "17", "18", "19"}
ALL_TARGET_SUBREDDITS = set().union(*GROUP_SUBREDDITS.values())
GROUP_NAMES = tuple(GROUP_SUBREDDITS)


def iter_comments(path: Path) -> Iterator[tuple[int, dict]]:
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
            yield line_number, comment


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

    if subreddit == "teenagers":
        flair = str(comment.get("author_flair_text") or "").strip()
        if flair not in AGE_YOUNG_FLAIRS:
            groups.remove("age_young")

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
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def quarter_sequence(first: str, last: str) -> list[str]:
    year, quarter = map(int, first.replace("-Q", " ").split())
    last_year, last_quarter = map(int, last.replace("-Q", " ").split())
    quarters = []
    while (year, quarter) <= (last_year, last_quarter):
        quarters.append(f"{year}-Q{quarter}")
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return quarters


def write_statistics(output_path: Path, quarters: list[str], counts: dict) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Quarterly counts")
    sheet.append(["quarter", *GROUP_NAMES])
    for quarter in quarters:
        sheet.append([quarter, *(counts[quarter][group] for group in GROUP_NAMES)])
    workbook.save(output_path)


def group_comments(input_path: Path, output_dir: Path) -> None:
    field_names: set[str] = set()
    last_quarter = None
    for line_number, comment in iter_comments(input_path):
        field_names.update(comment)
        quarter = quarter_for(comment, line_number)
        last_quarter = quarter if last_quarter is None else max(last_quarter, quarter)

    output_dir.mkdir(parents=True, exist_ok=True)
    columns = sorted(field_names)
    workbooks = {}
    sheets = {}
    for group in GROUP_NAMES:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Comments")
        sheet.append(columns)
        workbooks[group] = workbook
        sheets[group] = sheet

    quarters = (
        quarter_sequence(f"{STATISTICS_START_YEAR}-Q1", last_quarter)
        if last_quarter and int(last_quarter[:4]) >= STATISTICS_START_YEAR
        else []
    )
    counts = defaultdict(lambda: defaultdict(int))
    annual_counts = defaultdict(lambda: defaultdict(int))
    baseline_path = output_dir / "baseline.jsonl"
    try:
        with baseline_path.open("w", encoding="utf-8") as baseline_file:
            for line_number, comment in iter_comments(input_path):
                quarter = quarter_for(comment, line_number)
                matches = matching_groups(comment)
                for group in matches:
                    sheets[group].append(
                        [excel_value(comment.get(column)) for column in columns]
                    )
                    year = int(quarter[:4])
                    if year >= STATISTICS_START_YEAR:
                        counts[quarter][group] += 1
                        annual_counts[year][group] += 1
                subreddit = str(comment.get("subreddit", "")).casefold()
                if subreddit not in ALL_TARGET_SUBREDDITS:
                    baseline_file.write(json.dumps(comment, ensure_ascii=False) + "\n")

        for group, workbook in workbooks.items():
            workbook.save(output_dir / f"{group}.xlsx")
        statistics_path = output_dir / "quarterly_statistics.xlsx"
        write_statistics(statistics_path, quarters, counts)
    finally:
        for workbook in workbooks.values():
            workbook.close()

    print("Quarterly comment counts:")
    print("quarter\t" + "\t".join(GROUP_NAMES))
    for quarter in quarters:
        quarter_counts = [counts[quarter][group] for group in GROUP_NAMES]
        print(quarter + "\t" + "\t".join(map(str, quarter_counts)))

    if last_quarter and int(last_quarter[:4]) >= STATISTICS_START_YEAR:
        last_year = int(last_quarter[:4])
        last_quarter_number = int(last_quarter[-1])
        last_full_year = last_year if last_quarter_number == 4 else last_year - 1
        for year in range(STATISTICS_START_YEAR, last_full_year + 1):
            for group in GROUP_NAMES:
                count = annual_counts[year][group]
                if count < MINIMUM_ANNUAL_COMMENTS:
                    print(
                        f"WARNING: {year} has {count} comments in {group} "
                        f"(fewer than {MINIMUM_ANNUAL_COMMENTS})."
                    )
    print(f"Baseline comments -> {baseline_path}")
    print(f"Quarterly statistics -> {statistics_path}")
    print(f"Group workbooks -> {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    group_comments(args.input, args.output_dir)


if __name__ == "__main__":
    main()