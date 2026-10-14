"""Shared test support: import the tools, make scratch repositories, fake `ikigai`."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gonk  # noqa: E402  (the path above is what makes it importable)
import harness  # noqa: E402, F401  (re-exported for the tests)


def load_hook():
    spec = importlib.util.spec_from_file_location(
        "require_claim", ROOT / "hooks" / "claude" / "require-claim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scratch_repo(test) -> Path:
    """A copy of this starter's empty layout in a temporary directory, removed after the test."""
    tmp = Path(tempfile.mkdtemp(prefix="gonk-starter-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    for rel in [".gonk/layout.toml", ".gonk/gonk.toml"]:
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp / rel)
    for rel in [
        "openspec/specs",
        "openspec/changes",
        "artifacts",
        ".gonk/queries",
        ".gonk/scripts",
    ]:
        (tmp / rel).mkdir(parents=True, exist_ok=True)
    return tmp.resolve()


class FakeIkigai:
    """Answers each command from a script of (substring, Answer) pairs; records every call."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[tuple[str, str | None]] = []
        self.target = None

    def locate(self):
        return "/fake/bin/ikigai", "ikigai 0.0.0"

    def __call__(self, target):
        self.target = target
        return self

    def run(self, command, stdin=None):
        self.calls.append((command, stdin))
        for needle, answer in self.script:
            if needle in command:
                return answer
        return gonk.Answer(1, "", f"no scripted answer for {command!r}")


def ok(out="", err=""):
    return gonk.Answer(0, out, err)
