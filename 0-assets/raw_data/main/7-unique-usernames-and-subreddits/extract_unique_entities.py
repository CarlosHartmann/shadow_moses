import json
import os
import sqlite3
from pathlib import Path


MAIN_DIRECTORY = Path(__file__).resolve().parent.parent
OUTPUT_DIRECTORY = MAIN_DIRECTORY / "7-unique-usernames-and-subreddits"
INPUT_DIRECTORIES = (
    MAIN_DIRECTORY / "3a-grouping" / "grouped_subreddit_data",
    MAIN_DIRECTORY / "3b-age-grouping",
    MAIN_DIRECTORY / "4-cross-subreddit-data" / "cross_subreddit_data",
)
INPUT_FILES = (
    MAIN_DIRECTORY / "6-baseline-reduction" / "reduced_baseline.jsonl",
)
# Unreduced baselines; replaced by the reduced baseline above.
EXCLUDED_FILES = (
    MAIN_DIRECTORY / "3a-grouping" / "grouped_subreddit_data" / "baseline.jsonl",
    MAIN_DIRECTORY / "4-cross-subreddit-data" / "cross_subreddit_data" / "baseline.jsonl",
)
BATCH_LINES = 50_000
PROGRESS_LINES = 5_000_000


def collect_unique_values(files: list[Path], output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    database_path = output_directory / ".progress.sqlite3"

    with sqlite3.connect(database_path) as database:
        database.execute("PRAGMA synchronous = NORMAL")
        database.execute("PRAGMA cache_size = -32768")
        database.execute("PRAGMA temp_store = FILE")
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS usernames (
                value TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS subreddits (
                value TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS processed_files (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL
            ) WITHOUT ROWID;
            """
        )

        for file_number, path in enumerate(files, start=1):
            relative_path = path.relative_to(MAIN_DIRECTORY).as_posix()
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            previous = database.execute(
                "SELECT size, modified_ns FROM processed_files WHERE path = ?",
                (relative_path,),
            ).fetchone()
            if previous == signature:
                print(
                    f"File {file_number}/{len(files)}: skipping completed {relative_path}",
                    flush=True,
                )
                continue
            if previous is not None:
                raise RuntimeError(
                    f"Input changed since the saved run: {relative_path}. "
                    f"Remove {database_path} to rebuild from scratch."
                )

            print(
                f"File {file_number}/{len(files)}: processing {relative_path} "
                f"({stat.st_size:,} bytes)",
                flush=True,
            )
            line_count = 0
            malformed_count = 0
            with path.open("rb") as source:
                for raw_line in source:
                    line_count += 1
                    try:
                        record = json.loads(raw_line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        malformed_count += 1
                        continue
                    if not isinstance(record, dict):
                        malformed_count += 1
                        continue

                    author = record.get("author")
                    if isinstance(author, str) and author.strip() and author != "[deleted]":
                        database.execute(
                            "INSERT OR IGNORE INTO usernames(value) VALUES (?)",
                            (author,),
                        )

                    subreddit = record.get("subreddit")
                    if isinstance(subreddit, str) and subreddit.strip():
                        database.execute(
                            "INSERT OR IGNORE INTO subreddits(value) VALUES (?)",
                            (subreddit,),
                        )

                    if line_count % BATCH_LINES == 0:
                        database.commit()

                    if line_count % PROGRESS_LINES == 0:
                        print(
                            f"  {line_count:,} lines processed; "
                            f"{malformed_count:,} malformed",
                            flush=True,
                        )

            database.execute(
                "INSERT INTO processed_files(path, size, modified_ns) VALUES (?, ?, ?)",
                (relative_path, *signature),
            )
            database.commit()
            print(
                f"  Finished {line_count:,} lines; "
                f"{malformed_count:,} malformed",
                flush=True,
            )

        database.execute(
            "CREATE TABLE normalized_subreddits (value TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        database.execute(
            "INSERT INTO normalized_subreddits(value) "
            "SELECT MIN(value) FROM subreddits GROUP BY lower(value)"
        )
        database.execute("DROP TABLE subreddits")
        database.execute(
            "ALTER TABLE normalized_subreddits RENAME TO subreddits"
        )
        database.commit()

        for table, filename in (
            ("usernames", "unique_usernames.txt"),
            ("subreddits", "unique_subreddits.txt"),
        ):
            destination = output_directory / filename
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as output:
                for (value,) in database.execute(
                    f"SELECT value FROM {table} ORDER BY value"
                ):
                    output.write(value)
                    output.write("\n")
            os.replace(temporary, destination)
            count = database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"Wrote {count:,} unique values to {destination}", flush=True)


def main() -> None:
    files = sorted({
        path
        for directory in INPUT_DIRECTORIES
        for path in directory.rglob("*.jsonl")
        if path.is_file() and path not in EXCLUDED_FILES
    } | set(INPUT_FILES))
    missing_files = [path for path in files if not path.is_file()]
    if missing_files:
        raise FileNotFoundError(
            "Missing JSONL input files: "
            + ", ".join(str(path) for path in missing_files)
        )
    if not files:
        raise FileNotFoundError("No JSONL input files found in the configured directories")
    print(f"Found {len(files)} JSONL files", flush=True)
    collect_unique_values(files, OUTPUT_DIRECTORY)


if __name__ == "__main__":
    main()