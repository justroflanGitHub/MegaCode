"""Tokenize a VT capture and count escape sequences (dev tool)."""

import re
import sys
from collections import Counter

CSI = re.compile(r"\x1b\[[0-9;?<>=]*[A-Za-z]")
ESC = re.compile(r"\x1b[A-Za-z0-9=><()]")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

TOKENS = re.compile(
    "|".join(g.pattern for g in (OSC, CSI, ESC)) + r"|[^\x1b]+"
)


def main(path: str) -> None:
    data = open(path, encoding="utf-8", errors="replace").read()
    seqs: Counter = Counter()
    examples: dict = {}
    pos = 0
    for m in TOKENS.finditer(data):
        t = m.group(0)
        if t.startswith("\x1b"):
            key = repr(t[:40])
        else:
            key = "TEXT"
        seqs[key] += 1
        examples.setdefault(key, t[:70])
    for k, n in seqs.most_common(30):
        print(f"{n:4d}  {k:45}  e.g. {examples[k]!r}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "nano_scroll_capture.txt")
