#!/usr/bin/env python3
"""A stub `gh` for the worker's tests: just the calls the worker and the fake agent make.

    gh.py --state <file.json> pr create --head <branch> --title <t> [--base b] [-R owner/name]
    gh.py --state <file.json> pr list -R <slug> --head <branch> --state all --json …
    gh.py --state <file.json> pr checks <n> -R <slug> --json …
    gh.py --state <file.json> run list -R <slug> --commit <sha> --json …

The state file holds the pull requests, how `pr checks` answers (`"checks": "refused"` is a
fine-grained token, which GitHub offers no Checks permission for; `"ok"` answers), the runs
per head sha, and every call made. Anything else (`pr merge` above all) is recorded under
`forbidden` and fails, so a test can assert the worker never merged.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def flag(argv: list[str], name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] != ["--state"]:
        print("gh.py: --state <file> first", file=sys.stderr)
        return 2
    path, argv = Path(argv[1]), argv[2:]
    state = json.loads(path.read_text()) if path.exists() else {}
    state.setdefault("prs", [])
    state.setdefault("calls", []).append(argv)
    state.setdefault("forbidden", [])
    code, out, err = 0, "", ""
    slug = flag(argv, "-R", state.get("slug", "acme/lib"))

    if argv[:2] == ["pr", "create"]:
        head = flag(argv, "--head")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        number = len(state["prs"]) + 1
        url = f"https://github.com/{slug}/pull/{number}"
        state["prs"].append(
            {"number": number, "url": url, "state": "OPEN", "headRefName": head, "headRefOid": sha}
        )
        out = url + "\n"
    elif argv[:2] == ["pr", "list"]:
        head = flag(argv, "--head")
        fields = flag(argv, "--json", "number").split(",")
        rows = [{f: pr.get(f) for f in fields} for pr in state["prs"] if pr["headRefName"] == head]
        out = json.dumps(rows)
    elif argv[:2] == ["pr", "checks"]:
        if state.get("checks", "refused") == "refused":
            code = 1
            err = (
                "GraphQL: Resource not accessible by personal access token "
                "(node.statusCheckRollup)\n"
            )
        else:
            out = json.dumps([{"name": "gates", "bucket": "pass"}])
    elif argv[:2] == ["run", "list"]:
        sha = flag(argv, "--commit", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            out = "[]"  # what the real one does with a short sha: nothing, silently
        else:
            default = [
                {"name": "CI", "status": "completed", "conclusion": "success", "headSha": sha}
            ]
            out = json.dumps(state.get("runs", {}).get(sha, default))
    else:
        state["forbidden"].append(argv)
        code, err = 1, f"gh.py: not a call the worker may make: {argv}\n"

    path.write_text(json.dumps(state, indent=2))
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
