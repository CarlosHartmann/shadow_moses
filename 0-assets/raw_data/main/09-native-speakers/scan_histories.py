"""Scan the full comment and submission histories with the rules in patterns.py.

  python3 scan_histories.py run [--workers N] [--output DIR]   start or resume the scan
  python3 scan_histories.py status [--output DIR]              progress of the saved run
  python3 scan_histories.py show USER [--output DIR]           all evidence for one user

See notes.txt for the output format.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
import queue
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

STEP_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(STEP_DIRECTORY))
import patterns  # noqa: E402
DEFAULT_OUTPUT_DIRECTORY = STEP_DIRECTORY / "evidence"
DATABASE_NAME = "evidence.sqlite3"
LOG_NAME = "scan.log"
INPUT_ROOTS = (
    Path("/Volumes/Limbo/reddit-OPsayerhistories"),         # 2005-06..2021-05, uncompressed
    Path("/Volumes/dissertationhorse/OP_sayer_histories"),  # 2021-06..2022-12, .zst
)
STREAMS = ("comments", "submissions")
UNKNOWN_AUTHORS = {"[deleted]", "[removed]"}
REMOVED_TEXTS = {"[deleted]", "[removed]"}
# Per user and rule, a fixed random sample of hits is kept (the ones with the smallest hash,
# so the sample does not depend on the order the files are scanned in). The frequent, weak
# rules get a smaller sample; rule_counts keeps the full counts.
WEAK_RULES = {"A10_i_speak_lang", "B5_non_native", "C1_i_am_from", "C2_grew_up_in", "C3_i_live_in",
              "C4_moved_to", "C6_here_in", "C7_my_country", "C8_i_am_nationality", "C9_nationality_here",
              "D1_taught_english_abroad"}
WEAK_CAP = 20
STRONG_CAP = 50
CONTEXT_CHARS = 200
CHUNK_LINES = 1_000_000
READ_BUFFER = 16 * 1024 * 1024
STATUS_SECONDS = 60
RATE_WINDOW_SECONDS = 900
MIN_FREE_GB = 10


def cap(rule: str) -> int:
    return WEAK_CAP if rule in WEAK_RULES else STRONG_CAP


def log(output_directory: Path, message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    with open(output_directory / LOG_NAME, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def zst_content_size(path: Path) -> int | None:
    # The uncompressed size from the zstd frame header (None if the header does not record it).
    with open(path, "rb") as fh:
        header = fh.read(18)
    if header[:4] != b"\x28\xb5\x2f\xfd":
        return None
    descriptor = header[4]
    single_segment = (descriptor >> 5) & 1
    position = 5 + (0 if single_segment else 1) + (0, 1, 2, 4)[descriptor & 3]
    length = (1 if single_segment else 0, 2, 4, 8)[descriptor >> 6]
    if length == 0:
        return None
    value = int.from_bytes(header[position:position + length], "little")
    return value + 256 if length == 2 else value


def discover_files() -> list[dict]:
    files = []
    for root in INPUT_ROOTS:
        if not root.is_dir():
            raise SystemExit(f"Input folder not found (disk not mounted?): {root}")
        for stream in STREAMS:
            for path in sorted((root / stream).iterdir()):
                if path.name.startswith(".") or not path.name.endswith((".jsonl", ".jsonl.zst")):
                    continue
                compressed = path.name.endswith(".zst")
                size = zst_content_size(path) if compressed else path.stat().st_size
                files.append({
                    "key": f"{stream}/{path.name}",
                    "path": str(path),
                    "stream": stream,
                    "compressed": compressed,
                    # Unknown uncompressed sizes are estimated (only used for progress reports).
                    "size": size or path.stat().st_size * 8,
                })
    return files


def open_database(output_directory: Path) -> sqlite3.Connection:
    output_directory.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(output_directory / DATABASE_NAME, timeout=60)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA synchronous=NORMAL")
    database.execute("PRAGMA cache_size=-65536")
    database.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS files (
            key TEXT PRIMARY KEY, path TEXT, stream TEXT, size INTEGER,
            bytes_done INTEGER DEFAULT 0, lines_done INTEGER DEFAULT 0, undecodable INTEGER DEFAULT 0,
            done INTEGER DEFAULT 0, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS users (
            author TEXT PRIMARY KEY, comments INTEGER, submissions INTEGER,
            first_utc INTEGER, last_utc INTEGER) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS flairs (
            author TEXT, subreddit TEXT, flair TEXT, n INTEGER, first_utc INTEGER, last_utc INTEGER,
            PRIMARY KEY (author, subreddit, flair)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS rule_counts (
            author TEXT, rule TEXT, n INTEGER, PRIMARY KEY (author, rule)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS hits (
            author TEXT, rule TEXT, h INTEGER, name TEXT, subreddit TEXT, created_utc INTEGER,
            value TEXT, context TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS hits_author_rule_h ON hits (author, rule, h);
    """)
    return database


def settings_signature() -> dict:
    # A run can only be resumed with the same rules and sampling settings.
    return {
        "patterns_sha256": hashlib.sha256((STEP_DIRECTORY / "patterns.py").read_bytes()).hexdigest(),
        "caps": f"weak={WEAK_CAP},strong={STRONG_CAP},weak_rules={','.join(sorted(WEAK_RULES))}",
        "context_chars": str(CONTEXT_CHARS),
    }


def hit_hash(name: str, rule: str, start: int) -> int:
    digest = hashlib.blake2b(f"{name}|{rule}|{start}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def context(text: str, start: int, end: int) -> str:
    a = max(0, start - CONTEXT_CHARS)
    b = min(len(text), end + CONTEXT_CHARS)
    snippet = ("…" if a > 0 else "") + text[a:start] + "⟦" + text[start:end] + "⟧" + text[end:b] + ("…" if b < len(text) else "")
    return " ".join(snippet.split())


def new_chunk() -> dict:
    return {"lines": 0, "undecodable": 0, "users": {}, "flairs": {}, "rule_counts": Counter(), "hits": {}}


def scan_line(line: bytes, stream: str, chunk: dict) -> None:
    try:
        item = json.loads(line)
    except ValueError:
        chunk["undecodable"] += 1
        return
    author = item.get("author")
    if not author or author in UNKNOWN_AUTHORS:
        return
    utc = int(float(item.get("created_utc") or 0))
    subreddit = item.get("subreddit") or ""
    if stream == "comments":
        name = item.get("name") or f"t1_{item.get('id')}"
        text = item.get("body") or ""
        text = "" if text in REMOVED_TEXTS else text
    else:
        name = f"t3_{item.get('id')}"
        selftext = item.get("selftext") or ""
        text = (item.get("title") or "") + ("" if selftext in REMOVED_TEXTS else "\n\n" + selftext)

    user = chunk["users"].get(author)
    if user is None:
        user = chunk["users"][author] = [0, 0, utc, utc]
    user[0 if stream == "comments" else 1] += 1
    user[2] = min(user[2], utc)
    user[3] = max(user[3], utc)

    flair = (item.get("author_flair_text") or "").strip()
    if flair:
        entry = chunk["flairs"].get((author, subreddit, flair))
        if entry is None:
            chunk["flairs"][(author, subreddit, flair)] = [1, utc, utc]
        else:
            entry[0] += 1
            entry[1] = min(entry[1], utc)
            entry[2] = max(entry[2], utc)

    if not text:
        return
    cleaned = patterns.clean(text)
    for rule, _, match, value in patterns.find(cleaned):
        chunk["rule_counts"][(author, rule)] += 1
        h = hit_hash(name, rule, match.start())
        candidates = chunk["hits"].setdefault((author, rule), [])
        candidates.append((h, name, subreddit, utc, value or None, context(cleaned, match.start(), match.end())))
        # Only the smallest hashes can end up in the sample, so the rest need not be sent.
        if len(candidates) > 2 * cap(rule):
            candidates.sort()
            del candidates[cap(rule):]


def finish_chunk(chunk: dict) -> dict:
    for (_, rule), candidates in chunk["hits"].items():
        candidates.sort()
        del candidates[cap(rule):]
    return chunk


def worker(tasks: multiprocessing.Queue, results: multiprocessing.Queue, stop: multiprocessing.Event) -> None:
    # Ctrl-C reaches the whole process group; the main process decides when to stop.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while not stop.is_set():
        task = tasks.get()
        if task is None:
            break
        try:
            scan_file(task, results, stop)
        except Exception as error:  # reported to the main process, which stops the run
            results.put(("error", task["key"], f"{type(error).__name__}: {error}"))
            return
    results.put(("exit", None, None))


def scan_file(task: dict, results: multiprocessing.Queue, stop: multiprocessing.Event) -> None:
    position, lines = task["bytes_done"], task["lines_done"]
    process = None
    if task["compressed"]:
        # Own session, so that Ctrl-C does not end zstd early (which would look like the file's end).
        process = subprocess.Popen(["zstd", "-dcq", task["path"]], stdout=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, bufsize=READ_BUFFER, start_new_session=True)
        handle = process.stdout
        remaining = position
        while remaining:
            skipped = len(handle.read(min(remaining, 64 * 1024 * 1024)))
            if not skipped:
                raise OSError("compressed file ended before the resume position")
            remaining -= skipped
    else:
        handle = open(task["path"], "rb", buffering=READ_BUFFER)
        handle.seek(position)
    try:
        chunk = new_chunk()
        for line in handle:
            position += len(line)
            lines += 1
            chunk["lines"] += 1
            scan_line(line, task["stream"], chunk)
            if chunk["lines"] >= task["chunk_lines"]:
                results.put(("chunk", task["key"], (position, lines, False, finish_chunk(chunk))))
                chunk = new_chunk()
                if stop.is_set():
                    return
        if process is not None:
            handle.close()
            if process.wait() != 0:
                raise OSError(f"zstd exited with status {process.returncode}")
        elif position != os.path.getsize(task["path"]):
            raise OSError(f"read {position} bytes, but the file has {os.path.getsize(task['path'])}")
        results.put(("chunk", task["key"], (position, lines, True, finish_chunk(chunk))))
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        handle.close()


def commit_chunk(database: sqlite3.Connection, key: str, payload: tuple) -> int:
    position, lines, done, chunk = payload
    stored = 0
    with database:
        database.executemany("""
            INSERT INTO users VALUES (?, ?, ?, ?, ?) ON CONFLICT (author) DO UPDATE SET
                comments = comments + excluded.comments, submissions = submissions + excluded.submissions,
                first_utc = min(first_utc, excluded.first_utc), last_utc = max(last_utc, excluded.last_utc)
        """, ((author, *values) for author, values in chunk["users"].items()))
        database.executemany("""
            INSERT INTO flairs VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (author, subreddit, flair) DO UPDATE SET
                n = n + excluded.n, first_utc = min(first_utc, excluded.first_utc),
                last_utc = max(last_utc, excluded.last_utc)
        """, ((*k, *values) for k, values in chunk["flairs"].items()))
        database.executemany("""
            INSERT INTO rule_counts VALUES (?, ?, ?) ON CONFLICT (author, rule) DO UPDATE SET n = n + excluded.n
        """, ((author, rule, n) for (author, rule), n in chunk["rule_counts"].items()))
        for (author, rule), candidates in chunk["hits"].items():
            limit = cap(rule)
            existing = [row[0] for row in database.execute(
                "SELECT h FROM hits WHERE author = ? AND rule = ? ORDER BY h", (author, rule))]
            existing_set = set(existing)
            keep = sorted(set(existing) | {c[0] for c in candidates})[:limit]
            if len(existing) + len(candidates) > limit and keep:
                database.execute("DELETE FROM hits WHERE author = ? AND rule = ? AND h > ?", (author, rule, keep[-1]))
            keep_set = set(keep)
            new_rows = [(author, rule, *c) for c in candidates if c[0] in keep_set and c[0] not in existing_set]
            database.executemany("INSERT OR IGNORE INTO hits VALUES (?, ?, ?, ?, ?, ?, ?, ?)", new_rows)
            stored += len(new_rows)
        database.execute("""
            UPDATE files SET bytes_done = ?, lines_done = ?, undecodable = undecodable + ?, done = ?,
                finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE key = ?
        """, (position, lines, chunk["undecodable"], int(done), int(done), datetime.now().isoformat(timespec="seconds"), key))
    return stored


def progress(database: sqlite3.Connection) -> dict:
    total_files, done_files, total_bytes, done_bytes, done_lines, undecodable = database.execute(
        "SELECT count(*), sum(done), sum(size), sum(min(bytes_done, size)), sum(lines_done), sum(undecodable) FROM files"
    ).fetchone()
    return {"total_files": total_files, "done_files": done_files or 0, "total_bytes": total_bytes or 0,
            "done_bytes": done_bytes or 0, "done_lines": done_lines or 0, "undecodable": undecodable or 0}


def database_bytes(output_directory: Path) -> int:
    return sum(p.stat().st_size for p in output_directory.glob(DATABASE_NAME + "*"))


def status_line(output_directory: Path, state: dict, started: float | None, window: list) -> str:
    fraction = state["done_bytes"] / state["total_bytes"] if state["total_bytes"] else 0
    now = time.time()
    db = database_bytes(output_directory)
    window.append((now, state["done_bytes"], state["done_lines"], db))
    while len(window) > 2 and now - window[0][0] > RATE_WINDOW_SECONDS:
        window.pop(0)
    (t0, b0, l0, d0), (t1, b1, l1, d1) = window[0], window[-1]
    byte_rate = (b1 - b0) / (t1 - t0) if t1 > t0 else 0
    line_rate = (l1 - l0) / (t1 - t0) if t1 > t0 else 0
    remaining = state["total_bytes"] - state["done_bytes"]
    eta = remaining / byte_rate if byte_rate else None
    # The users and flairs tables stop growing early on, so the database is projected from its
    # recent growth per input byte rather than from its total size.
    projected = db + (d1 - d0) / (b1 - b0) * remaining if b1 - b0 > 1e9 else None
    free = shutil.disk_usage(output_directory).free
    parts = [
        f"{state['done_files']}/{state['total_files']} files",
        f"{state['done_bytes'] / 1e9:,.1f}/{state['total_bytes'] / 1e9:,.1f} GB ({fraction * 100:.1f}%)",
        f"{state['done_lines'] / 1e6:,.1f} M lines",
        *([f"{byte_rate / 1e6:.1f} MB/s, {line_rate / 1e3:.1f}k lines/s"] if started is not None else []),
        f"db {db / 1e9:.2f} GB" + (f" (projected ~{projected / 1e9:.1f} GB at the end)" if projected else ""),
        f"free {free / 1e9:.1f} GB",
    ]
    if started is not None:
        parts.append(f"elapsed {timedelta(seconds=int(now - started))}")
    if eta is not None:
        parts.append(f"ETA {timedelta(seconds=int(eta))} (~{datetime.now() + timedelta(seconds=eta):%a %H:%M})")
    return " | ".join(parts)


def run(output_directory: Path, workers: int) -> None:
    database = open_database(output_directory)
    signature = settings_signature()
    saved = dict(database.execute("SELECT key, value FROM meta"))
    if saved and saved != signature:
        changed = ", ".join(k for k in signature if saved.get(k) != signature[k])
        raise SystemExit(f"The saved run used different settings ({changed}). Move {output_directory} away to start over.")
    with database:
        database.executemany("INSERT OR IGNORE INTO meta VALUES (?, ?)", signature.items())
        for f in discover_files():
            database.execute("INSERT OR IGNORE INTO files (key, path, stream, size) VALUES (?, ?, ?, ?)",
                             (f["key"], f["path"], f["stream"], f["size"]))
            database.execute("UPDATE files SET path = ? WHERE key = ?", (f["path"], f["key"]))
    pending = [
        {"key": key, "path": path, "stream": stream, "compressed": path.endswith(".zst"),
         "bytes_done": bytes_done, "lines_done": lines_done, "chunk_lines": CHUNK_LINES}
        for key, path, stream, bytes_done, lines_done in database.execute(
            "SELECT key, path, stream, bytes_done, lines_done FROM files WHERE done = 0 ORDER BY size DESC")
    ]
    state = progress(database)
    log(output_directory, f"=== start: {workers} workers, {len(pending)} files to scan "
                          f"({state['done_files']} of {state['total_files']} already done), output {output_directory} ===")
    if not pending:
        log(output_directory, "Nothing left to scan.")
        return

    context_ = multiprocessing.get_context("spawn")
    tasks, results, stop = context_.Queue(), context_.Queue(maxsize=2 * workers), context_.Event()
    # Tasks left in the queue after a stop must not keep the main process from exiting.
    tasks.cancel_join_thread()
    for task in pending:
        tasks.put(task)
    for _ in range(workers):
        tasks.put(None)
    processes = [context_.Process(target=worker, args=(tasks, results, stop), daemon=True) for _ in range(workers)]
    for process in processes:
        process.start()

    interrupts = []

    def request_stop(signum, frame):
        interrupts.append(signum)
        if len(interrupts) == 1:
            log(output_directory, "Stopping after the current chunks (press Ctrl-C again to stop at once)...")
            stop.set()
        else:
            for process in processes:
                process.terminate()
            raise SystemExit("Stopped at once; the chunks in progress will be scanned again on resume.")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    started, last_status, window, running, stored = time.time(), 0.0, [], workers, 0
    failed = None
    while running:
        try:
            kind, key, payload = results.get(timeout=5)
        except queue.Empty:
            kind = None
        if kind == "chunk":
            stored += commit_chunk(database, key, payload)
            if payload[2]:
                log(output_directory, f"done {key.split('/')[0]} {key.split('_from_')[-1][:10]}")
        elif kind == "exit":
            running -= 1
        elif kind == "error":
            running -= 1
            failed = f"{key}: {payload}"
            log(output_directory, f"ERROR in {failed}. Stopping; run again to resume.")
            stop.set()
        if time.time() - last_status >= STATUS_SECONDS or not running:
            last_status = time.time()
            log(output_directory, status_line(output_directory, progress(database), started, window))
            if shutil.disk_usage(output_directory).free < MIN_FREE_GB * 1e9 and not stop.is_set():
                log(output_directory, f"Less than {MIN_FREE_GB} GB free on the output disk. Stopping; free space and run again.")
                stop.set()
        if running and not any(p.is_alive() for p in processes) and results.empty():
            break
    for process in processes:
        process.join(timeout=10)
    database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    state = progress(database)
    if state["done_files"] == state["total_files"]:
        log(output_directory, "Scan complete.")
    elif failed:
        raise SystemExit(1)
    else:
        log(output_directory, "Stopped. Run again to resume.")


def show(output_directory: Path, author: str) -> None:
    database = sqlite3.connect(f"file:{output_directory / DATABASE_NAME}?mode=ro", uri=True)
    user = database.execute("SELECT comments, submissions, first_utc, last_utc FROM users WHERE author = ?", (author,)).fetchone()
    if user is None:
        raise SystemExit(f"No data for {author} (yet).")
    day = lambda utc: datetime.fromtimestamp(utc, timezone.utc).strftime("%Y-%m-%d")
    print(f"{author}: {user[0]:,} comments, {user[1]:,} submissions, {day(user[2])} to {day(user[3])}")
    print("\nFlairs:")
    for subreddit, flair, n, first, last in database.execute(
            "SELECT subreddit, flair, n, first_utc, last_utc FROM flairs WHERE author = ? ORDER BY n DESC", (author,)):
        print(f"  r/{subreddit}: {flair!r} ({n}x, {day(first)} to {day(last)})")
    counts = dict(database.execute("SELECT rule, n FROM rule_counts WHERE author = ?", (author,)))
    for rule in sorted(counts):
        rows = database.execute(
            "SELECT created_utc, subreddit, name, value, context FROM hits WHERE author = ? AND rule = ? ORDER BY created_utc",
            (author, rule)).fetchall()
        print(f"\n{rule}: {counts[rule]} hits" + (f", {len(rows)} sampled" if len(rows) < counts[rule] else ""))
        for utc, subreddit, name, value, text in rows:
            print(f"  {day(utc)} r/{subreddit} {name}" + (f" [{value}]" if value else "") + f"\n    {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "status", "show"))
    parser.add_argument("user", nargs="?")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    args = parser.parse_args()
    if args.command == "run":
        run(args.output, args.workers)
    elif args.command == "status":
        if not (args.output / DATABASE_NAME).exists():
            raise SystemExit("No run yet.")
        database = sqlite3.connect(f"file:{args.output / DATABASE_NAME}?mode=ro", uri=True)
        print(status_line(args.output, progress(database), None, []))
    else:
        if not args.user:
            parser.error("show needs a user name")
        show(args.output, args.user)


if __name__ == "__main__":
    main()
