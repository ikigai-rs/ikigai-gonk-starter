#!/usr/bin/env python3
"""The review-every-commit profile's hook: ask gonk to review each file this commit touched.

`hooks/git/post-commit` runs this after every commit. For each file the commit added or
changed it runs `ikigai-gonk review request <root> <path>`, which drops one request into
gonk's review queue and exits: it opens no store and dials nothing, so it works while gonk is
down, and the same file committed again while the request waits is one request, not two.
gonk's review pass takes them one at a time, when review is armed.

A commit must never wait on this and never fail because of it, so the requests are handed to
a detached process and this returns at once, and every path ends in exit 0. What it cannot do
it says on standard error, once, and the commit goes ahead:

  - the working tree is not wired to gonk yet (`python3 tools/gonk.py init`);
  - it reaches gonk over QUIC: the queue is a directory on gonk's machine and no network door
    reaches it, so a request can only be dropped there;
  - `ikigai-gonk` is not on PATH.

Settings are `.gonk/process.toml`'s `[review]` table: `root` (the `gonk.browse.root` name,
by default this directory's name), `config` (gonk's config.toml, when it is not in gonk's
default config home) and `skip` (paths never queued). Standard library only.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROFILE = "review-every-commit"
TIMEOUT_S = 10


def say(message: str) -> None:
    print(f"review-on-commit: {message}", file=sys.stderr)


def git(repo: Path, *argv: str) -> str:
    done = subprocess.run(["git", *argv], cwd=repo, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else ""


def changed_files(repo: Path) -> list[str]:
    """The paths this commit added or changed, as git spells them. `--root` so a first commit
    is not skipped, `-m` so a merge commit reports its files; a deleted path has nothing to
    review."""
    out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "--root", "HEAD")
    paths = sorted({line for line in out.splitlines() if line})
    return [p for p in paths if (repo / p).is_file()]


def requests(repo: Path, which=shutil.which) -> tuple[list[list[str]], str | None]:
    """(the commands to run, one line to say instead) for this working tree's last commit."""
    sys.path.insert(0, str(repo / "tools"))
    import gonk  # the repository's own copy

    try:
        process = gonk.read_process(repo)
        if process is None or PROFILE not in process.get("process", []):
            return [], None  # not this repository's process: nothing to do or say
        cfg = gonk.load(repo)
    except gonk.ConfigError as e:
        return [], f"nothing queued: {e}"
    if cfg.target is None:
        return [], (
            "nothing queued: this working tree does not say how to reach gonk "
            "(`python3 tools/gonk.py init --socket <path>`)"
        )
    if cfg.target.quic:
        return [], (
            "nothing queued: this working tree reaches gonk over QUIC, and a review request is "
            "dropped into a queue on gonk's own machine, which no network door reaches"
        )
    binary = which("ikigai-gonk")
    if binary is None:
        return [], "nothing queued: `ikigai-gonk` is not on PATH (it drops the request)"
    review = process.get("review", {})
    root = gonk.review_root(repo, process)
    extra = ["--config", gonk.expand(review["config"], repo)] if review.get("config") else []
    skip = review.get("skip", [])
    commands = [
        [binary, "review", "request", root, path, *extra]
        for path in changed_files(repo)
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in skip)
    ]
    return commands, None


def drop(commands: list[list[str]]) -> int:
    """Run each request; a failure is one file not queued, never a reason to stop."""
    failed = 0
    for command in commands:
        try:
            done = subprocess.run(command, capture_output=True, timeout=TIMEOUT_S)
            failed += done.returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            failed += 1
    return failed


def main(argv: list[str]) -> int:
    if argv[:1] == ["--drop"]:
        drop(json.loads(argv[1]))
        return 0
    try:
        top = git(Path.cwd(), "rev-parse", "--show-toplevel").strip()
        if not top:
            return 0
        commands, note = requests(Path(top))
        if note:
            say(note)
        if not commands:
            return 0
        if "--foreground" in argv:
            drop(commands)
            return 0
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--drop", json.dumps(commands)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:  # a commit must never fail because of this hook
        say(f"nothing queued: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
