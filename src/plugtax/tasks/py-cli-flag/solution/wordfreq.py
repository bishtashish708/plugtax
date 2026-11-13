"""Print word frequencies of a text file, most frequent first."""

import argparse
import re
import sys
from collections import Counter

WORD = re.compile(r"[a-z']+")


def frequencies(text):
    """(word, count) pairs, most frequent first; ties alphabetical."""
    counts = Counter(WORD.findall(text.lower()))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--top", type=int, default=None)
    args = parser.parse_args(argv)
    with open(args.path) as f:
        pairs = frequencies(f.read())
    for word, count in pairs[: args.top] if args.top is not None else pairs:
        print(f"{count}\t{word}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
