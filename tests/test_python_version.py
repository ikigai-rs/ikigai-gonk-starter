"""An interpreter too old for tools/gonk.py and tools/worker.py gets a sentence, not a traceback.

macOS's /usr/bin/python3 is Apple's 3.9, and tomllib came in 3.11 (ledger item 2938). CI's Python
is new, so the first test hides tomllib to run the refusal anywhere, and the second keeps both
files parseable by 3.9, since a SyntaxError would come before the refusal could run. The third
runs the real old interpreter on a machine that has one.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("gonk.py", "worker.py")
APPLE_PYTHON = Path("/usr/bin/python3")

# Run a tool as a script on an interpreter whose tomllib is missing, as 3.9's is.
HIDE_TOMLLIB = (
    "import runpy, sys; sys.modules['tomllib'] = None; "
    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
)


def old_python() -> tuple[str, str] | None:
    """The version and real path of the machine's /usr/bin/python3 when it is older than 3.11,
    else None. On macOS that file is a stub, and `sys.executable` names the program it starts."""
    if not APPLE_PYTHON.exists():
        return None
    said = subprocess.run(
        [
            str(APPLE_PYTHON),
            "-c",
            "import sys; print(sys.version_info[0], sys.version_info[1], sys.executable)",
        ],
        capture_output=True,
        text=True,
    ).stdout.split(maxsplit=2)
    if len(said) != 3 or (int(said[0]), int(said[1])) >= (3, 11):
        return None
    return f"{said[0]}.{said[1]}", said[2].strip()


class AnOldPython(unittest.TestCase):
    def assert_refused(self, done: subprocess.CompletedProcess, version: str, exe: str):
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertIn(f"needs Python 3.11 or later (found {version} at {exe})", done.stderr)
        self.assertIn("brew install python", done.stderr)
        self.assertNotIn("Traceback", done.stderr)

    def test_a_missing_tomllib_is_a_sentence(self):
        version = f"{sys.version_info[0]}.{sys.version_info[1]}"
        for tool in TOOLS:
            with self.subTest(tool=tool):
                done = subprocess.run(
                    [sys.executable, "-c", HIDE_TOMLLIB, str(ROOT / "tools" / tool), "--help"],
                    capture_output=True,
                    text=True,
                )
                self.assert_refused(done, version, sys.executable)

    def test_both_files_still_parse_as_python_3_9(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                source = (ROOT / "tools" / tool).read_text(encoding="utf-8")
                ast.parse(source, filename=tool, feature_version=(3, 9))

    @unittest.skipUnless(old_python(), "/usr/bin/python3 is absent or 3.11 or later")
    def test_the_real_old_python_is_refused_the_same_way(self):
        version, exe = old_python()
        for tool in TOOLS:
            with self.subTest(tool=tool):
                done = subprocess.run(
                    [str(APPLE_PYTHON), str(ROOT / "tools" / tool), "--help"],
                    capture_output=True,
                    text=True,
                )
                self.assert_refused(done, version, exe)


if __name__ == "__main__":
    unittest.main()
