#!/usr/bin/env python3
"""The audits profile's one step: file an unled audit of some files as a ledger item.

    python3 tools/audit.py <file>… [--budget 60] [--repo-name <name>] [--dry-run]

The item's body is `.gonk/templates/audit.md` with `{repo}`, `{budget}` and `{files}` filled
in: the repository, the budget of tool calls, and the file list, which is all an auditor is
given. It is labeled `audit`. Every file named must exist in this repository, so an audit
never sends someone looking for a path that is not there. A second audit of the same files is
a second item, on purpose: an audit is periodic.

Standard library only, beside tools/gonk.py, whose transport and configuration it shares.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gonk  # noqa: E402  (beside this file; the path above makes it importable)

TEMPLATE = Path(".gonk") / "templates" / "audit.md"
LABEL = "audit"
DEFAULT_BUDGET = 60


def body(repo: Path, name: str, files: list[str], budget: int) -> str:
    template = (repo / TEMPLATE).read_text(encoding="utf-8")
    listing = "\n".join(f"- `{f}`" for f in files)
    return (
        template.replace("{repo}", name)
        .replace("{budget}", str(budget))
        .replace("{files}", listing)
        .strip()
    )


def main(argv: list[str] | None = None, make_ikigai=gonk.Ikigai) -> int:
    p = argparse.ArgumentParser(prog="audit.py", description=__doc__.splitlines()[0])
    p.add_argument("files", nargs="+", help="the files to audit, as paths in this repository")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="tool calls (default 60)")
    p.add_argument("--repo-name", help="the repository's name (default: this directory's)")
    p.add_argument("--repo", help="the repository (default: found from the working directory)")
    p.add_argument("--dry-run", action="store_true", help="print the item; file nothing")
    args = p.parse_args(argv)
    try:
        repo = Path(args.repo).resolve() if args.repo else gonk.find_repo(Path.cwd())
        if args.budget < 1:
            raise gonk.ConfigError("--budget is a number of tool calls, at least 1")
        files = []
        for raw in args.files:
            path = (Path.cwd() / raw).resolve()
            try:
                rel = path.relative_to(repo).as_posix()
            except ValueError:
                raise gonk.ConfigError(f"{raw} is not in this repository ({repo})") from None
            if not path.is_file():
                raise gonk.ConfigError(f"{rel}: no such file; an audit names files that exist")
            files.append(rel)
        name = args.repo_name or repo.name
        title = f"Audit {name}: {len(files)} file(s), {args.budget} tool calls"
        text = body(repo, name, files, args.budget)
        if args.dry_run:
            print(f"{title}\n\n{text}")
            return 0
        cfg = gonk.load(repo)
        gonk.need(cfg, "ledger", "target")
        command = f"sink {cfg.resource('append')} labels={LABEL}"
        return gonk.show(make_ikigai(cfg.target).run(command, stdin=f"{title}\n\n{text}"))
    except gonk.ConfigError as e:
        print(f"audit.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
