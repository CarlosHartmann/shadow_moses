"""Split the age comments into generation files (age_Z.jsonl, age_millennial.jsonl, ...)."""

from __future__ import annotations

import argparse
from pathlib import Path

from age_split import DEFAULT_INPUT, DEFAULT_OUTPUT_DIR, majority_bucket, split_by_bucket

# Same birth year cutoffs as deduce_birthyear.py; xennial overlaps two generations and is not a bucket.
GENERATIONS = (
    ("silent", 1928, 1945),
    ("boomer", 1946, 1964),
    ("X", 1965, 1980),
    ("millennial", 1981, 1996),
    ("Z", 1997, 2012),
)
FILE_NAMES = {name: f"age_{name}.jsonl" for name, _, _ in GENERATIONS}
STATISTICS_NAME = "age_generation_quarterly_statistics.xlsx"


def classify(birth_range):
    return majority_bucket(birth_range, GENERATIONS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    split_by_bucket(args.input, args.output_dir, FILE_NAMES, classify, STATISTICS_NAME)


if __name__ == "__main__":
    main()
