"""Filter the raw comment-extraction .jsonl file according to notes.txt rules:

1. Duplicate comments (same comment "id") are removed. Duplicates are not
   duplicate JSON objects with identical bodies; the same comment can appear
   multiple times because the search hit it more than once.
2. Comments whose body contains "i'm a bot" are removed.
3. Comments from AutoModerator, or from users whose name ends in "Bot"
   (case-sensitive), "Mod", or "Moderator", are removed. Excluded usernames
   are collected and written to a separate file.
4. Comments from October 2021 (UTC) are removed.
5. Comments whose subreddit begins with "u_" are removed.
6. Comments from before January 2010 (UTC) are removed.
7. All unique subreddits found among the kept comments are written to a
   separate file.
8. The processing time is printed at the end.

The input is streamed line by line and never loaded into memory as a whole.
Strings inside the objects may contain line breaks, so a record that does not
parse on its own is extended with the following lines until it does.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = (
    HERE.parent
    / "1-extraction_stage"
    / "comment_extraction_from_RC_2005-12.zst_executed-at_2026-09-30_at_21h-43m-00s_RC_2005-12.zst.jsonl"
)
DEFAULT_OUTPUT = HERE / "filtered_comments.jsonl"
DEFAULT_EXCLUDED_USERS_OUTPUT = HERE / "excluded_users.txt"
DEFAULT_SUBREDDITS_OUTPUT = HERE / "subreddits.txt"

BOT_MESSAGE_PATTERN = re.compile(r"i['\u2019]m a bot", re.IGNORECASE)
IO_BUFFER_SIZE = 16 * 1024 * 1024
MAX_RECORD_CHARS = 64 * 1024 * 1024  # a record longer than this is treated as corrupt
PROGRESS_EVERY = 5_000_000

# created_utc bounds (UTC) as unix timestamps
TS_2010_01_01 = 1262304000
TS_2021_10_01 = 1633046400
TS_2021_11_01 = 1635724800

_loads = json.JSONDecoder(strict=False).decode


def iter_json_records(path: Path) -> Iterator[tuple[dict, str]]:
    """Yield (object, raw_text) per JSON record, streaming; tolerates raw line breaks in strings."""
    pending = ""
    with open(path, "r", encoding="utf-8", buffering=IO_BUFFER_SIZE) as f:
        for line in f:
            if not pending:
                if not line.strip():
                    continue
                pending = line
            else:
                pending += line
            try:
                obj = _loads(pending)
            except json.JSONDecodeError:
                if len(pending) > MAX_RECORD_CHARS:
                    print("WARNING: discarding oversized unparseable record")
                    pending = ""
                continue
            yield obj, pending
            pending = ""
    if pending.strip():
        print("WARNING: trailing unparseable data at end of file ignored")


def is_bot_or_mod(author: str) -> bool:
    return author == "AutoModerator" or author.endswith(("Bot", "Mod", "Moderator"))


def id_key(comment_id: str) -> int | str:
    # Base-36 ints use far less memory than strings in the dedup set.
    try:
        return int(comment_id, 36)
    except (TypeError, ValueError):
        return comment_id


def filter_comments(
    input_path: Path,
    output_path: Path,
    excluded_users_output: Path,
    subreddits_output: Path,
) -> None:
    start = time.perf_counter()
    total_bytes = input_path.stat().st_size

    seen_ids: set = set()
    excluded_users: set[str] = set()
    subreddits: set[str] = set()
    kept = 0
    total = 0

    with open(output_path, "w", encoding="utf-8", buffering=IO_BUFFER_SIZE) as out_f:
        for obj, raw in iter_json_records(input_path):
            total += 1
            if total % PROGRESS_EVERY == 0:
                elapsed = time.perf_counter() - start
                print(f"  {total:,} records, {kept:,} kept, {elapsed / 60:.1f} min elapsed", flush=True)

            author = obj.get("author") or ""
            if is_bot_or_mod(author):
                excluded_users.add(author)
                continue

            created = obj.get("created_utc")
            if created is not None:
                created = float(created)
                if created < TS_2010_01_01 or TS_2021_10_01 <= created < TS_2021_11_01:
                    continue

            subreddit = obj.get("subreddit") or ""
            if subreddit.startswith("u_"):
                continue

            if BOT_MESSAGE_PATTERN.search(obj.get("body") or ""):
                continue

            key = id_key(obj.get("id"))
            if key in seen_ids:
                continue
            seen_ids.add(key)

            if subreddit:
                subreddits.add(subreddit)

            if raw.count("\n") <= 1:
                out_f.write(raw.rstrip("\r\n") + "\n")
            else:
                out_f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            kept += 1

    Path(excluded_users_output).write_text("".join(u + "\n" for u in sorted(excluded_users)), encoding="utf-8")
    Path(subreddits_output).write_text("".join(s + "\n" for s in sorted(subreddits)), encoding="utf-8")

    elapsed = time.perf_counter() - start
    print(f"Processed {total:,} comments, kept {kept:,}, removed {total - kept:,}.")
    print(f"Excluded {len(excluded_users)} unique bot/mod users -> {excluded_users_output}")
    print(f"Found {len(subreddits):,} unique subreddits -> {subreddits_output}")
    print(
        f"Processing time: {timedelta(seconds=round(elapsed))} ({elapsed:.1f} s) "
        f"for {total_bytes / 1e9:.2f} GB ({total_bytes / 1e6 / max(elapsed, 1e-9):.1f} MB/s)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Path to the raw .jsonl file.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to write the filtered .jsonl file.")
    parser.add_argument(
        "--excluded-users-output",
        type=Path,
        default=DEFAULT_EXCLUDED_USERS_OUTPUT,
        help="Path to write the list of excluded bot/mod usernames.",
    )
    parser.add_argument(
        "--subreddits-output",
        type=Path,
        default=DEFAULT_SUBREDDITS_OUTPUT,
        help="Path to write the list of unique subreddits found in the kept comments.",
    )
    args = parser.parse_args()

    filter_comments(args.input, args.output, args.excluded_users_output, args.subreddits_output)


if __name__ == "__main__":
    main()
