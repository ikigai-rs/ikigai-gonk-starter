#!/usr/bin/env python3
"""A fake coding agent for the worker's tests. It runs in the item's worktree, as a real one
would, and does what its flags say: record its brief, sleep, edit a file, commit, push the
branch and "open a pull request" through the stub gh, or report evidence without one.

    fake_agent.py --record <dir> --gh "<argv of the stub gh>" --branch B --item N
                  [--sleep S] [--no-pr] [--evidence URL --report-file P] [--exit N] -- <prompt>
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
    p.add_argument("--evidence")
    p.add_argument("--report-file")
    p.add_argument("--exit", type=int, default=0)
    p.add_argument("prompt")
    args = p.parse_args()

    record = Path(args.record)
    record.mkdir(parents=True, exist_ok=True)
    (record / f"prompt-{args.item}.md").write_text(args.prompt)
    (record / f"cwd-{args.item}").write_text(str(Path.cwd()))
    (record / f"started-{args.item}").write_text(str(time.time()))
    time.sleep(args.sleep)

    if args.evidence:
        Path(args.report_file).write_text(f"{args.evidence}\nIt does not reproduce: see the run.\n")
    if not args.no_pr:
        Path("WORKED.txt").write_text(f"item {args.item}\n")
        git("add", "WORKED.txt")
        git("commit", "-q", "-m", f"Work on item {args.item}")
        git("push", "-q", "origin", f"HEAD:refs/heads/{args.branch}")
        gh = shlex.split(args.gh)
        subprocess.run(
            [*gh, "pr", "create", "--head", args.branch, "--title", f"item {args.item}"],
            check=True,
            capture_output=True,
        )
    (record / f"finished-{args.item}").write_text(str(time.time()))
    return args.exit


if __name__ == "__main__":
    sys.exit(main())
