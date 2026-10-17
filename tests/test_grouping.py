import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "3a-grouping"
sys.path.insert(0, str(SCRIPT_DIR))

import grouping
from grouping import AGE_ANNOTATION_COLUMNS, flair_admits, load_relevant_flairs, update_grouped_flairs


def make_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "age"
    sheet.append(["subreddit", "flair", *AGE_ANNOTATION_COLUMNS])
    sheet.append(["askmen", "25M", 25])
    sheet.append(["askmen", "dup", None])
    sheet.append(["askmen", "dup", None, None, "30-39"])
    sheet.append(["askmen", "old", None, None, None, None, None, "old"])
    sheet.append(["askmen", "blank"])
    sheet.append(["askmen", "legacy", "X"])
    generation = [None] * len(AGE_ANNOTATION_COLUMNS)
    generation[AGE_ANNOTATION_COLUMNS.index("given_generation")] = "X"  # Generation X
    sheet.append(["genx", "GenX", *generation])
    workbook.save(path)


class AgeRelevanceTests(unittest.TestCase):
    def test_any_annotation_makes_a_flair_relevant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flairs.xlsx"
            make_workbook(path)
            known, relevant = load_relevant_flairs(path)
        self.assertEqual(
            relevant["age"],
            {("askmen", "25M"), ("askmen", "dup"), ("askmen", "old"), ("genx", "GenX")},
        )
        self.assertIn(("askmen", "blank"), known["age"])
        self.assertIn(("askmen", "legacy"), known["age"])

    def test_unseen_age_flairs_are_appended_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flairs.xlsx"
            make_workbook(path)
            combos = {"age": {("askmen", "new flair")}}
            from grouping import GROUP_NAMES

            update_grouped_flairs(path, {group: combos.get(group, set()) for group in GROUP_NAMES})
            _, relevant = load_relevant_flairs(path)
        self.assertNotIn(("askmen", "new flair"), relevant["age"])

    def test_unseen_flairs_of_other_groups_are_appended_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flairs.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "gender_male"
            sheet.append(["subreddit", "flair", "relevant"])
            sheet.append(["askmen", "M", "X"])
            workbook.save(path)
            from grouping import GROUP_NAMES

            combos = {"gender_male": {("askmen", "M"), ("askmen", "new")}}
            added = update_grouped_flairs(path, {group: combos.get(group, set()) for group in GROUP_NAMES})
            known, relevant = load_relevant_flairs(path)
        self.assertEqual(added["gender_male"], 1)
        self.assertIn(("askmen", "new"), known["gender_male"])
        self.assertEqual(relevant["gender_male"], {("askmen", "M")})


JAN_2011 = 1293840000
JAN_2013 = 1356998400


class FlairAdmitsTests(unittest.TestCase):
    def setUp(self):
        self.saved = grouping.HIGH_LIKELIHOOD_SUBREDDITS
        grouping.HIGH_LIKELIHOOD_SUBREDDITS = {"gender_male": {"mensrights"}}
        self.relevant = {"gender_male": {("askmen", "M")}}

    def tearDown(self):
        grouping.HIGH_LIKELIHOOD_SUBREDDITS = self.saved

    def admits(self, subreddit, flair, created, score):
        comment = {"created_utc": created, "score": score}
        return flair_admits("gender_male", comment, (subreddit, flair), self.relevant)

    def test_relevant_flair_admits_at_any_date(self):
        self.assertTrue(self.admits("askmen", "M", JAN_2013, 1))
        self.assertTrue(self.admits("askmen", "M", JAN_2011, 1))

    def test_unflaired_comment_from_mixed_subreddit_before_2012_is_not_admitted(self):
        self.assertFalse(self.admits("askmen", "", JAN_2011, 50))

    def test_high_likelihood_subreddit_before_2012_needs_score_of_five(self):
        self.assertTrue(self.admits("mensrights", "", JAN_2011, 5))
        self.assertFalse(self.admits("mensrights", "", JAN_2011, 4))

    def test_high_likelihood_subreddit_has_no_effect_from_2012_on(self):
        self.assertFalse(self.admits("mensrights", "", JAN_2013, 50))


if __name__ == "__main__":
    unittest.main()
