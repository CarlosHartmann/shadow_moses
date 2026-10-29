"""Step 8 bot filter: bot accounts and template comments among all comments of the discussions.

Step 2 removed authors whose names end in "Bot", "Mod" or "Moderator" (case-sensitive). That
misses bots such as wikipedia_text_bot or remindditbot, and a name rule alone also hits people
(Accountantnotbot, Chimpbot). This filter therefore looks at what an account writes, over all
its comments in the extracted texts (study comments, discussion comments, post authors' comments).

Bot accounts (all their comments are excluded as study comments, and marked as bots in the texts):
- A, discloses being a bot: at least half of its comments (and at least 3) say so in the first
  person or carry a bot footer (DISCLOSURE below);
- B, bot name and repetitive: the name ends in "bot"/"b0t" in any case (digits or underscores may
  follow), it has at least 5 comments, and at least 30% of them repeat one of its texts or start
  with one of its six-word openings (see below) at least 5 times;
- C, repeated openings: at least 50 comments, at least 80% starting with one of its six-word
  openings that it uses at least 5 times, but under 50% full repeats (mirror and repost bots;
  moderators with removal templates have full repeats and fall under the template rule instead).
Template comments (only these comments are excluded as study comments): a text, at least 20
characters after normalising, that the same author (not a bot account) posted at least 5 times.
Normalising: lower case, links, u/ and r/ names and numbers replaced, other characters dropped.

Checked by reading samples (notes.txt): 28 of 30 bot accounts are bots (the other two are humans
posting scripted messages); 25 of 25 template study comments are templates (mostly moderators'
removal notices). People with bot-like names who write normally are not caught.

Inputs: work/texts/RC (texts_to_parquet.py), work/forest/members (build_forest.py).
Outputs in work/bots/: bot_accounts.tsv (author, reason, measures), template_comments.parquet (id,
author, is_seed).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DISCLOSURE = (r"(i'?m a bot|i am a bot|i'?m just a bot|i am just a bot|beep[ ,.!-]*boop|bleep[ ,.!-]*bloop"
              r"|this action was performed automatically|this is an automated"
              r"|automated (message|response|reply|comment)|\^\(?this bot|bot (made|created) by|/r/botwatch"
              r"|github\.com/[^ )]*bot|summoned me|opt[ -]?out|reply .?delete.? to delete)")
NORMALISED = r"""left(trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(lower(r.body),
    'https?://[^ )\]]+', 'U', 'g'), '/?\b[ur]/[a-z0-9_-]+', 'N', 'g'), '[0-9]+', '0', 'g'), '[^a-z0 ]+', ' ', 'g')), 300)"""
REPEATS, MIN_TEMPLATE = 5, 20


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=HERE / "work")
    parser.add_argument("--memory-limit", default="6GB")
    args = parser.parse_args()
    w = args.work_dir
    out = w / "bots"
    out.mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute(f"SET temp_directory = '{w / 'duckdb_tmp'}'")
    disclosure = DISCLOSURE.replace("'", "''")
    con.execute(f"""CREATE TABLE c AS
        SELECT r.id, r.author, coalesce(m.is_seed, false) AS is_seed,
               regexp_matches(lower(r.body), '{disclosure}') AS discloses, {NORMALISED} AS text
        FROM read_parquet('{w / 'texts' / 'RC' / '*.parquet'}') r
        LEFT JOIN (SELECT id, bool_or(is_seed) AS is_seed FROM read_parquet('{w / 'forest' / 'members' / '*.parquet'}')
                   GROUP BY id) m ON m.id = r.id
        WHERE r.author NOT IN ('[deleted]', '') AND r.body NOT IN ('[deleted]', '[removed]')""")
    con.execute("""ALTER TABLE c ADD COLUMN opening VARCHAR""")
    con.execute("""UPDATE c SET opening = array_to_string(list_slice(
                       string_split(regexp_replace(trim(text), ' +', ' ', 'g'), ' '), 1, 6), ' ')""")
    con.execute("CREATE TABLE texts AS SELECT author, text, count(*) AS k FROM c GROUP BY ALL")
    con.execute(f"CREATE TABLE openings AS SELECT author, opening, count(*) AS k FROM c "
                f"WHERE length(opening) >= {MIN_TEMPLATE} GROUP BY ALL")
    con.execute(f"""CREATE TABLE accounts AS
        SELECT a.author, a.comments, a.study_comments, a.disclosing,
               coalesce(t.k, 0)::DOUBLE / a.comments AS repeated,
               coalesce(f.k, 0)::DOUBLE / a.comments AS templated,
               coalesce(o.k, 0)::DOUBLE / a.comments AS same_opening,
               regexp_matches(a.author, '(?i)b[o0]t[0-9_]*$') AS bot_name
        FROM (SELECT author, count(*) AS comments, count(*) FILTER (WHERE is_seed) AS study_comments,
                     avg(discloses::INT) AS disclosing FROM c GROUP BY author) a
        LEFT JOIN (SELECT author, sum(k) AS k FROM texts WHERE k >= {REPEATS} GROUP BY author) t USING (author)
        LEFT JOIN (SELECT author, sum(k) AS k FROM texts WHERE k >= {REPEATS} AND length(text) >= {MIN_TEMPLATE}
                   GROUP BY author) f USING (author)
        LEFT JOIN (SELECT author, sum(k) AS k FROM openings WHERE k >= {REPEATS} GROUP BY author) o USING (author)""")
    con.execute("""CREATE TABLE bots AS
        SELECT *, CASE WHEN disclosing >= 0.5 AND comments >= 3 THEN 'A discloses being a bot'
                       WHEN bot_name AND comments >= 5 AND greatest(repeated, same_opening) >= 0.3
                           THEN 'B bot name and repetitive'
                       ELSE 'C repeated openings' END AS reason
        FROM accounts
        WHERE (disclosing >= 0.5 AND comments >= 3)
           OR (bot_name AND comments >= 5 AND greatest(repeated, same_opening) >= 0.3)
           OR (comments >= 50 AND same_opening >= 0.8 AND templated < 0.5)""")
    con.execute(f"""COPY (SELECT author, reason, comments, study_comments, round(disclosing, 3) AS disclosing,
                                 round(repeated, 3) AS repeated, round(templated, 3) AS templated,
                                 round(same_opening, 3) AS same_opening
                          FROM bots ORDER BY study_comments DESC, comments DESC)
                    TO '{out / 'bot_accounts.tsv'}' (DELIMITER '\t', HEADER)""")
    con.execute(f"""COPY (SELECT c.id, c.author, c.is_seed FROM c JOIN texts USING (author, text)
                          WHERE texts.k >= {REPEATS} AND length(c.text) >= {MIN_TEMPLATE}
                            AND c.author NOT IN (SELECT author FROM bots))
                    TO '{out / 'template_comments.parquet'}' (FORMAT parquet)""")
    for reason, accounts, comments, study in con.execute(
            "SELECT reason, count(*), sum(comments), sum(study_comments) FROM bots GROUP BY 1 ORDER BY 1").fetchall():
        print(f"{reason}: {accounts:,} accounts, {comments:,} comments, {study:,} study comments")
    (templates, study) = con.execute(f"SELECT count(*), count(*) FILTER (WHERE is_seed) FROM "
                                     f"read_parquet('{out / 'template_comments.parquet'}')").fetchone()
    print(f"template comments: {templates:,}, of which {study:,} study comments")


if __name__ == "__main__":
    main()
