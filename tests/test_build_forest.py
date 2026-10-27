import sys
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "8-full-contexts" / "programmatic"
)
sys.path.insert(0, str(SCRIPT_DIR))

from build_forest import build_thread, to_base36  # noqa: E402

POST = 1000


def row(parent, is_sub=False, mention=False, seed=False):
    return (parent, is_sub, 0, mention, seed)


class BuildThreadTests(unittest.TestCase):
    def setUp(self):
        # Post -> 1 (top-level, no mention)
        #           -> 2 (mention, seed) -> 3 (no mention) -> 4 (mention, seed)
        #           -> 5 (no mention) -> 6 (mention, seed)
        #   Post -> 7 (mention) -> 8 (mention, seed)
        # 20 -> 21 (mention, seed): 20's parent 19 is not in the index
        self.rows = {
            1: row(POST, is_sub=True),
            2: row(1, mention=True, seed=True),
            3: row(2),
            4: row(3, mention=True, seed=True),
            5: row(1),
            6: row(5, mention=True, seed=True),
            7: row(POST, is_sub=True, mention=True),
            8: row(7, mention=True, seed=True),
            20: row(19),
            21: row(20, mention=True, seed=True),
        }
        self.discussions, self.members, self.seed_rows = [], [], []
        build_thread(POST, self.rows, [2, 4, 6, 8, 21, 99], self.discussions, self.members, self.seed_rows)

    def discussion(self, initial):
        return next(d for d in self.discussions if d[1] == initial)

    def test_lower_mentions_belong_to_the_topmost_mention_of_their_branch(self):
        seed_map = {s[0]: s[1:] for s in self.seed_rows}
        self.assertEqual(seed_map[2], ("t1_" + to_base36(2), True, "ok"))
        self.assertEqual(seed_map[4], ("t1_" + to_base36(2), False, "ok"))
        self.assertEqual(seed_map[6], ("t1_" + to_base36(6), True, "ok"))
        # 8's topmost mention is 7, which is not a seed but still names the discussion
        self.assertEqual(seed_map[8], ("t1_" + to_base36(7), False, "ok"))
        self.assertEqual(seed_map[99], (None, False, "not in index"))

    def test_discussion_holds_context_above_and_everything_below(self):
        members = {(m[0], m[1]): m for m in self.members}
        d2 = "t1_" + to_base36(2)
        self.assertEqual(members[(d2, 1)][2:4], ("context", 1))
        self.assertEqual(sorted(m[1] for m in self.members if m[0] == d2 and m[2] == "body"), [2, 3, 4])
        self.assertNotIn((d2, 5), members)  # other branch, not part of this discussion
        self.assertEqual(self.discussion(2)[4:12], (True, 1, 3, 2, 1, 2, 2, 2))

    def test_stopped_upward_walk_is_flagged(self):
        d = self.discussion(21)
        self.assertFalse(d[4])  # upward_complete
        self.assertIsNone(d[11])  # absolute depth unknown


if __name__ == "__main__":
    unittest.main()
