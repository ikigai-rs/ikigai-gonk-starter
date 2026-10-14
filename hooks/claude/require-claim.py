#!/usr/bin/env python3
"""OPTIONAL, and Claude Code only: refuse a file edit when this working tree holds no live
claim on a ledger item.

A Claude Code PreToolUse hook. It reads the tool call as JSON on standard input and, for the
file-editing tools, finds the repository the file is in, reads the claim this working tree
recorded (.gonk/state/claim.toml, written by `tools/gonk.py claim`), and asks gonk whether
that claim is still the holder's and its lease still runs. Exit 2 refuses the edit and tells
the agent why; exit 0 lets it through.

This is a second layer. The protocol in AGENTS.md works without it, and every other agent
keeps it by following the rules. What it does not cover, stated so nobody relies on it:
a write through a shell command (`Bash`), which no hook can attribute to a file reliably.

Each refusal is a sensor of the repository's harness (`.gonk/harness/harness.ttl`) and is
recorded in its firing log, best effort: recording never changes what the hook decides.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EDITING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Paths inside the repository the protocol itself writes, which a claim cannot precede.
EXEMPT = (".gonk/state/", ".gonk/local.toml")


def repo_of(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".gonk" / "layout.toml").is_file():
            return candidate
    return None


def fire(control: str, repo: Path | None, event: dict) -> None:
    """Record a refusal in `repo`'s harness firing log (tools/harness.py). Never raises."""
    if repo is None:
        return
    try:
        sys.path.insert(0, str(repo / "tools"))
        import gonk  # the repository's own copy

        gonk.fire(
            control,
            "refusal",
            repo,
            tool=event.get("tool_name"),
            session=event.get("session_id"),
        )
    except Exception:  # noqa: BLE001  (recording a firing never changes the decision)
        pass


def decide(event: dict, make_ikigai=None) -> tuple[int, str]:
    """(exit code, message) for one PreToolUse event."""
    if event.get("tool_name") not in EDITING_TOOLS:
        return 0, ""
    tool_input = event.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return 0, ""
    path = Path(raw)
    if not path.is_absolute():
        path = Path(event.get("cwd") or ".") / path
    path = path.resolve()
    repo = repo_of(path.parent)
    if repo is None:
        return 0, ""  # not a gonk-starter repository: none of this hook's business
    relative = path.relative_to(repo).as_posix()
    if relative.startswith(EXEMPT):
        return 0, ""

    sys.path.insert(0, str(repo / "tools"))
    import gonk  # the repository's own copy, found from the edited file

    try:
        cfg = gonk.load(repo)
        gonk.need(cfg, "ledger", "target")
    except gonk.ConfigError as e:
        fire("edit-hook-not-wired", repo, event)
        return (
            2,
            "require-claim: this repository is not wired to gonk yet, so no claim can be "
            f"checked.\n{e}",
        )
    allowed, why = gonk.check_claim(cfg, (make_ikigai or gonk.Ikigai)(cfg.target))
    if allowed:
        return 0, ""
    fire("edit-without-live-claim", repo, event)
    return 2, f"require-claim: refused an edit to {relative}: {why}"


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError as e:
        fire("edit-hook-unreadable-input", repo_of(Path.cwd().resolve()), {})
        print(f"require-claim: the hook input was not JSON ({e}); refusing", file=sys.stderr)
        return 2
    code, message = decide(event)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
