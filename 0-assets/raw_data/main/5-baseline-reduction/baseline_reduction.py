"""Create a reproducible, quarter-stratified sample of the baseline comments."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = (
    HERE.parent / "4-cross-subreddit-data" / "cross_subreddit_data" / "baseline.jsonl"
)
DEFAULT_OUTPUT = HERE / "reduced_baseline.jsonl"
DEFAULT_SAMPLE_SIZE = 1_000_000
DEFAULT_SEED = 42
PROGRESS_CHECK_EVERY_LINES = 100_000


def report_progress(
    stage: str,
    input_file,
    input_size: int,
    started_at: float,
    next_percent: int,
) -> int:
    percent = min(100, input_file.tell() * 100 // input_size) if input_size else 100
    if percent >= next_percent:
        elapsed = time.monotonic() - started_at
        print(f"{stage}: {percent}% ({elapsed:.0f}s elapsed).", flush=True)
        return (percent // 10 + 1) * 10
    return next_percent


def comment_quarter(line: str, line_number: int) -> str | None:
    if not line.strip():
        return None
    try:
        comment = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON on line {line_number}: {error}") from error
    if not isinstance(comment, dict):
        raise ValueError(f"Expected a JSON object on line {line_number}.")
    try:
        timestamp = float(comment["created_utc"])
        created_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
        raise ValueError(
            f"Missing or invalid created_utc on line {line_number}."
        ) from error
    quarter = (created_at.month - 1) // 3 + 1
    return f"{created_at.year}-Q{quarter}"


def count_by_quarter(input_path: Path) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    input_size = input_path.stat().st_size
    started_at = time.monotonic()
    next_percent = 10
    print("Pass 1/3: counting comments by quarter.", flush=True)
    with input_path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            quarter = comment_quarter(line, line_number)
            if quarter is not None:
                counts[quarter] += 1
            if line_number % PROGRESS_CHECK_EVERY_LINES == 0:
                next_percent = report_progress(
                    "Pass 1/3", input_file, input_size, started_at, next_percent
                )
    print(
        f"Pass 1/3 complete: {sum(counts.values()):,} comments "
        f"in {time.monotonic() - started_at:.1f}s.",
        flush=True,
    )
    return dict(counts)


def allocate_quotas(counts: dict[str, int], sample_size: int) -> dict[str, int]:
    total = sum(counts.values())
    if sample_size > total:
        raise ValueError(
            f"Requested {sample_size:,} comments, but the input contains only {total:,}."
        )

    quotas = {quarter: sample_size * count // total for quarter, count in counts.items()}
    remaining = sample_size - sum(quotas.values())
    largest_remainders = sorted(
        counts,
        key=lambda quarter: (
            -(sample_size * counts[quarter] % total),
            quarter,
        ),
    )
    for quarter in largest_remainders[:remaining]:
        quotas[quarter] += 1
    return quotas


def select_line_numbers(
    input_path: Path,
    counts: dict[str, int],
    quotas: dict[str, int],
    seed: int,
) -> list[int]:
    rng = random.Random(seed)
    reservoirs: dict[str, list[int]] = {quarter: [] for quarter in counts}
    seen: dict[str, int] = defaultdict(int)
    second_pass_counts: dict[str, int] = defaultdict(int)
    input_size = input_path.stat().st_size
    started_at = time.monotonic()
    next_percent = 10
    print("Pass 2/3: selecting the seeded sample.", flush=True)

    with input_path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            quarter = comment_quarter(line, line_number)
            if quarter is None:
                continue
            second_pass_counts[quarter] += 1
            if quarter not in counts:
                raise ValueError("Input changed between sampling passes.")

            seen[quarter] += 1
            quota = quotas[quarter]
            if quota == 0:
                continue
            reservoir = reservoirs[quarter]
            if len(reservoir) < quota:
                reservoir.append(line_number)
            else:
                replacement = rng.randrange(seen[quarter])
                if replacement < quota:
                    reservoir[replacement] = line_number
            if line_number % PROGRESS_CHECK_EVERY_LINES == 0:
                next_percent = report_progress(
                    "Pass 2/3", input_file, input_size, started_at, next_percent
                )

    if dict(second_pass_counts) != counts:
        raise ValueError("Input changed between sampling passes.")
    selected = sorted(
        line_number
        for reservoir in reservoirs.values()
        for line_number in reservoir
    )
    if len(selected) != sum(quotas.values()):
        raise ValueError("Sampling did not produce the requested number of comments.")
    print(
        f"Pass 2/3 complete: selected {len(selected):,} line positions "
        f"in {time.monotonic() - started_at:.1f}s.",
        flush=True,
    )
    return selected


def write_sample(input_path: Path, output_path: Path, selected_lines: list[int]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_size = input_path.stat().st_size
    started_at = time.monotonic()
    next_percent = 10
    print("Pass 3/3: writing the sampled comments.", flush=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temporary_path = Path(output_file.name)
            selected_index = 0
            with input_path.open("r", encoding="utf-8") as input_file:
                for line_number, line in enumerate(input_file, start=1):
                    if (
                        selected_index < len(selected_lines)
                        and line_number == selected_lines[selected_index]
                    ):
                        output_file.write(line if line.endswith("\n") else line + "\n")
                        selected_index += 1
                    if line_number % PROGRESS_CHECK_EVERY_LINES == 0:
                        next_percent = report_progress(
                            "Pass 3/3",
                            input_file,
                            input_size,
                            started_at,
                            next_percent,
                        )
            if selected_index != len(selected_lines):
                raise ValueError("Input changed before the sample could be written.")
        os.replace(temporary_path, output_path)
        print(
            f"Pass 3/3 complete: wrote {selected_index:,} comments "
            f"in {time.monotonic() - started_at:.1f}s.",
            flush=True,
        )
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    if input_path == output_path:
        print("Input and output paths must be different.", file=sys.stderr)
        return 2
    if args.sample_size <= 0:
        print("Sample size must be positive.", file=sys.stderr)
        return 2

    try:
        counts = count_by_quarter(input_path)
        if not counts:
            raise ValueError("The input contains no comments.")
        quotas = allocate_quotas(counts, args.sample_size)
        selected_lines = select_line_numbers(input_path, counts, quotas, args.seed)
        write_sample(input_path, output_path, selected_lines)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    print(
        f"Wrote {len(selected_lines):,} comments to {output_path} "
        f"(seed={args.seed}, quarters={len(quotas)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())