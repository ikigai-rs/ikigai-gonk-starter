#!/usr/bin/env python3
"""Every test in this repository, the way CI runs them: `python3 scripts/test.py`.

Each directory is its own `unittest` run, in its own process, so two artifacts' tests never
share a module name or a `sys.path`. The exit status is non-zero if any run failed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITES = ["tests", "artifacts/service/tests", "artifacts/cli/tests"]


def main() -> int:
    failed = []
    for suite in SUITES:
        print(f"-- {suite}", flush=True)
        argv = [sys.executable, "-m", "unittest", "discover", "-s", suite, "-v"]
        if subprocess.run(argv, cwd=ROOT).returncode != 0:
            failed.append(suite)
    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"all {len(SUITES)} suites passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
