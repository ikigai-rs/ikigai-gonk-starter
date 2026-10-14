#!/usr/bin/env python3
"""Every test in this repository, the way CI runs them: `python3 scripts/test.py`.

One `go test ./...` over the whole module: both artifacts' tests, in one run, so a failure in
either fails this. Go does the discovery and the compiling, so this script stays the same
whatever the tree grows.

It FAILS CLOSED when `go` is not on PATH: a missing toolchain must not read as a passing
suite. Standard library only (this script is Python because every target's entry point is
`python3 scripts/test.py`, so CI's one command works for any target).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    go = shutil.which("go")
    if go is None:
        print("test: no go on PATH: NOTHING WAS TESTED", file=sys.stderr)
        print(
            "  install it from https://go.dev/dl/ (or the actions/setup-go action)", file=sys.stderr
        )
        return 2
    # -count=1: a cached pass is not a run of today's templates and vectors.
    argv = [go, "test", "-count=1", "./..."]
    print("-- " + " ".join(argv), flush=True)
    return subprocess.run(argv, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
