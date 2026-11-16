import sys
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "08-full-contexts" / "programmatic"
)
sys.path.insert(0, str(SCRIPT_DIR))

import render_discussions as rd  # noqa: E402

POST = {"id": 1000, "author": "poster", "subreddit": "test", "created_utc": 0, "title": "A &amp; B",
        "selftext": "", "is_self": True}


def member(cid, parent, role, seed=False, time=None, top=False):
    return {"id": cid, "parent_id": parent, "parent_is_submission": top, "role": role, "is_seed": seed,
            "created_utc": time if time is not None else cid, "depth": None}


def comment(cid, parent, author="someone", body="text", time=None, top=False):
    return {"id": cid, "parent_id": ("t3_" if top else "t1_") + rd.to_base36(parent), "author": author,
            "body": body, "created_utc": time if time is not None else cid, "distinguished": None,
            "flair_text": None, "flair_richtext": None}


class RenderTests(unittest.TestCase):
    def setUp(self):
        # Post -> 1 (context) -> 2 (initial, seed) -> 4 (later reply), 3 (earlier reply) -> 5
        self.members = [member(1, POST["id"], "context", top=True), member(2, 1, "body", seed=True),
                        member(4, 2, "body"), member(3, 2, "body"), member(5, 3, "body", seed=True)]
        self.comments = {1: comment(1, POST["id"], top=True), 2: comment(2, 1, body="x &gt; y"),
                         3: comment(3, 2, author="poster"), 4: comment(4, 2), 5: comment(5, 3)}
        self.discussion = {"discussion_id": "t1_2", "initial_id": 2, "link_id": POST["id"],
                           "context_size": 1, "body_size": 4, "body_depth": 2}

    def test_thread_order_is_depth_first_with_replies_by_time(self):
        parent_of = {m["id"]: m["parent_id"] for m in self.members if m["role"] == "body"}
        times = {m["id"]: m["created_utc"] for m in self.members}
        self.assertEqual(rd.thread_order(2, parent_of, times), [2, 3, 5, 4])

    def test_render_numbers_marks_and_post_author_section(self):
        post_author_rows = [self.comments[3], comment(9, 1, author="poster", time=4),
                            comment(10, 1, author="poster", time=99)]
        row = rd.build_discussion(self.discussion, POST, self.members, self.comments, post_author_rows)
        text = row["text"]
        self.assertIn("Title: A & B", text)
        self.assertIn("`x > y`", text)
        self.assertIn("[3] u/poster (the post's author)", text)
        self.assertIn("[4] u/someone, 1970-01-01 00:00 UTC, reply to [3]", text)
        # 3 is shown in the discussion, 10 is after the last study comment (5): only 9 is listed
        self.assertIn("(1, in time order)", text)
        self.assertEqual(row["study_comments"], ["t1_2", "t1_5"])
        self.assertEqual(row["study_numbers"], [2, 4])
        self.assertEqual(row["comment_ids"][:5], ["t1_1", "t1_2", "t1_3", "t1_5", "t1_4"])
        self.assertEqual((row["post_author_comments_shown"], row["pruned"]), (1, False))

    def test_prune_keeps_paths_to_seeds_and_their_first_replies(self):
        # a chain 1 -> 2 -> ... -> 60 with the seed at 50; 51 replies to 50
        parent_of = {c: c - 1 for c in range(2, 61)}
        parent_of[1] = 0
        keep = rd.prune(1, parent_of, {}, [50])
        self.assertEqual(keep, {1} | set(range(30, 52)))

    def test_bot_accounts_are_marked(self):
        rd.BOTS.add("helperbot")
        try:
            self.assertIn("u/helperbot (bot)", rd.who({"author": "helperbot"}, "poster"))
        finally:
            rd.BOTS.discard("helperbot")


if __name__ == "__main__":
    unittest.main()
