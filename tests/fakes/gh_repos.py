#!/usr/bin/env python3
"""A stub `gh` for tools/worker_repos.py's tests: the two calls it makes, and nothing else.

    gh_repos.py --state <file> repo list <owner> --json name,url,isArchived,visibility --limit N
    gh_repos.py --state <file> api repos/<owner>/<name> --jq .permissions.push

The state file holds `repos` (the rows `repo list` answers, every field as gh names it) and
`push` (owner/name to true or false; a repository missing from it answers 404, as GitHub does
for one the token cannot see). Every call is recorded under `calls`; anything else fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] != ["--state"]:
        print("gh_repos.py: --state <file> first", file=sys.stderr)
        return 2
    path, argv = Path(argv[1]), argv[2:]
    state = json.loads(path.read_text())
    state.setdefault("calls", []).append(argv)
    code, out, err = 0, "", ""
    if argv[:2] == ["repo", "list"]:
        fields = argv[argv.index("--json") + 1].split(",")
        limit = int(argv[argv.index("--limit") + 1])
        rows = [{f: r.get(f) for f in fields} for r in state.get("repos", [])][:limit]
        out = json.dumps(rows) + "\n"
    elif argv[:1] == ["api"] and argv[2:] == ["--jq", ".permissions.push"]:
        slug = argv[1].removeprefix("repos/")
        if slug in state.get("push", {}):
            out = "true\n" if state["push"][slug] else "false\n"
        else:
            code, err = 1, "gh: Not Found (HTTP 404)\n"
    else:
        code, err = 1, f"gh_repos.py: not a call worker_repos.py makes: {argv}\n"
    path.write_text(json.dumps(state, indent=2))
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main())
