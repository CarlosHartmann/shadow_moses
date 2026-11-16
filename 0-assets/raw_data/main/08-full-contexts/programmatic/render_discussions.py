"""Step 6 of the forest recon: the discussions as readable text, like the files in ../manual/.

Each discussion becomes one text:
- the post: subreddit, author with flair, time, title, content (or link);
- the context: the chain of comments above the initial mention;
- the discussion: the initial mention and everything below it, all branches, in thread order
  (depth first, replies by time);
- at the end, the post author's other comments in the thread that were posted before the
  discussion's last study comment (what its speakers could have seen), the earliest 50 at most.
Every comment is numbered and says which comment (or the post) it replies to, so the branching
stays readable without indentation. The post's author is marked as such wherever they comment.
Study comments (the seeds) are not marked in the text; their numbers are stored next to it.

Heavy discussions (body above 250 comments or deeper than 50 levels, 3,307 of them) are pruned:
the initial mention; for each study comment up to 20 comments above it and its first 20 replies;
the top of the context chain and its last 20 comments. A note gives the number left out. They
are real discussions, not counting games (notes.txt), so they are kept.

Heavy discussions that look like joke or novelty chains are dropped (242; decision of
2026-10-29): more than 30% of their body comments repeat another one (compared in lower case,
letters, digits and spaces only), or more than half are shorter than 25 characters.

Discussions whose submission is not in the dumps (2,554 threads) are discarded too. Both kinds are
listed with the reason.

Bots and templates (detect_bots.py; decision of 2026-10-29): study comments by bot accounts and
template comments are not study comments any more; they stay in the texts as context, and bot
accounts are marked "(bot)". Discussions left without study comments are discarded. The excluded
study comments are listed in excluded_study_comments.tsv.

Inputs: work/forest/ (build_forest.py), work/texts/ (texts_to_parquet.py), work/bots/ (detect_bots.py).
Outputs in work/discussions/: part-NNNN.parquet (one per forest bucket, one row per discussion
with its text); discarded/ and excluded/ per bucket, and at the end all of them in discarded.tsv
and excluded_study_comments.tsv. Finished buckets are skipped on a rerun.
Show one discussion: render_discussions.py --show t1_xxxxxxx
"""

from __future__ import annotations

import argparse
import html
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
HEAVY_BODY, HEAVY_DEPTH = 250, 50
JOKE_DUPLICATES, JOKE_SHORT, SHORT = 0.3, 0.5, 25
KEEP_ABOVE, KEEP_REPLIES, KEEP_CONTEXT = 20, 20, 20
MAX_POST_AUTHOR_COMMENTS = 50
DELETED = (None, "", "[deleted]")
BOTS: set[str] = set()  # bot accounts from detect_bots.py, loaded in main()

SCHEMA = pa.schema([
    ("discussion_id", pa.string()), ("link_id", pa.string()), ("subreddit", pa.string()),
    ("study_comments", pa.list_(pa.string())), ("study_numbers", pa.list_(pa.int32())),
    ("comment_ids", pa.list_(pa.string())),  # the comment behind each number: [1] is comment_ids[0]
    ("comments_total", pa.int32()), ("comments_shown", pa.int32()), ("pruned", pa.bool_()),
    ("post_author_comments_shown", pa.int32()), ("post_author_comments_left_out", pa.int32()),
    ("text", pa.string()),
])


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def to_base36(n: int) -> str:
    out = ""
    while n:
        n, digit = divmod(n, 36)
        out = DIGITS[digit] + out
    return out or "0"


def when(created_utc) -> str:
    if created_utc is None:
        return "time unknown"
    return datetime.fromtimestamp(created_utc, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def plain(text) -> str:
    """Reddit stores &, < and > as HTML entities."""
    return html.unescape(text or "").strip()


def quoted(text) -> str:
    """Bodies in backticks, as in ../manual/: one for a single line, three for longer texts."""
    text = plain(text)
    if not text:
        return "(empty)"
    if "\n" in text or "`" in text:
        return f"```{text}```"
    return f"`{text}`"


def flair(row: dict) -> str | None:
    """The flair as shown on Reddit: author_flair_text, or the richtext flair when there is no plain one."""
    for value in (row.get("flair_text"), row.get("flair_richtext")):
        if value and plain(value):
            return plain(value)
    return None


def who(row: dict, post_author: str | None) -> str:
    author = row.get("author") or "[unknown]"
    parts = [f"u/{author}" if author != "[deleted]" else "[deleted]"]
    if post_author not in DELETED and author == post_author:
        parts.append("(the post's author)")
    if author in BOTS:
        parts.append("(bot)")
    if row.get("distinguished") == "moderator":
        parts.append("(moderator)")
    if flair(row):
        parts.append(f'(flair: "{flair(row)}")')
    return " ".join(parts)


def thread_order(initial: int, parent_of: dict[int, int], times: dict[int, int]) -> list[int]:
    """The body depth first, replies by time."""
    children = defaultdict(list)
    for comment, parent in parent_of.items():
        if comment != initial:
            children[parent].append(comment)
    order, stack = [], [initial]
    while stack:
        comment = stack.pop()
        order.append(comment)
        stack.extend(sorted(children[comment], key=lambda c: (times.get(c) or 0, c), reverse=True))
    return order


def prune(initial: int, parent_of: dict[int, int], times: dict[int, int], seeds: list[int]) -> set[int]:
    children = defaultdict(list)
    for comment, parent in parent_of.items():
        if comment != initial:
            children[parent].append(comment)
    keep = {initial}
    for seed in seeds:
        keep.add(seed)
        node, steps = seed, 0
        while node != initial and steps < KEEP_ABOVE:
            node = parent_of[node]
            keep.add(node)
            steps += 1
        keep.update(sorted(children[seed], key=lambda c: (times.get(c) or 0, c))[:KEEP_REPLIES])
    return keep


def render(discussion: dict, post: dict, context: list[dict], body: list[dict], comments: dict[int, dict],
           post_author_comments: list[dict], note: str | None = None) -> tuple[str, dict[int, int], int, int]:
    """context and body: the members to show, in order. Returns the text, the number given to each
    comment id, and how many of the post author's other comments are shown and left out."""
    post_author = post.get("author")
    lines = [f"Discussion: {discussion['discussion_id']}",
             f"Post subreddit: {post.get('subreddit')}",
             f"Author: {who(post, None)}",
             f"Posted: {when(post.get('created_utc'))}",
             f"Title: {plain(post.get('title'))}"]
    if post.get("is_self") is False and post.get("url"):
        lines.append(f"Link: {post['url']}")
    selftext = plain(post.get("selftext"))
    lines.append(f"Content: {quoted(selftext)}" if selftext else "Content: No text.")
    if note:
        lines += ["", note]

    numbers: dict[int, int] = {}

    def entry(row: dict) -> list[str]:
        numbers[row["id"]] = len(numbers) + 1
        parent = row.get("parent_id") or ""
        if parent.startswith("t3_"):
            target = "to the post"
        else:
            parent_number = numbers.get(int(parent[3:], 36)) if parent.startswith("t1_") else None
            target = f"to [{parent_number}]" if parent_number else "to a comment not shown"
        return [f"[{numbers[row['id']]}] {who(row, post_author)}, {when(row.get('created_utc'))}, reply {target}:",
                quoted(row.get("body")), ""]

    def comment(member: dict) -> dict:
        return comments.get(member["id"]) or {
            "id": member["id"], "author": "[unknown]", "body": "(text not found)",
            "parent_id": ("t3_" if member["parent_is_submission"] else "t1_") + to_base36(member["parent_id"]),
            "created_utc": member["created_utc"]}

    lines.append("")
    if context:
        lines += ["Context (the comments above the discussion, only the direct chain):", ""]
        for member in context:
            lines += entry(comment(member))
    lines += ["Discussion:", ""]
    for member in body:
        lines += entry(comment(member))

    if post_author in DELETED:
        lines.append("The post's author is deleted, so their other comments in the thread cannot be identified.")
        return "\n".join(lines).rstrip() + "\n", numbers, 0, 0
    others = sorted((c for c in post_author_comments if c["id"] not in numbers),
                    key=lambda c: (c.get("created_utc") or 0, c["id"]))
    shown = others[:MAX_POST_AUTHOR_COMMENTS]
    if not shown:
        lines.append(f"The post's author, u/{post_author}, has no other comments in the thread "
                     "before the last study comment.")
    else:
        more = f"; {len(others) - len(shown)} later ones are left out" if len(others) > len(shown) else ""
        lines += [f"Other comments by the post's author, u/{post_author}, in the thread, posted before "
                  f"the last study comment ({len(shown)}, in time order{more}):", ""]
        for row in shown:
            lines += entry(row)
    return "\n".join(lines).rstrip() + "\n", numbers, len(shown), len(others) - len(shown)


def is_heavy(discussion: dict) -> bool:
    return discussion["body_size"] > HEAVY_BODY or discussion["context_size"] + discussion["body_depth"] > HEAVY_DEPTH


def is_joke_like(members: list[dict], comments: dict[int, dict]) -> bool:
    bodies = [(comments.get(m["id"]) or {}).get("body") or "" for m in members if m["role"] == "body"]
    normalised = [re.sub(r"[^a-z0-9 ]", "", b.lower()).strip() for b in bodies]
    duplicates = 1 - len(set(normalised)) / len(bodies)
    short = sum(len(b) < SHORT for b in bodies) / len(bodies)
    return duplicates > JOKE_DUPLICATES or short > JOKE_SHORT


def build_discussion(discussion, post, members, comments, post_author_rows):
    """Order (and for heavy discussions prune) the members, then render. Returns the output row."""
    initial = discussion["initial_id"]
    by_id = {m["id"]: m for m in members}
    body_members = [m for m in members if m["role"] == "body"]
    parent_of = {m["id"]: m["parent_id"] for m in body_members}
    times = {m["id"]: m["created_utc"] for m in members}
    seeds = sorted((m["id"] for m in body_members if m["is_seed"]), key=lambda c: (times.get(c) or 0, c))
    # the context chain, from the top down: walk up from the initial mention's parent
    context_ids, node = [], by_id[initial]
    while not node["parent_is_submission"] and node["parent_id"] in by_id and by_id[node["parent_id"]]["role"] == "context":
        node = by_id[node["parent_id"]]
        context_ids.append(node["id"])
    context_ids.reverse()
    order = thread_order(initial, parent_of, times)
    heavy = is_heavy(discussion)
    note = None
    if heavy:
        keep = prune(initial, parent_of, times, seeds)
        order = [c for c in order if c in keep]
        if len(context_ids) > KEEP_CONTEXT + 1:
            context_ids = context_ids[:1] + context_ids[-KEEP_CONTEXT:]
        total = discussion["context_size"] + discussion["body_size"]
        note = (f"Note: this discussion is long ({total} comments). Only the comments around the study "
                f"comments are shown; {total - len(order) - len(context_ids)} are left out.")
    last_seed_time = max((times.get(s) or 0) for s in seeds)
    earlier = [r for r in post_author_rows if (r.get("created_utc") or 0) < last_seed_time]
    text, numbers, shown, left_out = render(discussion, post, [by_id[c] for c in context_ids],
                                            [by_id[c] for c in order], comments, earlier, note)
    return {
        "discussion_id": discussion["discussion_id"], "link_id": "t3_" + to_base36(discussion["link_id"]),
        "subreddit": post.get("subreddit"),
        "study_comments": ["t1_" + to_base36(s) for s in seeds], "study_numbers": [numbers[s] for s in seeds],
        "comment_ids": ["t1_" + to_base36(c) for c, _ in sorted(numbers.items(), key=lambda item: item[1])],
        "comments_total": discussion["context_size"] + discussion["body_size"],
        "comments_shown": len(order) + len(context_ids), "pruned": heavy,
        "post_author_comments_shown": shown, "post_author_comments_left_out": left_out, "text": text,
    }


def records(con, sql: str) -> list[dict]:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def split_texts(con, texts: Path, out: Path, buckets: int) -> None:
    """The comment and submission texts by forest bucket (link_id % buckets), once."""
    marker = out / "_buckets.txt"
    if marker.exists() and int(marker.read_text()) == buckets:
        return
    out.mkdir(parents=True, exist_ok=True)
    for kind, link in (("RC", "link_id"), ("RS", "id")):
        con.execute(f"""COPY (SELECT *, {link} % {buckets} AS bucket FROM read_parquet('{texts / kind / '*.parquet'}'))
                        TO '{out / kind}' (FORMAT parquet, PARTITION_BY (bucket), OVERWRITE)""")
    marker.write_text(str(buckets))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--out-dir", type=Path, default=HERE / "work" / "discussions")
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--show", help="print the text of one discussion (after the run)")
    args = parser.parse_args()
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute("SET preserve_insertion_order = false")
    if args.show:
        (text,) = con.execute(f"SELECT text FROM read_parquet('{args.out_dir / 'part-*.parquet'}') "
                              "WHERE discussion_id = ?", [args.show]).fetchone()
        print(text, end="")
        return

    w = args.work_dir
    con.execute(f"SET temp_directory = '{w / 'duckdb_tmp'}'")
    forest = w / "forest"
    buckets = int((forest / "rows_by_bucket" / "_buckets.txt").read_text())
    by_bucket = w / "texts_by_bucket"
    split_texts(con, w / "texts", by_bucket, buckets)
    BOTS.update(author for (author,) in con.execute(
        f"SELECT author FROM read_csv('{w / 'bots' / 'bot_accounts.tsv'}', delim='\t', header=true)").fetchall())
    templates = {i for (i,) in con.execute(
        f"SELECT id FROM read_parquet('{w / 'bots' / 'template_comments.parquet'}')").fetchall()}
    log(f"{len(BOTS):,} bot accounts, {len(templates):,} template comments")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    todo = [b for b in range(buckets) if not (args.out_dir / f"part-{b:04d}.parquet").exists()]
    log(f"{buckets} buckets, {buckets - len(todo)} already done, {len(todo)} to render")
    (args.out_dir / "discarded").mkdir(exist_ok=True)
    (args.out_dir / "excluded").mkdir(exist_ok=True)
    started, written = time.monotonic(), 0
    for number, bucket in enumerate(todo, start=1):
        part = f"part-{bucket:04d}.parquet"
        discussions = records(con, f"SELECT * FROM read_parquet('{forest / 'discussions' / part}')")
        members = defaultdict(list)
        for m in records(con, f"SELECT * FROM read_parquet('{forest / 'members' / part}')"):
            members[m["discussion_id"]].append(m)
        posts = {p["id"]: p for p in records(con, f"SELECT * EXCLUDE (bucket) FROM read_parquet('{by_bucket / 'RS' / f'bucket={bucket}' / '*.parquet'}')")} \
            if (by_bucket / "RS" / f"bucket={bucket}").exists() else {}
        comments = {c["id"]: c for c in records(con, f"SELECT * EXCLUDE (bucket) FROM read_parquet('{by_bucket / 'RC' / f'bucket={bucket}' / '*.parquet'}')")} \
            if (by_bucket / "RC" / f"bucket={bucket}").exists() else {}
        post_author_rows = defaultdict(list)
        for c in comments.values():
            post = posts.get(c["link_id"])
            if post and post["author"] not in DELETED and c["author"] == post["author"]:
                post_author_rows[c["link_id"]].append(c)
        rows, discarded, excluded = [], [], []
        for discussion in discussions:
            post = posts.get(discussion["link_id"])
            own = members[discussion["discussion_id"]]
            original = [m["id"] for m in own if m["is_seed"]]
            for m in own:
                if m["is_seed"]:
                    if (comments.get(m["id"]) or {}).get("author") in BOTS:
                        cause = "bot account"
                    elif m["id"] in templates:
                        cause = "template comment"
                    else:
                        continue
                    m["is_seed"] = False
                    excluded.append(f"t1_{to_base36(m['id'])}\t{discussion['discussion_id']}\t{cause}\n")
            reason = None
            if post is None:
                reason = "submission not in the dumps"
            elif is_heavy(discussion) and is_joke_like(own, comments):
                reason = "joke-like heavy discussion"
            elif not any(m["is_seed"] for m in own):
                reason = "only bot or template study comments"
            if reason:
                discarded.append(f"{discussion['discussion_id']}\tt3_{to_base36(discussion['link_id'])}\t"
                                 f"{','.join('t1_' + to_base36(s) for s in original)}\t{reason}\n")
                continue
            rows.append(build_discussion(discussion, post, members[discussion["discussion_id"]], comments,
                                         post_author_rows[discussion["link_id"]]))
        temporary = args.out_dir / f".{part}.tmp"
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), temporary, compression="zstd")
        (args.out_dir / "discarded" / f"part-{bucket:04d}.tsv").write_text("".join(discarded))
        (args.out_dir / "excluded" / f"part-{bucket:04d}.tsv").write_text("".join(excluded))
        temporary.rename(args.out_dir / part)
        written += len(rows)
        elapsed = time.monotonic() - started
        eta = timedelta(seconds=round(elapsed / number * (len(todo) - number)))
        log(f"[{number}/{len(todo)}] bucket {bucket}: {len(rows):,} discussions ({written:,} in total), "
            f"{len(discarded)} discarded, elapsed {timedelta(seconds=round(elapsed))}, ETA {eta} "
            f"(~{datetime.now() + eta:%a %H:%M})")
    with open(args.out_dir / "discarded.tsv", "w") as handle:
        handle.write("discussion_id\tlink_id\tstudy_comments\treason\n")
        for path in sorted((args.out_dir / "discarded").glob("part-*.tsv")):
            handle.write(path.read_text())
    with open(args.out_dir / "excluded_study_comments.tsv", "w") as handle:
        handle.write("study_comment\tdiscussion_id\treason\n")
        for path in sorted((args.out_dir / "excluded").glob("part-*.tsv")):
            handle.write(path.read_text())
    log("done")


if __name__ == "__main__":
    main()
