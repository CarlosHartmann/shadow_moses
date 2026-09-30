"""Filter the raw comment-extraction .jsonl file according to notes.txt rules:

1. Duplicate comments (same comment "id") are removed. Duplicates are not
   duplicate JSON objects with identical bodies; the same comment can appear
   multiple times because the search hit it more than once.
2. Comments whose body contains "i'm a bot" are removed.
3. Comments from AutoModerator, or from users whose name ends in "Bot"
   (case-sensitive), "Mod", or "Moderator", are removed. Excluded usernames
   are collected and written to a separate file.

The input file is *not* assumed to have one JSON object per physical line:
strings inside the objects may themselves contain raw line breaks, so objects
are parsed by incrementally feeding a JSON decoder rather than by splitting
on "\n".
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterator

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = (
    HERE.parent
    / "1-extraction_stage"
    / "comment_extraction_from_RC_2005-12.zst_executed-at_2026-09-11_at_15h-26m-50s_RC_2005-12.zst.jsonl"
)
DEFAULT_OUTPUT = HERE / "filtered_comments.jsonl"
DEFAULT_EXCLUDED_USERS_OUTPUT = HERE / "excluded_users.txt"

BOT_MESSAGE_PATTERN = re.compile(r"i['\u2019]m a bot", re.IGNORECASE)
CHUNK_SIZE = 1024 * 1024


def iter_json_objects(path: Path, chunk_size: int = CHUNK_SIZE) -> Iterator[dict]:
    """Yield JSON objects from a file that may contain raw line breaks inside strings."""
    decoder = json.JSONDecoder()
    buf = ""
    with open(path, "r", encoding="utf-8") as f:
        while True:
            chunk = f.read(chunk_size)
            buf += chunk
            while True:
                stripped = buf.lstrip()
                if not stripped:
                    buf = stripped
                    break
                try:
                    obj, idx = decoder.raw_decode(stripped)
                except json.JSONDecodeError:
                    # Not enough data yet to parse a full object.
                    buf = stripped
                    break
                yield obj
                buf = stripped[idx:]
            if not chunk:
                break


def is_bot_or_mod(author: str) -> bool:
    if author == "AutoModerator":
        return True
    return author.endswith("Bot") or author.endswith("Mod") or author.endswith("Moderator")


def filter_comments(
    input_path: Path,
    output_path: Path,
    excluded_users_output: Path,
) -> None:
    seen_ids: set[str] = set()
    excluded_users: set[str] = set()

    kept = 0
    total = 0

    with open(output_path, "w", encoding="utf-8") as out_f:
        for obj in iter_json_objects(input_path):
            total += 1
            body = obj.get("body", "")
            author = obj.get("author", "")
            comment_id = obj.get("id")

            if is_bot_or_mod(author):
                excluded_users.add(author)
                continue

            if BOT_MESSAGE_PATTERN.search(body):
                continue

            if comment_id in seen_ids:
                continue
            seen_ids.add(comment_id)

            out_f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            kept += 1

    with open(excluded_users_output, "w", encoding="utf-8") as users_f:
        for user in sorted(excluded_users):
            users_f.write(user + "\n")

    print(f"Processed {total} comments, kept {kept}, removed {total - kept}.")
    print(f"Excluded {len(excluded_users)} unique bot/mod users -> {excluded_users_output}")


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
    args = parser.parse_args()

    filter_comments(args.input, args.output, args.excluded_users_output)


if __name__ == "__main__":
    main()
