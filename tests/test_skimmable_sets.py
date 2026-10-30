"""skills/skimmable-text/sets.py: which parallel sets it finds in Markdown, and which leads it
raises. It lists and never judges, so these pin the sets and the leads, not any verdict."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from unittest import mock

from support import ROOT

spec = importlib.util.spec_from_file_location(
    "sets", ROOT / "skills" / "skimmable-text" / "sets.py"
)
sets = importlib.util.module_from_spec(spec)
sys.modules["sets"] = sets  # dataclasses look their module up
spec.loader.exec_module(sets)

PAGE = """\
# Guide

## Installing it
## Running it
## Configuration

- Fetch the [feed](https://example.org/feed)
- Fetch the index
- Fetch the sitemap
  - nested one
  - nested two
  - nested three

```
- not a list item
- inside a fence
- at all
```

| Step | What |
| --- | --- |
| `fetch` | reads |
| lift | converts |
| filter | narrows |
"""


def found(text: str) -> list[tuple[str, list[str]]]:
    return [
        (s.kind, [t for _, t in s.items]) for s in sets.markdown_sets("page.md", text.splitlines())
    ]


class Sets(unittest.TestCase):
    def test_headings_lists_and_table_columns_are_sets(self):
        self.assertEqual(
            found(PAGE),
            [
                ("headings, level 2", ["Installing it", "Running it", "Configuration"]),
                ("list", ["Fetch the feed", "Fetch the index", "Fetch the sitemap"]),
                ("list", ["nested one", "nested two", "nested three"]),
                ("table, first column", ["fetch", "lift", "filter"]),
            ],
        )

    def test_a_fenced_block_is_not_read(self):
        self.assertNotIn("inside a fence", str(found(PAGE)))

    def test_a_shared_first_word_is_a_p2_lead(self):
        s = sets.Set("page.md", "list", [(1, "Fetch a"), (2, "Fetch b"), (3, "Fetch c")])
        self.assertEqual(sets.leads(s), ['P2: 3 of 3 items start with "fetch"'])

    def test_mixed_openings_are_a_lead(self):
        s = sets.Set("page.md", "list", [(1, "Installing it"), (2, "Running it"), (3, "Config")])
        self.assertEqual(sets.leads(s), ["P1/P3: openings are mixed (2 -ing, 1 other)"])

    def test_a_long_item_is_a_p4_lead(self):
        items = [(1, "one two"), (2, "three four"), (3, "five six " + "word " * 10)]
        leads = sets.leads(sets.Set("page.md", "list", items))
        self.assertEqual(leads, ["P4: line 3 has 12 words; the set's median is 2"])

    def test_lines_mode_reads_one_item_per_line(self):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("Add a\nAdd b\n\nAdd c\n")):
            with contextlib.redirect_stdout(out):
                self.assertEqual(sets.main(["--lines"]), 0)
        text = out.getvalue()
        self.assertIn("<stdin>:1  lines, 3 items", text)
        self.assertIn('lead P2: 3 of 3 items start with "add"', text)
        self.assertTrue(text.endswith("1 set(s) of 3 or more items\n"))


if __name__ == "__main__":
    unittest.main()
