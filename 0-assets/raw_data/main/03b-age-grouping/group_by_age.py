"""Split the age comments into young (born after 1980) and old (born 1980 or earlier)."""

from __future__ import annotations

import argparse
from pathlib import Path

from age_split import DEFAULT_INPUT, DEFAULT_OUTPUT_DIR, majority_bucket, split_by_bucket

CUTOFF_YEAR = 1980
# The outer bounds only have to cover every plausible birth year.
BUCKETS = (
    ("old", 0, CUTOFF_YEAR),
    ("young", CUTOFF_YEAR + 1, 9999),
)
FILE_NAMES = {"young": "age_young.jsonl", "old": "age_old.jsonl"}
STATISTICS_NAME = "age_young_old_quarterly_statistics.xlsx"


def classify(birth_range):
    return majority_bucket(birth_range, BUCKETS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    split_by_bucket(args.input, args.output_dir, FILE_NAMES, classify, STATISTICS_NAME)


if __name__ == "__main__":
    main()
