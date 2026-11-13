import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from wordfreq import frequencies  # noqa: E402


class FreqTest(unittest.TestCase):
    def test_order(self):
        self.assertEqual(frequencies("b a b c a b"), [("b", 3), ("a", 2), ("c", 1)])


if __name__ == "__main__":
    unittest.main()
