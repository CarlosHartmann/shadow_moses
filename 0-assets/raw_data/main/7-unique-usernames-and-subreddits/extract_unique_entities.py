import json
import os
import sqlite3
from pathlib import Path


MAIN_DIRECTORY = Path(__file__).resolve().parent.parent
OUTPUT_DIRECTORY = MAIN_DIRECTORY / "7-unique-usernames-and-subreddits"
AGE_GROUPS = ("age_silent", "age_boomer", "age_X", "age_millennial", "age_Z", "age_young", "age_old")
OTHER_GROUPS = (
    "gender_male",
    "gender_female",
    "political_conservative",
    "political_progressive",
    "gender_political_conservative",
    "gender_political_progressive",
)
# Only the finalised study datasets: original group comments (3a, age groups from 3b),
# cross-subreddit comments (4) and the reduced baseline (6). Intermediate files such as
# the age.jsonl files and the unreduced baselines are not used.
INPUT_FILES = (
    *(MAIN_DIRECTORY / "3a-grouping" / "grouped_subreddit_data" / f"{g}.jsonl" for g in OTHER_GROUPS),
    *(MAIN_DIRECTORY / "3b-age-grouping" / f"{g}.jsonl" for g in AGE_GROUPS),
    *(
        MAIN_DIRECTORY / "4-cross-subreddit-data" / "cross_subreddit_data" / f"{g}.jsonl"
        for g in (*AGE_GROUPS, *OTHER_GROUPS)
    ),
    MAIN_DIRECTORY / "6-baseline-reduction" / "reduced_baseline.jsonl",
)
UNKNOWN_AUTHORS = {"[deleted]", "[removed]"}
# The subreddit list already handed off to the indexing process (a copy of an earlier
# unique_subreddits.txt); subreddits missing from it are listed separately.
INDEXED_SUBREDDITS_NAME = "indexed_subreddits.txt"
ADDED_SUBREDDITS_NAME = "subreddits_not_yet_indexed.txt"
BATCH_LINES = 50_000
PROGRESS_LINES = 5_000_000


def collect_unique_values(files: list[Path], output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    database_path = output_directory / ".progress.sqlite3"

    # Resume only if the saved run used exactly these inputs, unchanged; otherwise values
    # from files that are no longer inputs would survive in the database.
    signatures = {
        path.relative_to(MAIN_DIRECTORY).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in files
    }
    if database_path.exists():
        with sqlite3.connect(database_path) as database:
            try:
                saved = {
                    path: (size, modified)
                    for path, size, modified in database.execute(
                        "SELECT path, size, modified_ns FROM processed_files"
                    )
                }
            except sqlite3.OperationalError:
                saved = None
        database.close()
        if saved is None or any(signatures.get(path) != signature for path, signature in saved.items()):
            print("Inputs changed since the saved run; rebuilding from scratch.", flush=True)
            database_path.unlink()

    indexed_path = output_directory / INDEXED_SUBREDDITS_NAME
    indexed_subreddits = (
        set(indexed_path.read_text(encoding="utf-8").splitlines()) if indexed_path.exists() else None
    )

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
            signature = signatures[relative_path]
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
            stat = path.stat()

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
                    if isinstance(author, str) and author.strip() and author not in UNKNOWN_AUTHORS:
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

        database.execute("DROP TABLE IF EXISTS normalized_subreddits")
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

        # Subreddits not indexed yet, so that only those have to be indexed in addition.
        if indexed_subreddits is not None:
            known = {value.casefold() for value in indexed_subreddits}
            added = [
                value
                for (value,) in database.execute("SELECT value FROM subreddits ORDER BY value")
                if value.casefold() not in known
            ]
            (output_directory / ADDED_SUBREDDITS_NAME).write_text(
                "".join(f"{value}\n" for value in added), encoding="utf-8"
            )
            print(
                f"{len(added):,} subreddits not in {INDEXED_SUBREDDITS_NAME} -> {ADDED_SUBREDDITS_NAME}",
                flush=True,
            )


def main() -> None:
    files = sorted(INPUT_FILES)
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