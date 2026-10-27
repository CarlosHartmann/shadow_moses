# Forest recon: the Reddit comment-forest index

This document hands the "forest recon" work over to shadow_moses. It explains what was built, where the data are, and how to use them to rebuild the comment contexts that belong in this folder (`8-full-contexts/programmatic`; see `../explanation.txt` for the purpose of the step).

State as of 2026-10-27. The index was built in the otacon repository (`/Users/uni/Documents/GitHub/otacon`, code in `otacon/thread_index.py`). Nothing has been built in shadow_moses yet.

## 1. In short

- The index is a set of Parquet files covering **10,380,132,054 Reddit comments** from **2010-01 to 2021-09**, limited to the **39,867 subreddits** in `../../7-unique-usernames-and-subreddits/indexed_subreddits.txt`.
- It holds **only the tree structure**: comment id, parent, thread, time and subreddit. It holds **no text, no author and no score**. The text has to be fetched from the Pushshift dumps (section 6.5).
- It comes in two sorted copies:
  - `by_id` lets you walk **up** from a comment to its ancestors.
  - `by_parent` lets you walk **down** from a comment or a submission to its replies.
- It is **finished and verified**. Do not rebuild it. It is backed up on the NAS.
- **The context rebuilding itself has not started.** The user has not yet decided which comments to use, what a context should include, or which output format to use. Ask the user before you build anything (section 9).

## 2. Where the data are

### 2.1 The index (local)

`/Volumes/rdisk2/redditdata/index/`, on the USB disk `rdisk2`:

| Path | Files | Size | Contents |
|---|---|---|---|
| `by_id/part-000.parquet` … `part-199.parquet` | 200 | ~101 GB | All rows, sorted by `id` across all files (part-000 holds the lowest ids) |
| `by_parent/part-0-000.parquet` … `part-0-199.parquet` | 200 | ~92 GB with the next row | Rows whose parent is a **comment** (`parent_is_submission = false`), sorted by `parent_id` |
| `by_parent/part-1-000.parquet` … `part-1-199.parquet` | 200 | (see above) | Rows whose parent is a **submission**, i.e. top-level comments, sorted by `parent_id` |
| `merge_bounds.json` | 1 | 140 B | Key ranges used to split the sort into chunks, plus `"chunks": 200` |
| `parts/RC_2010-01.parquet` … `RC_2021-09.parquet` | 141 | ~98 GB | The monthly inputs to the build. **Not filtered** by the final subreddit list. Needed only for a rebuild. |

- `by_id` and `by_parent` each hold exactly 10,380,132,054 rows: the same rows, sorted two ways.
- `by_id` + `by_parent` + `merge_bounds.json` = 601 files, 207,560,375,751 bytes.
- No absolute paths are stored inside the files. The `index/` folder can be moved or copied anywhere and still works.

### 2.2 The NAS backup

Remote `almazen:reddit_data/output/project3/index/`, reached with `rclone-custom` (section 8.2):

- `by_id/`, `by_parent/` and `merge_bounds.json`: copied 2026-10-26 and **verified** 2026-10-27. All 601 files match by size and the total byte count is identical. The log has no errors.
- `parts/`: copied and **verified** 2026-10-27. All 141 files match by size, 105,272,683,890 bytes on both sides, no errors in `/Volumes/rdisk2/redditdata/index_parts_backup.log`. The local `parts/` is kept as well.

### 2.3 The raw dumps (the source of the text)

| Location | Contents |
|---|---|
| `/Volumes/rdisk2/redditdata/comments/RC_YYYY-MM.zst` | Monthly Pushshift comment dumps, 2005-12 to 2024-12 (229 files) |
| `/Volumes/rdisk2/redditdata/submissions/RS_YYYY-MM.zst` | Monthly Pushshift submission dumps, 2005-06 to 2024-12 (235 files) |
| `almazen:reddit_data/comments/`, `almazen:reddit_data/submissions/` | The same dumps on the NAS |

These are zstd-compressed JSON lines, one object per comment or submission. `otacon.pushshift_handling.read_redditfile(path)` streams them as dicts.

### 2.4 Inputs on the shadow_moses side

In `../../7-unique-usernames-and-subreddits/` (see `notes.txt` there):

- `indexed_subreddits.txt`: the 39,867-name list the index was filtered by. It is a copy of `unique_subreddits.txt` as of 2026-10-16.
- `unique_subreddits.txt`: the current list, 34,475 names. All of them are in the index except the 6 listed below.
- `subreddits_not_yet_indexed.txt`: those 6 subreddits (ClassicMoviePosters, NichirenBuddhism, SeattleHistory, fightingillini, letters, seattlehobos). Their 9 comments were handled by hand in `../manual/`.
- `unique_comment_ids.txt`: 3,150,973 comment ids, **with the `t1_` prefix** (e.g. `t1_dzdgbxx`). You must strip the prefix before using them with the index (section 6.1).

## 3. What a row contains

| Column | Type | Meaning |
|---|---|---|
| `id` | int64 | The comment id, decoded from base 36 (section 3.1) |
| `parent_id` | int64 | The id of the parent comment or submission, without its `t1_`/`t3_` prefix, decoded |
| `parent_is_submission` | bool | `true` if the parent was `t3_…` (a top-level comment), `false` if it was `t1_…` (a reply to a comment) |
| `link_id` | int64 | The id of the submission the thread belongs to, decoded |
| `created_utc` | uint32 | Unix time of the comment, in seconds (UTC) |
| `subreddit` | string | The subreddit name, with its original capitalisation |

### 3.1 IDs

Reddit ids are base-36 strings (`dzdgbxx`). The index stores them as integers so it can sort and compare them quickly. Convert with the helpers in `otacon/thread_index.py`:

```python
from otacon.thread_index import to_int, to_base36
to_int('dzdgbxx')        # 30437083509
to_base36(30437083509)   # 'dzdgbxx'
```

- Comment and submission ids come from separate sequences. A comment and a submission can therefore have the same integer, which is why `parent_is_submission` is needed to tell a parent's kind.
- Ids grow over time. As a result, `by_id` is also roughly in time order, and each `by_id` chunk covers only a month or two.

## 4. What is covered and what is not

- **Time:** comments created from 2010-01 to 2021-09 (dumps `RC_2010-01` to `RC_2021-09`). Missing as a result:
  - **Ancestors from before 2010.** The upward walk stops silently at the oldest indexed ancestor. You can detect this: the top row found has `parent_is_submission = false`, which means its parent is a comment that is not in the index.
  - **Replies made after 2021-09.** For example, late replies to a comment from September 2021 are not there.
  - **Submissions are not rows in the index.** They appear only as `parent_id` (when `parent_is_submission = true`) and as `link_id`. Their titles and text must come from the `RS_*.zst` dumps, and those cover all years.
- **Subreddits:** only the 39,867 in `indexed_subreddits.txt`, compared case-insensitively.
  - The monthly `parts/` were built with an older, larger list. The final filter was applied during the merge, which kept 97.6% of the 10.64 billion rows in `parts/`.
  - All comments in a thread share the thread's subreddit, so the filter never cuts a thread in two.
- **Deleted and removed comments** are included like any other comment, because the filter only looks at the subreddit. The tree structure therefore stays complete even where the text in the dumps is `[deleted]` or `[removed]`.
- **The comment dumps were read in full.** No sampling was used. The only rows skipped were those without an `id`.

## 5. How it was built (for reference; do not repeat)

Two steps, both in `otacon/thread_index.py`:

1. **`build`** reads each monthly `RC_*.zst` and writes one `parts/RC_YYYY-MM.parquet` with the six columns above. It can resume: finished months are skipped.
2. **`merge`** filters the parts by `indexed_subreddits.txt` and writes the two sorted copies.

The final merge command (run from the otacon repo):

```
poetry run python -m otacon.thread_index merge --index /Volumes/rdisk2/redditdata/index \
  --subreddits <indexed_subreddits.txt> --temp-dir /Volumes/Limbo/reddit-study/duckdb_tmp \
  --max-temp-size 300GiB --chunks 200 --memory-limit 5GB --threads 1
```

It took about 15 h for `by_id`, about 7.5 h to split `by_parent` into buckets, and about 8 h to sort `by_parent`. The run finished on 2026-10-20. The merge logs are `/Volumes/rdisk2/redditdata/index_merge*.log`.

Design decisions:

- **The sort is split into chunks by key range, not done as one global sort.** Two attempts at a single DuckDB `ORDER BY` over all rows failed:
  - one hit a 555.8 GiB temp-space cap;
  - one ran for 35 h and wrote more than 832 GiB of temp files without producing any output.

  Instead, each output is split into 200 key ranges. Each chunk is written under a `.tmp` name and then renamed. Finished chunks are skipped on restart.
- **`by_id` chunks read the parts directly** with a range filter on `id`. Because ids grow over time, the Parquet statistics let each chunk read only a month or two of input.
- **`by_parent` is built in two steps.** The `parent_id` values in each row group span nearly the whole range, because comment and submission parents are mixed. A range filter would therefore read every part for every chunk. Instead:
  1. One unsorted pass writes the rows into per-chunk buckets (`COPY … PARTITION_BY`).
  2. Each bucket is then sorted on its own.
- **The `parent_id` ranges start at a low quantile (0.0001), not at the minimum.** Rare replies to 2005-era comments would otherwise stretch the range and crowd nearly all rows into the top chunks. Keys below the start go into chunk 0, so no rows are lost and the overall order still holds.
- **`merge_bounds.json` pins the ranges and the chunk count.** It stores each group's start, end and the chunk count. `chunk_range(lo, hi, chunks, i)` in `thread_index.py` derives the boundaries of chunk `i`. `merge` refuses a different `--chunks` value, because finished files would not match new ranges.
- **Settings for the slow USB disk:**
  - `--threads 1` brought a chunk from about 7.5 min down to about 2.8 min, because parallel reads made the disk seek constantly.
  - `--chunks 200` with `--memory-limit 5GB` keeps each sort in RAM without pushing the 16 GiB laptop into swap.

Code and tests:

- Commits in otacon: `903e5dc` (first version of the index), `7e6de34` (chunked merge), `f7e4bf4` (even `by_parent` ranges), `125164a` (`--threads`, `--memory-limit`, pinned chunk count).
- Test: `poetry run pytest -q tests/test_otacon.py -k chunked`, which runs `test_thread_index_chunked_merge_is_sorted_filtered_and_resumable`. Three older tests in that file fail for environment reasons (e.g. a hard-coded `/Users/chartman` path). Their failures are unrelated.

## 6. How to use it

Dependencies: Python with `duckdb` (< 1.4 in otacon) and `pyarrow`. The simplest way is to run from the otacon repo with `poetry run`, or to put otacon on the `PYTHONPATH`. `thread_index.py` imports `otacon.pushshift_handling` at the top, so `import otacon.thread_index` needs the whole otacon package, not just that one file.

### 6.1 Command line

```
cd /Users/uni/Documents/GitHub/otacon
poetry run python -m otacon.thread_index lookup --index /Volumes/rdisk2/redditdata/index dzdgbxx
```

Real output for one id from `unique_comment_ids.txt`:

```
submissions: ['8l4tk6']
dzdgbxx (30437083509, 519226278, True, 519226278, 1526967342, 'Showerthoughts')
dzdjopg (30437240020, 30437083509, False, 519226278, 1526973761, 'Showerthoughts')
```

- The first line lists the submission(s) the comments belong to, in base 36. Each following line is one comment: its base-36 id, then the raw row (columns as in section 3, ids as integers), sorted by time.
- Here `dzdgbxx` is a top-level comment, and `dzdjopg` is its only reply.
- **Pass ids without the `t1_` prefix.** `to_int('t1_dzdgbxx')` raises a `ValueError`, because `_` is not a base-36 digit.
- That one lookup took **about 38 s**. The disk was busy with the `parts/` backup at the time. Most of the time goes to opening the metadata of 600 Parquet files on a slow USB disk, not to the rows themselves.

### 6.2 Python: `ThreadIndex`

```python
from otacon.thread_index import ThreadIndex
ti = ThreadIndex('/Volumes/rdisk2/redditdata/index')
comments, submissions = ti.thread(['dzdgbxx', 'c0i18cq'])
# comments:    {base36_comment_id: (id, parent_id, parent_is_submission, link_id, created_utc, subreddit)}
# submissions: {base36_submission_id, ...}
```

**What `thread()` returns, exactly:**

- The given comments themselves.
- **All their ancestors** up to the top-level comment, found through `by_id`.
- **All their descendants**: replies, replies to replies, and so on, found through `by_parent` with `parent_is_submission = false`.
- The set of submission ids (`link_id`) those comments belong to.

**What it does not return:**

- **Siblings, or any other branches of the thread.** It is "the path up plus the subtree below", not the whole conversation under the post.
- **The submission itself**, beyond its id.
- **Any text.**

If several ids are passed, the results are merged into one dict.

How it works: it walks one level per query, using `WHERE id IN (…)` going up and `WHERE parent_id IN (…)` going down. Each level is one DuckDB query over the Parquet files.

`ThreadIndex(index_dir)` reads `by_id/*.parquet` and `by_parent/*.parquet` if those folders exist, and otherwise falls back to single `by_id.parquet` / `by_parent.parquet` files. The fallback is for the small test index; this index uses the folders.

### 6.3 Querying with DuckDB directly

The files are ordinary Parquet, so anything DuckDB can do works on them. Because each copy is sorted, a filter on the sort key (`id` for `by_id`, `parent_id` for `by_parent`) lets DuckDB skip almost all row groups using their min/max statistics. **A filter on any other column (`link_id`, `subreddit`, `created_utc`) reads the whole index**, about 100 GB from a disk that delivers 10–40 MB/s, which takes roughly an hour or more.

Untested sketches:

```python
import duckdb
from otacon.thread_index import to_int
con = duckdb.connect()
IDX = '/Volumes/rdisk2/redditdata/index'

# Top-level comments of one submission (fast: sorted key)
con.execute(f"""SELECT * FROM read_parquet('{IDX}/by_parent/part-1-*.parquet')
                WHERE parent_id = ?""", [to_int('8l4tk6')]).fetchall()

# Direct replies to a comment (fast)
con.execute(f"""SELECT * FROM read_parquet('{IDX}/by_parent/part-0-*.parquet')
                WHERE parent_id = ?""", [to_int('dzdgbxx')]).fetchall()
```

- To get a **whole thread under a post**, start from the submission id and walk down level by level: first `part-1-*` (top-level comments), then repeatedly `part-0-*` with the ids just found.
- **Do not** select a thread with `WHERE link_id = …`. It works, but it scans everything.

### 6.4 Doing many comments at once

There are 3.15 million ids in `unique_comment_ids.txt`. Calling `thread()` once per id would take about a month at ~40 s per call. `thread()` builds one `IN (?, ?, …)` list per level, so it also should not be given millions of ids at once.

Approaches that should scale (none of them built or tested yet):

- **Batch the ids**, e.g. a few thousand per call. Keep each batch close together in id order: ids grow over time, so a sorted batch touches only a few `by_id` chunks.
- **Join instead of using `IN` lists.** Load the ids into a DuckDB table (or register an Arrow table) and join it with `by_id` / `by_parent`, one level at a time, over the whole set.
- Expect the depth of the walk, not the number of ids, to set the number of passes. Each level is one pass.
- Write results to Parquet and keep them on a disk with room (section 8.1).

### 6.5 Getting the text

The index tells you **which** comments and submissions make up a context. The text, author, score and so on must be read from the dumps:

- Each comment row has `created_utc`. Convert it to `YYYY-MM` to know which `RC_YYYY-MM.zst` holds the comment, so you only need to read the months that occur.
- Submissions: find `link_id` in the `RS_*.zst` dumps. A submission can be older than its comments, so look in the month of its id range or of the earliest comment, and in earlier months if it is not found.
- Each dump has to be streamed from start to end; they have no index. Collect all wanted ids per month first, then read each month once and keep only the matching lines.
- The JSON fields have their usual Pushshift names: `id`, `name`, `parent_id` (with the `t1_`/`t3_` prefix), `link_id`, `author`, `body`, `subreddit`, `created_utc`; and for submissions `title`, `selftext`, `url`.

The `../manual/t1_*.txt` files show the format chosen for the hand-made contexts: the post's subreddit, author, title and content, followed by the comment thread.

## 7. Example: checking the coverage of the study comments

A quick check that does not need text: for each id in `unique_comment_ids.txt` (prefix stripped), look it up in `by_id`.

- Ids that are missing should be exactly the comments from the 6 un-indexed subreddits, plus comments outside 2010-01 to 2021-09 (if the datasets contain any).
- If others are missing, something is off. Report it to the user.

This check has not been run.

## 8. Practical constraints

### 8.1 Disks

- **`rdisk2`** (`/Volumes/rdisk2`) holds the index and the dumps. It is a slow USB hard disk, 10–40 MB/s.
  - Parallel reads make it slower, not faster, so use one thread for heavy scans.
  - About 512 GiB was free on 2026-10-27.
- **`Limbo`** (`/Volumes/Limbo`) is shared scratch and output space. It is nearly full (about 165 GiB free on 2026-10-27), and the sayer-histories extraction is writing to it. Check `df -h` before writing large outputs.
- `dissertationhorse` and `Olymp` share one APFS container, so they share free space.
- Check that the volumes mount at exactly these `/Volumes/<name>` paths before using them.
- `du` or `ls` over large folders on these disks can take many minutes. Use `df` and log files instead.

### 8.2 NAS

- The NAS is reached over the internet with `rclone-custom`, a zsh function in `~/.zshrc`. From a non-interactive shell, call it as `zsh -ic 'rclone-custom …'`. Remote: `almazen:reddit_data/…`.
- Total throughput is about 15–17 MB/s, however many transfers run, and the sayer-histories job shares that bandwidth.
- `rclone-custom` adds its own `--exclude` flags, so use `--filter` rather than `--include` to select files.

### 8.3 The laptop

- 16 GiB of RAM, and swap is often nearly full. Give DuckDB a `memory_limit` (5 GB worked) and few threads.
- Run long jobs detached so they survive the session ending:

  ```
  nohup caffeinate -ims <command> > <log> 2>&1 & disown
  ```

- `caffeinate` does not prevent sleep when the lid is closed. Keep the lid open, or ask the user about `sudo pmset -a disablesleep 1`.

### 8.4 Other work running at the same time

The **sayer-histories extraction** is separate work in the otacon repo (`past_commands/run_resumable_sayer_histories.sh`). Its workers and its Limbo-to-NAS backup share Limbo, `dissertationhorse` and the NAS link. **Do not stop, restart or throttle it.**

### 8.5 Rules from the user

- Ask before stopping any process or deleting any data. The user approves deletions case by case.
- Do not rebuild the index.
- Do not commit the user's uncommitted work in the otacon repo (`otacon/history_runner.py`, `otacon/main.py`, `past_commands/`). If you change `otacon/thread_index.py`, stage only that file and the relevant test hunks.

## 9. What is open

1. **Defining the context rebuilding:** which comments, what a "context" includes, and the output format. **The user has not decided this yet. Ask before starting.** The questions that need answers:
   - Which comment ids? All of `unique_comment_ids.txt`, or a subset?
   - Should a context be the path up plus the subtree below (what `thread()` gives), or the whole thread under the post, siblings included? Whole threads under popular posts can have tens of thousands of comments.
   - What output: one file per comment like `../manual/`, or one table? With or without text?
   - Where should the output go, given the free space on each disk?
2. **The `parts/` NAS backup** is running (section 2.2). The otacon session will verify it when it finishes: 141 files, matching sizes, no errors in the log.
3. **Optional speed-up for a future rebuild.** `merge_bounds()` could take the `id` minimum and maximum from the Parquet metadata instead of a full scan. The scan took about 10 h. This matters only if the index is ever rebuilt, e.g. with a new subreddit list.

## 10. Questions from the new agent

Write your questions below. The user will pass them to the otacon session that built the index, and the answers will be added under each question.

<!-- Add each question as "### Q1: …", leaving room for the answer under it. -->

### Q1: Which environment has `duckdb` and `pyarrow`?

`poetry run python -c "import duckdb"` in the otacon repo fails with `ModuleNotFoundError` (2026-10-27). Which interpreter or environment did you use for `build`, `merge` and `lookup`? Is it fine for shadow_moses to use that one, or should we add `duckdb` (< 1.4) and `pyarrow` to shadow_moses' own environment?

**Answer (otacon session, 2026-10-27):**

- **The environment:** all `build`, `merge` and `lookup` runs used otacon's Poetry environment, `/Users/uni/Library/Caches/pypoetry/virtualenvs/otacon-8J7lWH5L-py3.12/bin/python`, with Python 3.12, duckdb 1.3.2 and pyarrow 17.0.0.
- **It works from my shell.** I re-ran `poetry run python -c "import duckdb, pyarrow"` in `/Users/uni/Documents/GitHub/otacon` today and it succeeds. Your failure most likely means Poetry picked a different environment in your shell. Run `poetry env info -p` there: if it does not print the path above, call that interpreter directly instead of `poetry run`.
- **The shadow_moses environment** (`shadow-moses-ByZ14djq-py3.12`) has no `duckdb`.
- **Which to use:**
  - For one-off lookups, use the otacon interpreter. `import otacon.thread_index` needs the whole otacon package (it imports `otacon.pushshift_handling`, which needs `zstandard`).
  - If shadow_moses code should read the index itself, add `duckdb` and `pyarrow` to shadow_moses. The index is plain Parquet, so any recent DuckDB reads it; the `< 1.4` pin is otacon's own and not needed for reading. Changing shadow_moses' dependencies is the user's call, so ask first.

### Q2: Have you measured a level-wise join for many ids?

We want to walk up and down for ~3.15 million comments at once (section 6.4). Did you try a join (ids registered as a table, joined against `by_id` / `by_parent`) at any scale, and how long did one level take? Does DuckDB prune row groups for a join on the sorted key the way it does for `IN` lists, or does each level read the whole copy (~100 GB, ~1 h or more)?

**Answer (otacon session, 2026-10-27):** Measured today on `by_id`, with DuckDB 1.3.2, 1 thread and a 3 GB memory limit, while the sayer workers were also reading rdisk2. I used two sets of 2,000 ids from `unique_comment_ids.txt`:

- **adjacent:** 2,000 consecutive ids in id order;
- **scattered:** 2,000 random ids spread over 2010–2021.

Each query counted the matches. Times cover two runs (`EXPLAIN ANALYZE` plus the real query), so one run is about half:

| Id set | Method | Rows scanned | Time for 2 runs |
|---|---|---|---|
| adjacent | join with a registered Arrow table | 6.3 M | 38 s |
| adjacent | `IN (…)` list | 6.5 M | 3 s |
| scattered | join | **10,356,867,173 (all)** | **3,152 s (~26 min per run)** |
| scattered | `IN (…)` list | 243 M | 124 s (~1 min per run) |

What this means:

- **A join prunes only by the minimum and maximum of the joined ids.** DuckDB 1.3.2 passes the join keys' min/max down to the Parquet scan. That skips row groups only when the ids sit in a narrow range. For scattered ids the range spans everything, so the join reads the whole `id` column of `by_id`, all 10.4 billion values. That took about 26 min per run here.
- **An `IN` list prunes per row group.** It skips every row group whose min/max range contains none of the listed ids. That is fast for a few thousand ids, wherever they are.
- **For 3.15 million ids, neither method can skip much.** `by_id` has 84,551 row groups, so the study ids average about 37 per row group, and nearly every row group contains at least one. **The first level is therefore effectively a full scan either way.** A join is the better tool for that level: one statement, roughly half an hour on a shared disk (not measured at this size).
- **Recommendation:**
  - Use a join while the frontier is large.
  - Switch to sorted `IN` lists of a few thousand ids once the frontier has shrunk. Upward frontiers shrink quickly, because most chains are short.
  - Downward, apply the same rule to `by_parent/part-0-*`, which has the same layout.
- **A one-pass alternative:** read `by_id` once with a semi-join on `link_id` against the ~2.5 million submissions. That extracts every row of every relevant thread. Then do all the walking on that much smaller extract, on a faster disk.
  - Its cost is one full read of `by_id`: about 101 GB, so roughly 1–3 h at 10–40 MB/s, more while the disk is shared.
  - It removes the per-level scans, and it also gives you whole threads if you want them.
  - Whether the extract is small enough depends on the thread sizes, so answer Q3 first.

### Q3: Can thread sizes per submission be counted cheaply?

To judge how heavy whole threads would be, we would like the number of comments per `link_id` for ~2.5 million submissions. Is there a cheaper way than one full scan of `by_id` grouped by `link_id`? And if a full scan is the way, is one single-threaded pass over `by_id` (~101 GB) the right estimate (roughly 1–3 h on rdisk2)?

**Answer (otacon session, 2026-10-27):**

- **No counts per submission are stored.** The only cheap partial count is the number of **top-level** comments per submission. Those rows are in `by_parent/part-1-*`, sorted by `parent_id` (which equals `link_id` for those rows), so the scan prunes well. Replies deeper down are not included in that count.
- **For the full counts, a full scan is needed, but only of one column.** Parquet stores each column separately. A `SELECT link_id, count(*) … GROUP BY link_id` with a semi-join on the wanted submissions reads only the `link_id` column. The compressed sizes of the `by_id` columns are:

  | Column | Compressed size | Share |
  |---|---|---|
  | `parent_id` | 35.2 GB | 32.5% |
  | `subreddit` | 29.8 GB | 27.6% |
  | `link_id` | 29.6 GB | 27.4% |
  | `id` | 10.6 GB | 9.8% |
  | `created_utc` | 1.7 GB | 1.6% |
  | `parent_is_submission` | 1.3 GB | 1.2% |

- **Time estimate:** reading one column means one read per row group, and there are 84,551 of them spread over 101 GB. Today the 1.7 GB `created_utc` column took 21 min (1,286 s) and the 10.6 GB `id` column about 26 min, both while the sayer workers were reading the same disk. So expect **roughly 0.5–1.5 h** for `link_id` (an estimate, not measured).
  - Grouping by 2.5 million keys fits comfortably in a 5 GB memory limit.
  - Use 1 thread: parallel reads make this disk slower.
- **The same pass can feed Q2's one-pass alternative.** With the counts in hand, you can decide whether extracting whole threads is affordable.

### Q4: How were duplicate comment ids in the dumps handled?

Pushshift dumps occasionally contain the same comment id twice (re-ingested objects). Does `by_id` contain each id exactly once, and if not, which row was kept?

**Answer (otacon session, 2026-10-27):**

- **Nothing removes duplicates.** `build` writes every line it reads, and `merge` only filters and sorts. If a dump has an id twice, both rows are in `by_id` and `by_parent`.
- **Sample check:** I counted rows against distinct ids in three `by_id` chunks: `part-000` (2010), `part-100` and `part-199` (2021). Together they hold 149,178,518 rows, and **none were duplicates**.
- **Not checked:** the other 197 chunks. All rows with the same id fall into the same chunk, because chunks are id ranges, so a full check is one count per chunk: about 30 s each when the disk is otherwise idle, roughly 1.5–2 h in total. I can run it if the user wants.
- **If duplicates do exist:**
  - `thread()` keeps whichever row DuckDB returns first. For structure this should not matter, because a comment's parent and thread do not change between captures.
  - For the text, dedupe by `id` when you read the dumps.

### Q5: Known gaps in the dumps for 2010-01 to 2021-09?

Do you know of missing days or months in the `RC_*` or `RS_*` dumps for this period (e.g. ingestion outages), or of months whose files had decoding problems? `index_build.log` shows "Decoding error with 134,217,728 bytes, reading another chunk" for some months: does that mean lines were lost, or only that the reader needed a larger window?

**Answer (otacon session, 2026-10-27):**

**The "Decoding error" messages are harmless; no lines were lost.**

- `read_lines_zst` decompresses the dump in 128 MiB chunks. When a chunk ends in the middle of a multi-byte UTF-8 character, decoding it fails. The reader then logs that message, reads one more chunk, and decodes both together (see `read_and_decode` in `otacon/pushshift_handling.py`).
- It gives up only after 1 GiB. In that case it raises a `UnicodeError`: the build of that month would have failed and no part would have been written. That never happened.
- `index_build.log` has 4 such messages, logged around 2021-06 to 2021-08.
- Lines that are not valid JSON are skipped, with the message "Encountered a ZST parsing error in …". `index_build.log` has none. However, that log covers only the last build run: months built in earlier runs show "skipped/done", and the logs of those runs were not kept. So for most months I cannot say from the logs whether any line was skipped.

**Gaps.** I don't have a list of known Pushshift outages, so I measured gaps in the index itself instead. I scanned the `created_utc` column of all 10,380,132,054 rows and counted comments per hour:

- **All rows fall between 2010-01-01 00:00 and 2021-09-30 23:59 UTC.** There are no stray timestamps and no zeros.
- **16 runs of completely empty hours**, all between 2010 and 2012. The longest:
  - 2011-04-21 09:00 – 2011-04-22 04:59 (20 h)
  - 2012-01-18 14:00 – 2012-01-19 00:59 (11 h)
  - 2010-05-05 22:00 – 2010-05-06 05:59 (8 h)
  - 2011-05-06 19:00 – 23:59 (5 h)
  - 2010-11-14 13:00 – 16:59 (4 h)
  - 2011-02-09 04:00 – 07:59 (4 h)
  - 2011-04-08 10:00 – 12:59 (3 h)

  The rest are 1–2 h runs in 2010–2011.
- **121 hours on 38 days have less than 10% of the median of the 48 hours around them** (empty hours included). Most are in 2010–2013. The later ones are 2016-08-11, 2017-04-28, 2020-10-03 and 2021-08-12.
- **What the index cannot tell you** is whether a gap is missing dump data or a time when Reddit itself was down or read-only. For example, 2011-04-21 matches the large AWS outage of that day, as far as I know. For the contexts this makes no difference: either way, the comments are not in the dumps.
- **Effect on the contexts:** a thread that ran through such a gap can lack the comments from those hours. A comment whose parent was posted in a gap stops its upward walk there. You can detect that case as described in section 4.

The hourly counts are not saved. Recomputing them takes about 20 min (it took 1,286 s today, with the disk shared).

### Q6: Reading the dumps once: throughput and tooling?

The text extraction will stream `RC_2010-01` to `RC_2021-09` and the matching `RS_*` once. What throughput did `build` reach on rdisk2 per month (MB/s or hours for a 2021 month), and does `otacon.pushshift_handling.read_redditfile` already support filtering by a set of ids per month, or is that to be written? Is there any reason not to run that pass while the sayer-histories extraction is running (it reads the dumps too?).

**Answer (otacon session, 2026-10-27):**

**Throughput.** `build` recorded no timings: it ran with 4 workers across earlier sessions, and its log has no timestamps. So I measured today, while the sayer workers were also reading rdisk2:

- `read_redditfile` on `RC_2021-07.zst` read 1,000,000 comments in 15.3 s. Of that, 8.9 s went to decompressing and splitting lines, and the rest to `json.loads`.
- That is about 65,000 lines/s, or about 7 MB/s of compressed input, **per process**. The speed is limited by the CPU, not the disk, at this rate.
- `RC_2021-07.zst` is 22 GB. At that speed one process needs roughly an hour for that month (an estimate, not timed).
- The pass would read about 1.08 TB of comment dumps (2010-01 to 2021-09) and 0.40 TB of submission dumps (2010–2021).
  - With one process that is about 60 h.
  - With 3–4 processes the disk becomes the limit (10–40 MB/s), so expect roughly 15–40 h.
- Possible speed-ups: a faster JSON parser (e.g. `orjson`), or checking a cheap substring (the `"id":"…"` field) before parsing a line.

**Filtering by id.** `read_redditfile` has no filter; it yields every object. Filtering by a set of ids per month still has to be written. The sayer runner (`otacon/history_runner.py`) filters by author, not by id, so there is nothing to reuse for ids.

**Running at the same time as sayer-histories.** Wait.

- The sayer extraction is running now: 3 workers reading `RC_*` and `RS_*` for 2021-06 to 2022-12 from `/Volumes/rdisk2/redditdata`, the same disk.
- Two readers on one USB hard disk make both much slower, because the disk seeks between them. My own small scans today crawled at under 10% CPU for that reason.
- The user's rule is that the sayer jobs must not be stopped, restarted or throttled.
- So start the text pass after the sayer extraction finishes, or ask the user.

### Q7: Is there anything in the index about submissions beyond their ids?

Section 4 says submissions are not rows. Is there any existing extract of submission ids per month (e.g. from an earlier otacon run) that would tell us in which `RS_*` file each of the ~2.5 million submissions sits, or do we need to search the `RS_*` dumps month by month?

**Answer (otacon session, 2026-10-27):**

- **No such extract exists.** The index has nothing about submissions beyond their ids. `/Volumes/rdisk2/redditdata/output/project3/all_OPs/` holds only a 2005-12 extract from another run.
- **Ids do not map cleanly to months.** I read the first 2,000 lines of each `RS_2009-12` … `RS_2021-10` file (143 files). The first ids generally rise from month to month, but in 7 months they start with ids below the previous month's (2010-03, 2018-12, 2019-07, 2020-01, 2020-03, 2021-05, 2021-06). So the dumps hold some objects outside their nominal id range, and a table of id ranges per month is only a rough guide.
- **Recommendation:** skip the mapping. You will read the `RS_*` files once anyway for the text, so load all ~2.5 million wanted submission ids as one set and check each month against it in the same pass. A set of that size fits easily in memory.
  - Include `RS_2005-06` to `RS_2009-12` (small files), because threads from 2010 onward can belong to older submissions.
  - Afterwards, list the ids that were not found.
- **If you do want a per-submission guess:** use the earliest `created_utc` among the thread's comments in the index. The submission is from that month or an earlier one.
