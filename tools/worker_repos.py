#!/usr/bin/env python3
"""Enroll an owner's repositories in a worker's configurations, and check the token can push.

    python3 tools/worker_repos.py --owner acme --dry-run       what it would add, as a diff
    python3 tools/worker_repos.py --owner acme                 add the missing [repos.<name>]
    python3 tools/worker_repos.py --owner acme --check-push    then ask GitHub about each
    python3 tools/worker_repos.py --check-push                 only ask, about every enrolled

Standard library only (Python 3.11 or later), beside tools/worker.py. It lists the owner's
repositories with `gh repo list <owner> --json name,url,isArchived,visibility --limit 1000`,
skips archived ones (and private ones unless `--include-private`; `--only` and `--exclude` take
names or shell patterns), and appends a `[repos.<name>]` block (`remote`, `github`) for each one
missing from EACH worker configuration it is pointed at: every `.gonk/worker*.toml` of this
checkout by default, so `worker.toml` and each loop's `worker-<agent>.toml` alike.

It never removes or edits a block: it only appends, after the file's last line, so every
comment and every hand edit stays exactly as it was. A repository already enrolled under
another name (a block whose `github` is the same `owner/name`) is left alone. Run it again and
it adds nothing. Each file is written atomically (a temporary file beside it, then a rename),
keeps its mode, and is checked to parse, with every block it had unchanged, before it replaces
the old one.

`--check-push` asks GitHub, for every repository enrolled in those configurations, whether the
account the worker's `gh` is signed in as may push there (`gh api repos/<owner>/<name> --jq
.permissions.push`), and lists the ones it may not. What it then says depends on the token's type
(`gh auth status`): a fine-grained token's list of repositories is a manual step on GitHub, and
this says where; a classic token reaches what the account's collaborator grants reach, so this
points to `repo-protect --grant-worker` instead.

Exit status: 0 done; 1 gh or a write failed; 2 a usage or configuration error; 3 `--check-push`
found repositories the worker cannot push to.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worker  # noqa: E402  (beside this file; the path above makes it importable)

gonk = worker.gonk

OK, FAILED, CONFIG, CANNOT_PUSH = 0, 1, 2, 3
LIMIT = 1000
BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class Failed(Exception):
    """gh did not answer, or a file could not be written: exit 1."""


def run_gh(gh: list[str], *argv: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([*gh, *argv], capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"`{gh[0]}` is not on PATH")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", "gh did not answer within 120s")


def first_line(done: subprocess.CompletedProcess) -> str:
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[0] if lines else f"exit {done.returncode}"


def list_repositories(gh: list[str], owner: str) -> list[dict]:
    fields = "name,url,isArchived,visibility"
    done = run_gh(gh, "repo", "list", owner, "--json", fields, "--limit", str(LIMIT))
    if done.returncode != 0:
        raise Failed(f"gh repo list {owner} failed: {first_line(done)}")
    try:
        rows = json.loads(done.stdout or "[]")
    except ValueError as e:
        raise Failed(f"gh repo list {owner} did not answer JSON: {e}") from e
    if len(rows) >= LIMIT:
        note = f"note: gh listed {LIMIT} repositories, its limit; some may be missing"
        print(note, file=sys.stderr)
    return rows


def patterns(text: str | None) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def select(rows: list[dict], args) -> tuple[list[dict], list[tuple[str, str]]]:
    """(the repositories to enroll, sorted by name; (name, why) for each left out)."""
    only, exclude = patterns(args.only), patterns(args.exclude)
    take, skip = [], []
    for row in sorted(rows, key=lambda r: r["name"].lower()):
        name = row["name"]
        if row.get("isArchived"):
            skip.append((name, "archived"))
        elif (row.get("visibility") or "PUBLIC").upper() != "PUBLIC" and not args.include_private:
            skip.append((name, f"{row['visibility'].lower()} (--include-private takes it)"))
        elif only and not any(fnmatch.fnmatchcase(name, p) for p in only):
            skip.append((name, "not named by --only"))
        elif any(fnmatch.fnmatchcase(name, p) for p in exclude):
            skip.append((name, "named by --exclude"))
        elif not worker.REPO_SHAPE.match(name):
            skip.append((name, "not a name a `repo:<name>` label can carry"))
        else:
            take.append(row)
    return take, skip


def slug_of(block: dict) -> str | None:
    if isinstance(block, dict) and block.get("github"):
        return block["github"]
    m = worker.GITHUB_REMOTE.search(str((block or {}).get("remote", "")))
    return m.group(1) if m else None


def toml_key(name: str) -> str:
    return name if BARE_KEY.match(name) else json.dumps(name)


def block_lines(owner: str, row: dict) -> list[str]:
    slug = f"{owner}/{row['name']}"
    url = (row.get("url") or f"https://github.com/{slug}").rstrip("/")
    remote = url if url.endswith(".git") else url + ".git"
    return [
        f"[repos.{toml_key(row['name'])}]",
        f"remote = {json.dumps(remote)}",
        f"github = {json.dumps(slug)}",
    ]


def enrolled_text(path: Path, text: str, owner: str, take: list[dict]) -> tuple[str, list[str]]:
    """The configuration with a block appended for each repository it lacks, and their names.
    The text before the additions is the file's own, byte for byte."""
    try:
        table = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise gonk.ConfigError(f"{path}: {e}") from e
    repos = table.get("repos") or {}
    slugs = {slug_of(block) for block in repos.values()}
    added, lines = [], []
    for row in take:
        if row["name"] in repos or f"{owner}/{row['name']}" in slugs:
            continue
        lines += ["", *block_lines(owner, row)]
        added.append(row["name"])
    if not added:
        return text, []
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    head = [
        "",
        f"# Added by tools/worker_repos.py from `gh repo list {owner}`, {day}: one block per",
        "# repository this worker may work in, by the name its items' `repo:<name>` label uses.",
    ]
    if text and not text.endswith("\n"):
        text += "\n"
    new = text + "\n".join(head + lines[1:]) + "\n"  # the comment, then the first block
    # Refuse anything but an append: it parses, and every block it had is unchanged.
    after = tomllib.loads(new).get("repos") or {}
    if any(after.get(name) != block for name, block in repos.items()):
        raise gonk.ConfigError(f"{path}: appending would change a block it has; nothing written")
    if any(name not in after for name in added):
        raise gonk.ConfigError(f"{path}: an appended block did not parse; nothing written")
    return new, added


def write_atomically(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise Failed(f"{path}: could not write: {e}") from e


def configurations(repo: Path, named: list[str]) -> list[Path]:
    if named:
        paths = [Path(p).resolve() for p in named]
    else:
        paths = sorted((repo / ".gonk").glob("worker*.toml"))
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise gonk.ConfigError(f"no such configuration: {', '.join(map(str, missing))}")
    if not paths:
        raise gonk.ConfigError(
            f"no .gonk/worker*.toml in {repo}: write one first (`python3 tools/worker.py "
            "init-agents --name <worker> --agents claude,codex,hermes`, or copy "
            "worker/worker.sample.toml to .gonk/worker.toml)"
        )
    return paths


def check_push(gh: list[str], slugs: list[str]) -> list[tuple[str, str]]:
    """(owner/name, why) for each repository the signed-in account may not push to."""
    cannot = []
    for slug in slugs:
        done = run_gh(gh, "api", f"repos/{slug}", "--jq", ".permissions.push")
        answer = done.stdout.strip()
        if done.returncode != 0:
            cannot.append((slug, f"gh api: {first_line(done)}"))
        elif answer != "true":
            cannot.append((slug, f"permissions.push is {answer or 'absent'}"))
    return cannot


def token_kind(gh: list[str]) -> str:
    """"classic", "fine-grained" or "" (unknown), from the prefix class `gh auth status` shows."""
    done = run_gh(gh, "auth", "status")
    text = f"{done.stdout}\n{done.stderr}"
    if "github_pat_" in text:
        return "fine-grained"
    if re.search(r"\bgh[pousr]_", text):
        return "classic"
    return ""


def classic_page() -> list[str]:
    return [
        "The worker's token is a CLASSIC token: it reaches exactly the repositories the account",
        "may push to as a collaborator or member, and no token setting changes that. Grant the",
        "worker's account on each repository above (`repo-protect --grant-worker`), or have an",
        "owner invite it. worker/README.md step 3 has both token routes.",
    ]


def token_page(owners: list[str]) -> list[str]:
    lines = [
        "A fine-grained token's repositories are chosen by hand on GitHub. Signed in as the",
        "account the worker's token belongs to, open",
        "  https://github.com/settings/personal-access-tokens",
        "choose the worker's token, Edit, and under Repository access add the repositories",
        "above (its permissions stay as worker/README.md step 3 lists them).",
    ]
    for owner in owners:
        lines.append(
            f"If {owner} requires approval of fine-grained tokens, an owner approves the change at"
        )
        lines.append(
            f"  https://github.com/organizations/{owner}/settings/personal-access-token-requests"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="worker_repos.py", description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="the worker's checkout (default: found from here)")
    p.add_argument("--owner", help="the user or organization whose repositories to enroll")
    p.add_argument(
        "--config",
        action="append",
        default=[],
        help="a configuration to enroll in (repeatable; default: every .gonk/worker*.toml)",
    )
    p.add_argument("--include-private", action="store_true", help="private ones too")
    p.add_argument("--only", help="only these names or patterns (comma-separated)")
    p.add_argument("--exclude", help="never these names or patterns (comma-separated)")
    p.add_argument("--dry-run", action="store_true", help="print the diff; write nothing")
    p.add_argument(
        "--check-push", action="store_true", help="ask GitHub whether the worker can push"
    )
    p.add_argument("--gh", default="gh", help="the GitHub CLI (default: gh)")
    args = p.parse_args(argv)
    gh = shlex.split(args.gh)
    try:
        if not args.owner and not args.check_push:
            raise gonk.ConfigError("name --owner to enroll its repositories, or --check-push")
        repo = Path(args.repo).resolve() if args.repo else gonk.find_repo(Path.cwd())
        paths = configurations(repo, args.config)
        texts = {path: path.read_text(encoding="utf-8") for path in paths}
        if args.owner:
            take, skip = select(list_repositories(gh, args.owner), args)
            for name, why in skip:
                print(f"skip   {args.owner}/{name}: {why}")
            for path in paths:
                new, added = enrolled_text(path, texts[path], args.owner, take)
                shown = path.relative_to(repo) if path.is_relative_to(repo) else path
                if not added:
                    print(f"same   {shown}: every one of the {len(take)} is enrolled already")
                    continue
                if args.dry_run:
                    sys.stdout.writelines(
                        difflib.unified_diff(
                            texts[path].splitlines(keepends=True),
                            new.splitlines(keepends=True),
                            f"a/{shown}",
                            f"b/{shown}",
                        )
                    )
                    print(f"would  {shown}: add {len(added)}: {', '.join(added)}")
                else:
                    write_atomically(path, new)
                    print(f"added  {shown}: {len(added)}: {', '.join(added)}")
                texts[path] = new
        if not args.check_push:
            return OK
        slugs = []
        for text in texts.values():
            for block in (tomllib.loads(text).get("repos") or {}).values():
                slug = slug_of(block)
                if slug and slug not in slugs:
                    slugs.append(slug)
        cannot = check_push(gh, slugs)
        if not cannot:
            print(f"push   the worker may push to all {len(slugs)} enrolled repositories")
            return OK
        print(f"the worker cannot push to {len(cannot)} of {len(slugs)} enrolled repositories:")
        for slug, why in cannot:
            print(f"  {slug}  ({why})")
        owners = sorted({slug.split("/")[0] for slug, _ in cannot})
        if token_kind(gh) == "classic":
            print("\n".join(classic_page()))
        else:
            print("\n".join(token_page(owners)))
        return CANNOT_PUSH
    except gonk.ConfigError as e:
        print(f"worker_repos.py: {e}", file=sys.stderr)
        return CONFIG
    except Failed as e:
        print(f"worker_repos.py: {e}", file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
