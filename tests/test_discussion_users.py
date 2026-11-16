import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "10-discussion-users"
sys.path.insert(0, str(SCRIPT_DIR))

import discussion_users as du  # noqa: E402


class DiscussionUsersTests(unittest.TestCase):
    def test_age_groups_come_from_the_common_span(self):
        self.assertEqual(du.age_groups({(1998, 1999), (1997, 1998)}), (["age_Z", "age_young"], (1998, 1998)))
        self.assertEqual(du.age_groups({(1960, 1961)}), (["age_boomer", "age_old"], (1960, 1961)))
        self.assertEqual(du.age_groups({(1990, 1991), (1970, 1971)}), ([], None))

    def test_relevance_follows_the_annotation_columns(self):
        self.assertTrue(du.is_relevant("gender_male", {"relevant": "X"}))
        self.assertFalse(du.is_relevant("gender_male", {"checked": "n"}))
        self.assertTrue(du.is_relevant("age", {"given_age": 34}))
        self.assertTrue(du.is_relevant("age", {"given_generation": "X"}))
        self.assertFalse(du.is_relevant("age", {"given_age": "X"}))  # the legacy marker

    def test_annotations_survive_a_rewrite(self):
        columns = {"age": ["subreddit", "flair", "given_age"], "gender_male": ["subreddit", "flair", "relevant"]}
        row = {"users": 1, "comments": 2, "first_year": 2011, "gain": 0.5, "example": "link"}
        rows_of = {group: [] for group in du.SHEET_GROUPS}
        rows_of["gender_male"] = [{"pair": ("askmen", "male"), "cells": {"relevant": "X"}, **row},
                                  {"pair": ("askmen", "ninja"), "cells": {"checked": "n"}, **row},
                                  {"pair": ("askmen", "open"), "cells": {}, **row}]
        rows_of["age"] = [{"pair": ("teenagers", "16"), "cells": {"given_age": 16}, **row}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new_flairs.xlsx"
            du.write_new_flairs(path, rows_of, columns)
            kept = du.load_new_annotations(path)
        self.assertEqual(kept["gender_male"], {("askmen", "male"): {"relevant": "X"}, ("askmen", "ninja"): {"checked": "n"}})
        self.assertEqual(kept["age"], {("teenagers", "16"): {"given_age": 16}})


if __name__ == "__main__":
    unittest.main()
