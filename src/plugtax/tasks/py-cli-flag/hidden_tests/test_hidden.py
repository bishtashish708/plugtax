import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.getcwd())
import wordfreq  # noqa: E402


def run(args, text):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        wordfreq.main([*args, f.name])
    os.unlink(f.name)
    return out.getvalue().splitlines()


class HiddenTest(unittest.TestCase):
    def test_top(self):
        self.assertEqual(run(["--top", "2"], "b a b c a b d"), ["3\tb", "2\ta"])

    def test_top_ties(self):
        self.assertEqual(run(["--top", "2"], "c b a"), ["1\ta", "1\tb"])

    def test_default_all(self):
        self.assertEqual(len(run([], "a b c d")), 4)

    def test_top_larger(self):
        self.assertEqual(run(["--top", "10"], "a a b"), ["2\ta", "1\tb"])


if __name__ == "__main__":
    unittest.main()
