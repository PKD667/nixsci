"""Prose budgets from WRITING.md, as warnings. Exit status is 0: the budgets warn, they do not block."""

from __future__ import annotations

import re
import sys
from pathlib import Path

DENY = [
    "powerful", "elegant", "robust", "novel", "seamless", "rich", "principled",
    "In this section we", "It is worth noting", "As mentioned above",
    "Importantly", "Interestingly", "Notably",
]
SENTENCES_PER_PARAGRAPH = 5
EMDASHES_PER_PARAGRAPH = 1


def paragraphs(text):
    """(first line number, text) of each blank-line separated block, comments and commands removed."""
    line, block, start = 1, [], 1
    for raw in text.split("\n") + [""]:
        stripped = re.sub(r"(?<!\\)%.*", "", raw)
        if stripped.strip():
            if not block:
                start = line
            block.append(stripped)
        elif block:
            yield start, " ".join(block)
            block = []
        line += 1


def warnings(path, text):
    for number, line in enumerate(text.split("\n"), 1):
        code = re.sub(r"(?<!\\)%.*", "", line)
        for word in DENY:
            if re.search(re.escape(word), code, re.IGNORECASE):
                yield f"{path}:{number}: deny-list: {word!r}"
    for start, para in paragraphs(text):
        if para.lstrip().startswith("\\"):
            continue
        sentences = len(re.findall(r"[.!?](?:\s|$)", para))
        if sentences > SENTENCES_PER_PARAGRAPH:
            yield f"{path}:{start}: paragraph has {sentences} sentences (cap {SENTENCES_PER_PARAGRAPH})"
        dashes = para.count("---") + para.count("\u2014")
        if dashes > EMDASHES_PER_PARAGRAPH:
            yield f"{path}:{start}: paragraph has {dashes} em-dashes (cap {EMDASHES_PER_PARAGRAPH})"


def main(argv):
    if argv and argv[0] == "remeasure":
        from .remeasure import main as remeasure
        return remeasure(argv[1:])
    if not argv:
        print("usage: nixsci out <file.tex|dir> ...", file=sys.stderr)
        return 2
    count = 0
    for arg in argv:
        root = Path(arg)
        for path in sorted(root.rglob("*.tex")) if root.is_dir() else [root]:
            for message in warnings(path, path.read_text()):
                print(message)
                count += 1
    print(f"{count} warning(s)", file=sys.stderr)
    return 0
