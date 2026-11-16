#!/usr/bin/env python3
"""Checks for any agent's work, whatever made it: a commit message and a pull request.

    python3 tools/checks.py commit-msg <file>     the git commit-msg hook (hooks/git/commit-msg)
    python3 tools/checks.py pr [--event <file>] [--changed <file>]
                                                  a pull request, in CI (scripts/gates.sh)

THE COMMIT MESSAGE (AGENTS.md, "6. Never a bare `#N`"). Ledger items and pull requests share
numbers, so a bare `#12` names neither. Refused: `#12` with nothing before it that says whose
number it is. Allowed: a link (`https://…/pull/12`, `…/items/12`), `owner/repo#12`,
`<ledger>#12` (`default#12`), `PR #12`, `ledger #12` and `ledger item 12`. Lines git drops
(those starting with `#`) are not read. Installed by `git config core.hooksPath hooks/git`
(`just hooks`), the same mechanism as the review hook.

THE PULL REQUEST, read from the event GitHub Actions writes (`$GITHUB_EVENT_PATH`) and the
files it changes. Outside a pull request it checks nothing and says so. Two checks:

- ACCESSIBILITY (AGENTS.md, "7. Read ACCESSIBILITY.md before changing what people see").
  Nothing can check that someone READ ACCESSIBILITY.md. This checks the evidence instead: a
  pull request that changes a path the repository declares user-facing must have a
  `## Accessibility` heading in its description.
- SPEC FIRST (AGENTS.md, "Spec first"; tools/spec_checks.py, carried by the spec profile).
  Every folder under `openspec/changes/` (archive
  included) has its `proposal.md` and `tasks.md`, and every folder under `openspec/specs/`
  its `spec.md`. And a pull request MARKED as a behavior change, by the label
  `behavior-change` or a title starting `behavior:`, must add or change a file under
  `openspec/changes/`. The mark is the trigger because it is predictable: the author says
  which kind of change it is, and the check holds them to it.

Both are configurable in `.gonk/checks.toml` (absent: the defaults below):

    [pr]
    user_facing = ["faces/*", "*.html", "*.css", "*.svg"]   # fnmatch globs; `*` crosses `/`
    behavior_labels = ["behavior-change"]
    behavior_title_prefixes = ["behavior:"]

Every refusal is recorded in the working tree's firing log (tools/harness.py), as the other
sensors' are. Exit status: 0 passed, 1 refused, 2 could not read its input.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

DEFAULTS = {
    "user_facing": ["faces/*", "*.html", "*.css", "*.svg"],
    "behavior_labels": ["behavior-change"],
    "behavior_title_prefixes": ["behavior:"],
}

# A `#N` is qualified by what is right before it: a word or path (`owner/repo#12`,
# `default#12`, a URL's fragment), or the words `PR ` or `ledger `. Anything else is bare.
BARE = re.compile(r"(?<![\w/.\-])(?<!PR )(?<!ledger )#(\d+)\b", re.IGNORECASE)


def bare_numbers(message: str) -> list[str]:
    """The bare `#N`s of a commit message, in order, reading only the lines git keeps.

    >>> bare_numbers("Fix the hook (#12)")
    ['#12']
    >>> bare_numbers("See owner/repo#12, default#3, PR #4, ledger #5, ledger item 6")
    []
    >>> bare_numbers("https://github.com/o/r/pull/12 and https://x/items/9#7")
    []
    """
    found = []
    for line in message.splitlines():
        if line.startswith("#"):
            continue  # a comment line, which git strips
        found += [m.group(0) for m in BARE.finditer(line)]
    return found


def repo_root(start: Path) -> Path:
    for candidate in (start.resolve(), *start.resolve().parents):
        if (candidate / ".gonk").is_dir():
            return candidate
    return start.resolve()


def cmd_commit_msg(path: Path, repo: Path) -> int:
    try:
        message = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"commit-msg: could not read the message: {e}", file=sys.stderr)
        return 2
    found = bare_numbers(message)
    if not found:
        return 0
    harness.fire("commit-bare-number", "refusal", repo, tool="git commit")
    print(
        f"Commit refused: {', '.join(found)} is a bare number. Ledger items and pull "
        "requests share numbers, so say which one you mean: `ledger item 12` for a ledger "
        "item, `PR #12` or `owner/repo#12` for a pull request, or a link. "
        "Your message is kept in .git/COMMIT_EDITMSG.",
        file=sys.stderr,
    )
    return 1


# --------------------------------------------------------------------------- the pull request


def load_config(repo: Path) -> dict:
    config = dict(DEFAULTS)
    try:
        import tomllib

        with (repo / ".gonk" / "checks.toml").open("rb") as f:
            config.update(tomllib.load(f).get("pr", {}))
    except FileNotFoundError:
        pass
    return config


def user_facing(changed: list[str], globs: list[str]) -> list[str]:
    return [p for p in changed if any(fnmatch.fnmatch(p, g) for g in globs)]


def has_accessibility_section(body: str) -> bool:
    return re.search(r"^##\s+Accessibility\s*$", body or "", re.M | re.I) is not None


def check_accessibility(pr: dict, changed: list[str], config: dict, repo: Path) -> list[str]:
    seen = user_facing(changed, config["user_facing"])
    if not seen or has_accessibility_section(pr.get("body") or ""):
        return []
    harness.fire("pr-accessibility-section", "refusal", repo, tool="ci")
    return [
        "This pull request changes what people see ("
        + ", ".join(seen[:5])
        + (", …" if len(seen) > 5 else "")
        + ") and its description has no `## Accessibility` section. Read ACCESSIBILITY.md, "
        "then add the section with a Check | Result | Evidence table, one row per check. "
        "This check sees only the section, not whether anyone read the file."
    ]


def changed_files_from_git(base: str, repo: Path) -> list[str] | None:
    """The files a pull request changes: its merge commit (what CI checks out) against its
    base, fetching the base when the checkout is shallow."""

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)

    if git("cat-file", "-e", base + "^{commit}").returncode != 0:
        git("fetch", "--no-tags", "--depth=1", "origin", base)
    diff = git("diff", "--name-only", base, "HEAD")
    if diff.returncode != 0:
        return None
    return [line for line in diff.stdout.splitlines() if line]


def cmd_pr(event_path: str | None, changed_path: str | None, repo: Path) -> int:
    if not event_path:
        print("pr checks: not a pull request (no $GITHUB_EVENT_PATH); nothing checked")
        return 0
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"pr checks: could not read the event {event_path}: {e}", file=sys.stderr)
        return 2
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        print("pr checks: the event is not a pull request's; nothing checked")
        return 0
    if changed_path:
        changed = Path(changed_path).read_text(encoding="utf-8").split()
    else:
        changed = changed_files_from_git((pr.get("base") or {}).get("sha", ""), repo)
        if changed is None:
            print("pr checks: could not list the files this pull request changes", file=sys.stderr)
            return 2
    config = load_config(repo)
    problems = check_accessibility(pr, changed, config, repo)
    try:  # the spec profile's check, in a repository that carries it
        import spec_checks
    except ImportError:
        spec_checks = None
    if spec_checks is not None:
        problems += spec_checks.check_spec(pr, changed, config, repo)
    for p in problems:
        print(f"pr checks: {p}", file=sys.stderr)
    if problems:
        print(
            "pr checks: after editing the description, push again or re-run the job: "
            "an edit alone does not start CI.",
            file=sys.stderr,
        )
        return 1
    print(f"pr checks: passed ({len(changed)} changed file(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="checks.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("commit-msg", help="refuse a bare #N in a commit message")
    c.add_argument("file")
    r = sub.add_parser("pr", help="check a pull request in CI")
    r.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    r.add_argument("--changed", help="a file listing the changed paths (default: from git)")
    p.add_argument("--repo", help="the repository (default: found from the working directory)")
    args = p.parse_args(argv)
    repo = Path(args.repo).resolve() if args.repo else repo_root(Path.cwd())
    if args.command == "commit-msg":
        return cmd_commit_msg(Path(args.file), repo)
    return cmd_pr(args.event, args.changed, repo)


if __name__ == "__main__":
    sys.exit(main())
