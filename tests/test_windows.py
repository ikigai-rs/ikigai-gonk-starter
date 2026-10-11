"""Windows checkouts, reproduced anywhere with git's own settings (docs/windows.md).

Git for Windows installs with `core.autocrlf=true`, and `core.symlinks` is off unless Developer
Mode or Administrator rights let it create links. Both are plain git settings, so a clone made
with them here is the clone a Windows user gets, and these tests make one: of this tree, and
of a repository tools/new.py generates.

Each green test has a control that removes the defense and expects the defect, so a pass
means the check can see what it checks for:

- with `.gitattributes`, no file is checked out with CRLF; without it, the shell scripts and
  the spec vectors are (a CRLF script does not run: `bad interpreter: /bin/bash^M`);
- `CLAUDE.md` is a file whose one line imports the rules; a symlink in its place is checked
  out as a file holding the target's NAME, which Claude Code would read as the rules.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

spec = importlib.util.spec_from_file_location("new", ROOT / "tools" / "new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)

# What Git for Windows does by default, as git settings any platform's git honors.
WINDOWS = ["-c", "core.autocrlf=true", "-c", "core.symlinks=false"]

# Every git call here ignores the machine's own configuration (a global autocrlf, hooks, a
# signing key), so the only settings in play are the ones a test names.
ISOLATED = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
COMMITTER = [
    "-c",
    "user.name=test",
    "-c",
    "user.email=test@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=" + os.devnull,
]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=ISOLATED, capture_output=True, text=True, check=True
    ).stdout


def this_tree() -> list[str] | None:
    """The files of this working tree that a commit would carry: tracked, or new and not
    ignored, and present."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    rels = sorted({p for p in out.decode().split("\0") if p})
    return [rel for rel in rels if (ROOT / rel).is_file() or (ROOT / rel).is_symlink()]


class WindowsCheckout(unittest.TestCase):
    def scratch(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="gonk-windows-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        return tmp

    def commit(self, source: Path, rels: list[str], *, leave_out=(), link_claude=False) -> Path:
        """A repository holding `rels` copied from `source`, committed (no Windows settings
        yet: this is the repository as it is published)."""
        repo = self.scratch() / "origin"
        kept = [rel for rel in rels if rel not in leave_out]
        for rel in kept:
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            if link_claude and rel == "CLAUDE.md":
                os.symlink("AGENTS.md", repo / rel)
            else:
                shutil.copy(source / rel, repo / rel)
        git("init", "-q", cwd=repo)
        git("add", "--", *kept, cwd=repo)
        git(*COMMITTER, "commit", "-q", "-m", "the tree", cwd=repo)
        return repo

    def windows_clone(self, repo: Path) -> Path:
        dest = self.scratch() / "clone"
        git(*WINDOWS, "clone", "-q", str(repo), str(dest), cwd=repo)
        return dest

    def crlf(self, clone: Path) -> list[str]:
        rels = git("ls-files", "-z", cwd=clone).split("\0")
        return sorted(rel for rel in rels if rel and b"\r\n" in (clone / rel).read_bytes())

    def assertWindowsSafe(self, clone: Path):
        self.assertEqual(self.crlf(clone), [], "checked out with CRLF despite .gitattributes")
        self.assertFalse((clone / "CLAUDE.md").is_symlink())
        self.assertEqual((clone / "CLAUDE.md").read_bytes(), b"@AGENTS.md\n")
        modes = git("ls-files", "-s", cwd=clone).splitlines()
        self.assertEqual([m for m in modes if m.startswith("120000")], [], "a tracked symlink")

    # -- this tree -----------------------------------------------------------------------

    def test_a_windows_clone_of_this_tree_is_lf_with_claude_md_intact(self):
        rels = this_tree()
        if rels is None:
            self.skipTest("not a git working tree: this clones what a commit would carry")
        self.assertIn(".gitattributes", rels)
        self.assertWindowsSafe(self.windows_clone(self.commit(ROOT, rels)))

    def test_control_without_gitattributes_scripts_and_vectors_are_crlf(self):
        rels = this_tree()
        if rels is None:
            self.skipTest("not a git working tree: this clones what a commit would carry")
        clone = self.windows_clone(self.commit(ROOT, rels, leave_out={".gitattributes"}))
        crlf = self.crlf(clone)
        for rel in [
            "worker/worker-setup.sh",
            "scripts/gates.sh",
            "templates/library/openspec/vectors.json",
            "worker/units/gonk-worker.service",
        ]:
            self.assertIn(rel, crlf)
        self.assertTrue(
            (clone / "scripts" / "gates.sh").read_bytes().startswith(b"#!/usr/bin/env bash\r\n")
        )

    def test_control_a_symlinked_claude_md_becomes_the_targets_name(self):
        rels = ["AGENTS.md", "CLAUDE.md"]
        clone = self.windows_clone(self.commit(ROOT, rels, link_claude=True))
        self.assertFalse((clone / "CLAUDE.md").is_symlink())
        self.assertEqual((clone / "CLAUDE.md").read_bytes(), b"AGENTS.md")

    # -- a generated repository ----------------------------------------------------------

    def test_a_windows_clone_of_a_generated_repository_is_lf_with_claude_md_intact(self):
        dest = self.scratch() / "library"
        new.generate(dest, "python")
        rels = [str(rel) for rel in new.files_under(dest)]
        self.assertIn(".gitattributes", rels)
        self.assertWindowsSafe(self.windows_clone(self.commit(dest, rels)))


if __name__ == "__main__":
    unittest.main()
