#!/usr/bin/env python3
"""List the parallel sets in Markdown files, for a skimmable-text review (SKILL.md beside this).

A set is what a reader scans as one: the sibling headings under one parent, the items of one
list, the first column of one table. For each set this prints every item's line, its first two
words and its length, then LEADS: a first word most items share (principle 2), openings of
mixed kinds (principles 1 and 3), and items much longer or shorter than the rest (principle 4).
A lead is a place to look, never a finding.

    python3 sets.py FILE...            Markdown files
    python3 sets.py --lines [FILE]     one item per line (a file, or stdin), as one set

Standard library only. Exits 0 whatever it finds; it lists, it does not judge.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
FENCE = re.compile(r"^\s*(```|~~~)")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
MARKUP = re.compile(r"[*_`]")
MIN_SET = 3


@dataclass
class Set:
    path: str
    kind: str
    items: list[tuple[int, str]] = field(default_factory=list)


def plain(text: str) -> str:
    """The words a reader sees: links as their text, no emphasis or code marks."""
    return " ".join(MARKUP.sub("", LINK.sub(r"\1", text)).split())


def markdown_sets(path: str, lines: list[str]) -> list[Set]:
    headings: dict[tuple[int, int], Set] = {}
    lists: dict[tuple[int, int, bool], Set] = {}
    tables: list[Set] = []
    parents = [0] * 7  # the line of the latest heading at each level
    open_lists: dict[int, tuple[int, bool]] = {}  # indent -> (key line, ordered)
    table: Set | None = None
    fenced = False
    for n, raw in enumerate(lines, 1):
        if FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = raw.rstrip("\n")
        if line.lstrip().startswith("|"):
            if TABLE_RULE.match(line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if table is None:
                table = Set(path, "table, first column")  # this row is the header
                tables.append(table)
            elif cells and cells[0]:
                table.items.append((n, plain(cells[0])))
            continue
        table = None
        h = HEADING.match(line)
        if h:
            level = len(h.group(1))
            parent = max(parents[:level])
            key = (parent, level)
            headings.setdefault(key, Set(path, f"headings, level {level}")).items.append(
                (n, plain(h.group(2)))
            )
            parents[level] = n
            for deeper in range(level + 1, 7):
                parents[deeper] = 0
            open_lists.clear()
            continue
        m = ITEM.match(line)
        if m:
            indent, ordered = len(m.group(1)), m.group(2)[0].isdigit()
            for deeper in [i for i in open_lists if i > indent]:
                del open_lists[deeper]
            if indent not in open_lists or open_lists[indent][1] != ordered:
                open_lists[indent] = (n, ordered)
            key = (open_lists[indent][0], indent, ordered)
            kind = "ordered list" if ordered else "list"
            lists.setdefault(key, Set(path, kind)).items.append((n, plain(m.group(3))))
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            open_lists.clear()  # a paragraph at the margin ends every open list
    found = list(headings.values()) + list(lists.values()) + tables
    return sorted((s for s in found if len(s.items) >= MIN_SET), key=lambda s: s.items[0][0])


def opening(word: str) -> str:
    w = word.lower()
    if len(w) > 4 and w.endswith("ing"):
        return "-ing"
    if len(w) > 3 and w.endswith("ed"):
        return "-ed"
    return "other"


def leads(s: Set) -> list[str]:
    words = [text.split() for _, text in s.items]
    found = []
    firsts = Counter(w[0].lower() for w in words if w)
    if firsts:
        word, count = firsts.most_common(1)[0]
        if count >= MIN_SET and count * 2 >= len(words):
            found.append(f'P2: {count} of {len(words)} items start with "{word}"')
    kinds = Counter(opening(w[0]) for w in words if w)
    if len(kinds) > 1 and all(c * 4 >= len(words) for c in kinds.values()):
        mix = ", ".join(f"{c} {k}" for k, c in kinds.most_common())
        found.append(f"P1/P3: openings are mixed ({mix})")
    lengths = [len(w) for w in words]
    mid = median(lengths)
    for (n, _), length in zip(s.items, lengths, strict=True):
        if length > 2 * mid + 1 or (mid >= 4 and length * 2 < mid):
            found.append(f"P4: line {n} has {length} words; the set's median is {mid:g}")
    return found


def show(s: Set) -> None:
    print(f"{s.path}:{s.items[0][0]}  {s.kind}, {len(s.items)} items")
    for n, text in s.items:
        first = " ".join(text.split()[:2])
        clipped = text if len(text) <= 72 else text[:71] + "…"
        print(f"  {n:>5}  {first:<28} {len(text.split()):>3}w  {clipped}")
    for lead in leads(s):
        print(f"  lead {lead}")
    print()


def main(argv: list[str]) -> int:
    if argv[:1] == ["--lines"]:
        source = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else sys.stdin.read()
        name = argv[1] if len(argv) > 1 else "<stdin>"
        s = Set(name, "lines")
        s.items = [(n, plain(t)) for n, t in enumerate(source.splitlines(), 1) if t.strip()]
        sets = [s] if len(s.items) >= MIN_SET else []
    else:
        if not argv:
            print(__doc__.strip(), file=sys.stderr)
            return 2
        sets = []
        for name in argv:
            text = Path(name).read_text(encoding="utf-8")
            sets += markdown_sets(name, text.splitlines())
    for s in sets:
        show(s)
    print(f"{len(sets)} set(s) of {MIN_SET} or more items")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
