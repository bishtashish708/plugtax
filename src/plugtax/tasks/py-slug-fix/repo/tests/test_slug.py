import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from slug import slugify  # noqa: E402


class SlugTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_accents(self):
        self.assertEqual(slugify("Café au lait"), "cafe-au-lait")

    def test_edges(self):
        self.assertEqual(slugify("  --Hello, World!--  "), "hello-world")


if __name__ == "__main__":
    unittest.main()
