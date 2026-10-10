import sys
import unittest
from pathlib import Path


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1] / "0-assets" / "raw_data" / "main" / "3b-age-grouping"
)
sys.path.insert(0, str(SCRIPT_DIR))

from deduce_birthyear import birth_range_for, parse_range


class BirthRangeTests(unittest.TestCase):
    def test_single_age_gives_two_birth_years(self):
        self.assertEqual(birth_range_for({"given_age": 17}, 2015), (1997, 1998))

    def test_age_range_uses_widest_span(self):
        self.assertEqual(birth_range_for({"given_agerange": "30-39"}, 2015), (1975, 1985))

    def test_birth_year_wins_over_age(self):
        annotation = {"given_birthyear": 1998, "given_age": 40}
        self.assertEqual(birth_range_for(annotation, 2015), (1998, 1998))

    def test_reversed_birth_year_range_is_sorted(self):
        self.assertEqual(
            birth_range_for({"given_birthyear_range": "1981-1980"}, 2015), (1980, 1981)
        )

    def test_generation_is_year_independent(self):
        self.assertEqual(birth_range_for({"given_generation": "X"}, 2012), (1965, 1980))
        self.assertEqual(birth_range_for({"given_generation": "X"}, 2020), (1965, 1980))

    def test_description_only_has_no_range(self):
        self.assertIsNone(birth_range_for({"given_description": "old"}, 2015))

    def test_parse_range_rejects_text(self):
        self.assertIsNone(parse_range("old"))


if __name__ == "__main__":
    unittest.main()
