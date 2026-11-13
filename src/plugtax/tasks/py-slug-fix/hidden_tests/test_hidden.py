import os
import sys
import unittest

sys.path.insert(0, os.getcwd())
from slug import slugify  # noqa: E402


class HiddenSlugTest(unittest.TestCase):
    def test_truncate_on_word(self):
        self.assertEqual(slugify("alpha beta gamma", max_len=12), "alpha-beta")

    def test_truncate_trailing_sep(self):
        self.assertEqual(slugify("alpha beta!", max_len=11), "alpha-beta")

    def test_no_edges_after_truncate(self):
        s = slugify("one two three four five six", max_len=9)
        self.assertFalse(s.startswith("-") or s.endswith("-"))
        self.assertEqual(s, "one-two")

    def test_symbols_only(self):
        self.assertEqual(slugify("!!!"), "")

    def test_basic_and_accents(self):
        self.assertEqual(slugify("Crème Brûlée Recipe"), "creme-brulee-recipe")


if __name__ == "__main__":
    unittest.main()
