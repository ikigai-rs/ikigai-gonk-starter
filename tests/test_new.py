"""tools/new.py: what a generated repository carries.

That the generated repository's own tests PASS is checked by scripts/gates.sh, which
generates every target into a scratch directory and runs its suite as that repository's CI
would, so a stale template fails `just gates` and CI alike.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from support import ROOT, gonk

spec = importlib.util.spec_from_file_location("new", ROOT / "tools" / "new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)


def scratch(test) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    return tmp / "library"


class Generate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
        cls.repo = cls.tmp / "library"
        cls.written = new.generate(cls.repo, "python")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_starter_file_it_names_exists(self):
        for rel in new.STARTER_FILES:
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_the_starters_tools_and_rules_are_copied_unchanged(self):
        for rel in ["AGENTS.md", "tools/gonk.py", "hooks/claude/require-claim.py"]:
            self.assertEqual((self.repo / rel).read_bytes(), (ROOT / rel).read_bytes(), rel)
        self.assertEqual(os.readlink(self.repo / "CLAUDE.md"), "AGENTS.md")
        self.assertTrue(os.access(self.repo / "tools" / "gonk.py", os.X_OK))

    def test_the_sample_and_the_target_are_both_there(self):
        for rel in [
            "README.md",
            "openspec/vectors.json",
            "openspec/specs/loans/spec.md",
            "openspec/changes/add-holds/tasks.md",
            ".gonk/queries/open-items-for-artifact.rq",
            ".gonk/scripts/next-for-artifact.plan.ttl",
            "artifacts/service/lending/domain.py",
            "artifacts/cli/library.py",
            "scripts/test.py",
            ".github/workflows/ci.yml",
        ]:
            self.assertTrue((self.repo / rel).is_file(), rel)
        # The sample's README replaces nothing of the starter's: the starter's is not copied.
        self.assertIn("A lending library", (self.repo / "README.md").read_text())
        # What belongs to the starter alone stays behind.
        for rel in [
            "tools/new.py",
            "templates",
            "scripts/gates.sh",
            "LICENSE-MIT",
            "justfile.recipes",
        ]:
            self.assertFalse((self.repo / rel).exists(), rel)

    def test_the_justfile_keeps_the_gonk_recipes_and_swaps_the_starters_own(self):
        names = [n for n, _ in new.recipe_blocks((self.repo / "justfile").read_text()) if n]
        for recipe in ["init", "seed", "next", "claim", "report", "close", "gates", "serve"]:
            self.assertIn(recipe, names)
        self.assertNotIn("new", names)
        self.assertEqual(names.count("gates"), 1)
        self.assertIn("python3 scripts/test.py", (self.repo / "justfile").read_text())

    def test_the_generated_layout_and_artifact_lines_pass_init_checks(self):
        layout = gonk.read_toml(self.repo / ".gonk" / "layout.toml")
        _, bad = gonk.layout_problems(self.repo, layout)
        self.assertEqual(bad, [])
        self.assertEqual(gonk.artifact_problems(self.repo, layout), [])
        names = {name for _, name in gonk.named_artifacts(self.repo, layout)}
        self.assertEqual(names, {"service", "cli"})

    def test_a_directory_that_is_not_empty_is_refused(self):
        with self.assertRaises(SystemExit) as refused:
            new.generate(self.repo, "python")
        self.assertIn("not empty", str(refused.exception))

    def test_an_unknown_target_is_refused_naming_the_known_ones(self):
        with self.assertRaises(SystemExit) as refused:
            new.generate(scratch(self), "cobol")
        self.assertIn("this starter has python", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
