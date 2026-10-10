import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "3a-grouping"
sys.path.insert(0, str(SCRIPT_DIR))

from grouping import AGE_ANNOTATION_COLUMNS, load_relevant_flairs, update_grouped_flairs


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
    workbook.save(path)


class AgeRelevanceTests(unittest.TestCase):
    def test_any_annotation_makes_a_flair_relevant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flairs.xlsx"
            make_workbook(path)
            known, relevant = load_relevant_flairs(path)
        self.assertEqual(
            relevant["age"], {("askmen", "25M"), ("askmen", "dup"), ("askmen", "old")}
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


if __name__ == "__main__":
    unittest.main()
