"""The worker refuses to start a loop unless this account's global git excludes ignore Claude
Code's `.claude/settings.local.json` (ledger item 3347), where Claude Code saves the commands a
person approves, secrets included. A scratch HOME, so nothing depends on this machine's git
configuration.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worker_support import FakeLedger, Rig, worker

LINE = "**/.claude/settings.local.json"


class Excludes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "XDG_"))}
        self.env = {**env, "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"}

    def git_config(self, value: str) -> None:
        subprocess.run(
            ["git", "config", "--global", "core.excludesFile", value], env=self.env, check=True
        )

    def test_missing_is_refused_with_the_fix(self):
        why = worker.unignored_claude_settings(self.env)
        default = self.home / ".config" / "git" / "ignore"
        self.assertIn(f"({default})", why)
        self.assertIn(f"printf '%s\\n' '{LINE}' >> {default}", why)

    def test_the_default_file(self):
        default = self.home / ".config" / "git" / "ignore"
        default.parent.mkdir(parents=True)
        default.write_text("target/\n**/.claude/settings.json\n")
        self.assertIsNotNone(worker.unignored_claude_settings(self.env))
        default.write_text(f"target/\r\n{LINE}  \r\n")
        self.assertIsNone(worker.unignored_claude_settings(self.env))

    def test_xdg_config_home(self):
        xdg = self.home / "xdg"
        (xdg / "git").mkdir(parents=True)
        (xdg / "git" / "ignore").write_text(LINE + "\n")
        self.assertIsNone(
            worker.unignored_claude_settings({**self.env, "XDG_CONFIG_HOME": str(xdg)})
        )

    def test_the_file_core_excludes_file_names(self):
        self.git_config("~/.gitignore_global")
        default = self.home / ".config" / "git" / "ignore"
        default.parent.mkdir(parents=True)
        default.write_text(LINE + "\n")
        self.assertEqual(worker.global_excludes(self.env), self.home / ".gitignore_global")
        self.assertIsNotNone(worker.unignored_claude_settings(self.env))
        (self.home / ".gitignore_global").write_text(LINE)
        self.assertIsNone(worker.unignored_claude_settings(self.env))

    def test_a_loop_is_refused_and_once_is_not(self):
        rig = Rig(self)
        rig.configure()
        err = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(err):
            code = worker.main(["--repo", str(rig.checkout), "run"], FakeLedger())
        self.assertEqual(code, worker.CONFIG)
        self.assertIn("refusing to start a loop", err.getvalue())
        self.assertIn(LINE, err.getvalue())
        with mock.patch.object(worker, "unignored_claude_settings") as check:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                worker.main(["--repo", str(rig.checkout), "run", "--once"], FakeLedger())
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
