#!/usr/bin/env python3
"""The hub-and-spoke profile: file, dispatch, and close an arc with merge evidence.

    python3 tools/hub.py arc "<title>" --brief <file> [--parent N] [--after N]… [--labels a,b]
    python3 tools/hub.py dispatch <item> <dir> [--holder <name>] [--base <ref>]
    python3 tools/hub.py close <item> --pr <merged PR URL> [--note <text>]

`arc` files one ledger item whose body is the brief (`.gonk/templates/brief.md` is the shape),
labeled `arc`, and links it: `parent` to the item it is part of, and `blocks` from each arc
that must land first, so the ledger's `next` holds it back until they close.

`dispatch` gives an arc its own working tree and its own holder: a `git worktree` on a new
branch `arc-<item>`, and that tree's `.gonk/local.toml` saying how it reaches gonk (this
tree's way) under the holder `arc-<item>`. Two agents never share a tree or a claim (rule 2
of AGENTS.md); the agent started there claims the item and works from its brief.

`close` is the step after the authorized merge on green CI. It verifies the merged PR
with `gh` and records its URL and merge commit IRI on the ledger close StatusEvent
(ikigai-ledger 0.18.0 or later). It never merges a pull request.

Standard library only, beside tools/gonk.py, whose transport and configuration it shares. The
brief and working-tree refusals are harness sensors,
recorded in this working tree's firing log (tools/harness.py).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gonk  # noqa: E402  (beside this file; the path above makes it importable)

ARC_LABEL = "arc"


def cmd_arc(args, cfg: gonk.Config, make_ikigai) -> int:
    gonk.need(cfg, "ledger", "target")
    brief = Path(args.brief)
    if not brief.is_file():
        raise gonk.refuse("arc-needs-brief", cfg.repo, f"--brief {args.brief}: no such file")
    body = brief.read_text(encoding="utf-8").strip()
    if not body:
        raise gonk.refuse(
            "arc-needs-brief", cfg.repo, f"--brief {args.brief} is empty: an arc is its brief"
        )
    labels = ",".join([ARC_LABEL, *([args.labels] if args.labels else [])])
    if not re.fullmatch(r"[A-Za-z0-9._,:-]+", labels):
        raise gonk.ConfigError("--labels is a comma-separated list of label words, no spaces")
    words = ["sink", cfg.resource("append"), f"labels={labels}"]
    if args.priority is not None:
        words.append(f"priority={int(args.priority)}")
    ikigai = make_ikigai(cfg.target)
    answer = ikigai.run(" ".join(words), stdin=f"{args.title.strip()}\n\n{body}")
    m = gonk.APPENDED.search(answer.out) if answer.ok else None
    if m is None:
        return gonk.show(answer)
    display, iri = m.group(1), m.group(2)
    print(f"filed   {display} {args.title.strip()}")
    failed = 0
    # Each link names its object by pipe (`content`), where `link` takes it; a number for an
    # existing item, the IRI for the one just filed (`#` starts a comment in the grammar).
    links = [(iri, "parent", str(args.parent), f"{display} has parent {args.parent}")]
    links = links if args.parent is not None else []
    links += [(str(n), "blocks", iri, f"{n} blocks {display}") for n in args.after or []]
    for item, kind, target, what in links:
        done = ikigai.run(f"sink {cfg.resource('link')} item={item} type={kind}", stdin=target)
        if done.ok:
            print(f"linked  {what}")
        else:
            failed += 1
            print(f"failed  {what}: {done.text() or f'exit {done.code}'}", file=sys.stderr)
    print(f"\nnext: python3 tools/hub.py dispatch {display.rsplit('#', 1)[1]} ../arc-<n>")
    return 1 if failed else 0


def cmd_dispatch(args, cfg: gonk.Config, make_ikigai) -> int:
    gonk.need(cfg, "ledger", "target")
    holder = args.holder or f"arc-{args.item}"
    problem = gonk.holder_problem(holder)
    if problem:
        raise gonk.ConfigError(problem)
    dest = Path(args.dir).expanduser().resolve()
    if dest.exists():
        raise gonk.refuse(
            "dispatch-needs-new-tree",
            cfg.repo,
            f"{dest} exists: an arc's working tree is a new directory",
        )
    branch = f"arc-{args.item}"
    done = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(dest), args.base],
        cwd=cfg.repo,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise gonk.ConfigError(f"git worktree add: {(done.stderr or done.stdout).strip()}")
    target = cfg.target
    local = (
        {"transport": "socket", "socket": target.socket}
        if target.socket
        else {"transport": "quic", "quic": target.quic, "cert_dir": str(target.cert_dir)}
    )
    local["holder"] = holder
    gonk.write_toml(dest / ".gonk" / "local.toml", gonk.LOCAL_TOML_HEADER, local)
    print(f"dispatched item {args.item}: {dest} on branch {branch}, holder `{holder}`")
    print(
        "\nnext, for the agent that works it, in that directory:\n"
        f"  python3 tools/gonk.py claim {args.item}       take it, as `{holder}`\n"
        "  the item's body is the brief: work from it, open a pull request, merge on green,\n"
        f"  python3 tools/gonk.py report {args.item} --evidence <PR URL>"
    )
    return 0


PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")


def cmd_close(args, cfg: gonk.Config, make_ikigai) -> int:
    """After the dispatcher checks CI and merges, close with the PR and its merge commit."""
    gonk.need(cfg, "ledger", "target")
    requested = PR_URL.fullmatch(args.pr)
    if not requested:
        raise gonk.ConfigError("--pr must be a GitHub pull request URL")
    try:
        done = subprocess.run(
            ["gh", "pr", "view", args.pr, "--json", "state,url,mergeCommit"],
            cwd=cfg.repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise gonk.ConfigError(f"could not verify merged PR: {e}") from e
    if done.returncode != 0:
        raise gonk.ConfigError(f"gh pr view: {(done.stderr or done.stdout).strip()}")
    try:
        pr = json.loads(done.stdout)
        url = pr["url"]
        canonical = PR_URL.fullmatch(url)
        sha = pr["mergeCommit"]["oid"]
        if (
            pr["state"] != "MERGED"
            or canonical is None
            or canonical.groups() != requested.groups()
            or not re.fullmatch(r"[0-9a-fA-F]{40}", sha)
        ):
            raise ValueError("PR is not verified as merged with a valid merge commit")
    except (ValueError, KeyError, TypeError) as e:
        raise gonk.ConfigError(f"could not verify merged PR: {e}") from e
    owner, repo, _ = canonical.groups()
    close = argparse.Namespace(
        item=args.item,
        evidence=[url, f"https://github.com/{owner}/{repo}/commit/{sha}"],
        reason="done",
        note=args.note,
    )
    return gonk.cmd_close(close, cfg, make_ikigai)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hub.py", description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="the repository (default: found from the working directory)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("arc", help="file an arc whose body is its brief, and link it")
    s.add_argument("title")
    s.add_argument("--brief", required=True, help="the brief, a Markdown file")
    s.add_argument("--parent", type=int, help="the item this arc is part of")
    s.add_argument(
        "--after", type=int, action="append", help="an arc that must land first (repeatable)"
    )
    s.add_argument("--labels", help="more labels, comma-separated (`arc` is always one)")
    s.add_argument("--priority", type=int)
    s.set_defaults(run=cmd_arc)

    s = sub.add_parser("dispatch", help="a working tree and a holder of its own for an arc")
    s.add_argument("item", type=int)
    s.add_argument("dir", help="the new working tree")
    s.add_argument("--holder", help="the holder it claims under (default: arc-<item>)")
    s.add_argument("--base", default="HEAD", help="what the branch starts from (default: HEAD)")
    s.set_defaults(run=cmd_dispatch)
    s = sub.add_parser("close", help="close an item with its merged PR and merge commit evidence")
    s.add_argument("item", type=int)
    s.add_argument("--pr", required=True, help="the merged GitHub pull request URL")
    s.add_argument("--note")
    s.set_defaults(run=cmd_close)
    return p


def main(argv: list[str] | None = None, make_ikigai=gonk.Ikigai) -> int:
    args = parser().parse_args(argv)
    try:
        repo = Path(args.repo).resolve() if args.repo else gonk.find_repo(Path.cwd())
        return args.run(args, gonk.load(repo), make_ikigai)
    except gonk.ConfigError as e:
        print(f"hub.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
