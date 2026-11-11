#!/usr/bin/env python3
"""A fake coding agent for the worker's tests. It runs in the item's worktree, as a real one
would, and does what its flags say: record its brief, sleep, edit a file, commit, push the
branch and "open a pull request" through the stub gh, or report evidence without one.

    fake_agent.py --record <dir> --gh "<argv of the stub gh>" --branch B --item N
                  [--sleep S] [--no-pr] [--evidence URL --report-file P] [--exit N]
                  [--report TEXT] [--dirty] [--ignored] [--commit-only] -- <prompt>

The last four are a reviewer's: write TEXT to the report file, leave an untracked file, leave a
file `.gitignore` covers, or commit and push nothing further.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path


def git(*argv: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=fake agent", "-c", "user.email=agent@example.invalid", *argv],
        check=True,
        capture_output=True,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--record", required=True)
    p.add_argument("--gh", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--sleep", type=float, default=0)
    p.add_argument("--no-pr", action="store_true")
    p.add_argument("--rework", help="existing PR branch to update without a report or new PR")
    p.add_argument("--evidence")
    p.add_argument("--report-file")
    p.add_argument("--exit", type=int, default=0)
    p.add_argument("--report")
    p.add_argument("--dirty", action="store_true")
    p.add_argument("--ignored", action="store_true")
    p.add_argument("--commit-only", action="store_true")
    p.add_argument("prompt")
    args = p.parse_args()

    record = Path(args.record)
    record.mkdir(parents=True, exist_ok=True)
    (record / f"prompt-{args.item}.md").write_text(args.prompt)
    (record / f"cwd-{args.item}").write_text(str(Path.cwd()))
    (record / f"started-{args.item}").write_text(str(time.time()))
    time.sleep(args.sleep)

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    (record / f"head-{args.item}").write_text(head.stdout.strip())
    if args.report is not None:
        Path(args.report_file).write_text(args.report)
    if args.dirty:
        Path("LEFT-BEHIND.txt").write_text("a reviewer changes nothing\n")
    if args.ignored:
        Path("build-output.tmp").write_text("output\n")
    if args.commit_only:
        Path("COMMITTED.txt").write_text("a reviewer commits nothing\n")
        git("add", "COMMITTED.txt")
        git("commit", "-q", "-m", "A reviewer's commit")
    if args.evidence:
        Path(args.report_file).write_text(f"{args.evidence}\nIt does not reproduce: see the run.\n")
    if args.rework:
        git("fetch", "origin", args.rework)
        git("checkout", "-B", args.rework, "FETCH_HEAD")
    if not args.no_pr or args.rework:
        Path("WORKED.txt").write_text(f"item {args.item}\n")
        git("add", "WORKED.txt")
        git("commit", "-q", "-m", f"Work on item {args.item}")
        git("push", "-q", "origin", f"HEAD:refs/heads/{args.rework or args.branch}")
        gh = shlex.split(args.gh)
        subprocess.run(
            [
                *gh,
                "pr",
                "update-head" if args.rework else "create",
                "--head",
                args.rework or args.branch,
                "--title",
                f"item {args.item}",
            ],
            check=True,
            capture_output=True,
        )
    (record / f"finished-{args.item}").write_text(str(time.time()))
    return args.exit


if __name__ == "__main__":
    sys.exit(main())
