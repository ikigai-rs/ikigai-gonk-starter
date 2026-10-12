#!/usr/bin/env python3
"""Every test in this repository, the way CI runs them: `python3 scripts/test.py`.

One `cargo test` over the workspace: both artifacts' tests, in one run, so a failure in
either fails this. Cargo does the building, so this script stays the same whatever the
workspace grows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    argv = ["cargo", "test", "--workspace"]
    print("-- " + " ".join(argv), flush=True)
    return subprocess.run(argv, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
