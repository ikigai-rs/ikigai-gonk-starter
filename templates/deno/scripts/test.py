#!/usr/bin/env python3
"""Every test in this repository, the way CI runs them: `python3 scripts/test.py`.

One `deno test` over the whole tree: both artifacts' tests, in one run, so a failure in
either fails this. Deno does the discovery and the type checking, so this script stays the
same whatever the tree grows.

It FAILS CLOSED when `deno` is not on PATH: a missing toolchain must not read as a passing
suite. Standard library only (this script is Python because every target's entry point is
`python3 scripts/test.py`, so CI's one command works for any target).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The tests bind a loopback port for the real service and read the vectors: the two
# permissions they need, named here rather than granted wholesale.
PERMISSIONS = ["--allow-net=127.0.0.1", "--allow-read"]


def main() -> int:
    deno = shutil.which("deno")
    if deno is None:
        print("test: no deno on PATH: NOTHING WAS TESTED", file=sys.stderr)
        print(
            "  install it from https://deno.com/ (or the denoland/setup-deno action)",
            file=sys.stderr,
        )
        return 2
    argv = [deno, "test", *PERMISSIONS]
    print("-- " + " ".join(argv), flush=True)
    return subprocess.run(argv, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
