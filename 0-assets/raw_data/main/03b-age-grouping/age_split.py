"""Shared helpers for splitting the age comments by their deduced birth year range."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "age.jsonl"
DEFAULT_OUTPUT_DIR = HERE
RANGE_FIELD = "deduced_birthyear_range"
UNASSIGNED = "unassigned"
STATISTICS_START_YEAR = 2010

# (name, first birth year, last birth year), oldest first.
Bucket = tuple[str, int, int]


def majority_bucket(
    birth_range: Sequence[int] | None, buckets: Sequence[Bucket]
) -> str | None:
    """Bucket containing most years of the range; ties go to the older bucket."""
    if not birth_range:
        return None
    low, high = sorted(birth_range)
    best_name, best_overlap = None, 0
    for name, first, last in buckets:
        overlap = min(high, last) - max(low, first) + 1
        if overlap > best_overlap:
            best_name, best_overlap = name, overlap
    return best_name


def quarter_for(comment: dict) -> str:
    created_at = datetime.fromtimestamp(float(comment["created_utc"]), tz=timezone.utc)
    return f"{created_at.year}-Q{(created_at.month - 1) // 3 + 1}"


def quarter_sequence(first: str, last: str) -> list[str]:
    year, quarter = map(int, first.replace("-Q", " ").split())
    last_year, last_quarter = map(int, last.replace("-Q", " ").split())
    quarters = []
    while (year, quarter) <= (last_year, last_quarter):
        quarters.append(f"{year}-Q{quarter}")
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return quarters


def write_statistics(
    path: Path,
    group_names: Sequence[str],
    counts: dict[str, dict[str, int]],
    run_info: list[tuple[str, str]],
) -> None:
    last_quarter = max(counts, default=None)
    quarters = (
        quarter_sequence(f"{STATISTICS_START_YEAR}-Q1", last_quarter)
        if last_quarter and int(last_quarter[:4]) >= STATISTICS_START_YEAR
        else []
    )
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Quarterly counts")
    sheet.append(["quarter", *group_names])
    for quarter in quarters:
        sheet.append([quarter, *(counts.get(quarter, {}).get(name, 0) for name in group_names)])
    info_sheet = workbook.create_sheet("Run info")
    for row in run_info:
        info_sheet.append(list(row))
    workbook.save(path)


def split_by_bucket(
    input_path: Path,
    output_dir: Path,
    file_names: dict[str, str],
    classify: Callable[[Sequence[int] | None], str | None],
    statistics_name: str | None = None,
) -> Counter:
    """Write each comment to the file of its bucket; comments without a bucket are dropped."""
    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: output_dir / file_name for name, file_name in file_names.items()}
    spills = {name: path.with_name(f".{path.name}.spill") for name, path in paths.items()}
    counts: Counter = Counter()
    quarterly: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    try:
        with ExitStack() as stack, input_path.open("r", encoding="utf-8") as source:
            outputs = {name: stack.enter_context(spill.open("w", encoding="utf-8")) for name, spill in spills.items()}
            for line in source:
                if not line.strip():
                    continue
                comment = json.loads(line)
                bucket = classify(comment.get(RANGE_FIELD))
                if bucket is None:
                    counts[UNASSIGNED] += 1
                    continue
                outputs[bucket].write(line if line.endswith("\n") else line + "\n")
                counts[bucket] += 1
                quarterly[quarter_for(comment)][bucket] += 1
        for name, spill in spills.items():
            spill.replace(paths[name])
    finally:
        for spill in spills.values():
            spill.unlink(missing_ok=True)
    for name, path in paths.items():
        print(f"{path.name}: {counts[name]:,} comments")
    print(f"dropped (no birth year range or outside all buckets): {counts[UNASSIGNED]:,} comments")
    if statistics_name:
        run_info = [
            ("run started", datetime.now().isoformat(timespec="seconds")),
            ("input", str(input_path)),
            ("comments written", f"{sum(counts[name] for name in paths):,}"),
            ("comments dropped", f"{counts[UNASSIGNED]:,}"),
            ("total runtime", f"{time.monotonic() - started:.2f} seconds"),
        ]
        write_statistics(output_dir / statistics_name, list(paths), quarterly, run_info)
        print(f"Quarterly statistics -> {output_dir / statistics_name}")
    return counts
