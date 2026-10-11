"""The starter's own shape: what a new repository made from it must carry."""

from __future__ import annotations

import subprocess
import unittest

from support import ROOT


class Shape(unittest.TestCase):
    def test_claude_md_imports_agents_md(self):
        """One line, `@AGENTS.md`, which Claude Code expands into the rules. Not a symlink: Git
        for Windows checks a link out as a file holding the target's NAME unless core.symlinks
        is on, and Claude Code then reads `AGENTS.md`, nine bytes, as the rules (docs/windows.md).
        """
        claude = ROOT / "CLAUDE.md"
        self.assertFalse(claude.is_symlink(), "CLAUDE.md must be a file, never a symlink")
        self.assertEqual(claude.read_bytes(), b"@AGENTS.md\n")

    def test_the_tree_holds_no_symlinks(self):
        """Any tracked symlink becomes a one-line text file in a Windows checkout."""
        try:
            out = subprocess.run(
                ["git", "ls-files", "-s", "-z"], cwd=ROOT, capture_output=True, check=True
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git working tree: the check reads the index's file modes")
        links = [e.split("\t", 1)[1] for e in out.decode().split("\0") if e.startswith("120000")]
        self.assertEqual(links, [])

    def test_gitattributes_keeps_every_checkout_lf(self):
        """The blanket rule; tests/test_windows.py proves it on a real clone."""
        lines = (ROOT / ".gitattributes").read_text().splitlines()
        self.assertIn("* text=auto eol=lf", lines)

    def test_every_reserved_directory_has_a_readme(self):
        for rel in [
            ".gonk",
            ".gonk/queries",
            ".gonk/scripts",
            "openspec",
            "openspec/specs",
            "openspec/changes",
            "artifacts",
            "hooks/claude",
        ]:
            self.assertTrue((ROOT / rel / "README.md").is_file(), rel)

    def test_no_ledger_is_named_in_the_template(self):
        # A fresh repository must say which ledger is its own; init reports it missing.
        text = (ROOT / ".gonk" / "gonk.toml").read_text()
        self.assertFalse(any(line.startswith("ledger") for line in text.splitlines()), text)

    def test_the_core_rules_name_no_agent_specific_tool(self):
        core = (ROOT / "AGENTS.md").read_text().split("## Optional: a hook for Claude Code")[0]
        for word in ["PreToolUse", "Bash tool", "TodoWrite", "MultiEdit", "Edit tool"]:
            self.assertNotIn(word, core)


if __name__ == "__main__":
    unittest.main()
