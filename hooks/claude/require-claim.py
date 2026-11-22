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

Before any of that, and for every tool it is given (`Bash` too), it applies the repository's
HOOK RULES: the `harness:HookRule` entries of `.gonk/harness/harness.ttl`, loaded when the hook
starts (tools/harness.py, `load_hook_rules`). A rule names the tools it applies to, a pattern
over the call's command or path, refuse or warn, and the sentence the agent sees. A refusal
exits 2 with that sentence; a warning lets the call through and hands the sentence to the
agent as context. A malformed rule is said on standard error and ignored, never applied.

Each refusal is a sensor of the repository's harness (`.gonk/harness/harness.ttl`) and is
recorded in its firing log, best effort: recording never changes what the hook decides. A
refused call is also one `outcome=refused` line in the session trail (tools/trail.py), which
`tool-trail.py` writes for every other call; that is best effort too.
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


def trail_refused(event: dict) -> None:
    """Write the refused call to the session trail (tools/trail.py). Never raises."""
    try:
        repo = repo_of(Path(event.get("cwd") or ".").resolve())
        if repo is None:
            return
        sys.path.insert(0, str(repo / "tools"))
        import trail  # the repository's own copy

        trail.hook_ended(repo, event, "refused")
    except Exception:  # noqa: BLE001  (recording a call never changes the decision)
        pass


def harness_of(repo: Path):
    """The repository's own tools/harness.py, or None when it carries none."""
    sys.path.insert(0, str(repo / "tools"))
    try:
        import harness  # the repository's own copy

        return harness
    except Exception:  # noqa: BLE001  (a broken harness never blocks; the claim check stands)
        return None


def apply_rules(event: dict) -> tuple[int, str]:
    """(exit code, message) of the repository's hook rules on this call: 2 and the sentences
    of the rules that refuse, else 0 and those of the rules that warn (empty when none fire).
    The repository is the file's, for a path, else the working directory's."""
    tool_input = event.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    cwd = Path(event.get("cwd") or ".")
    start = (Path(raw) if Path(raw).is_absolute() else cwd / raw) if isinstance(raw, str) else cwd
    repo = repo_of(start.resolve().parent if isinstance(raw, str) else start.resolve())
    harness = harness_of(repo) if repo is not None else None
    if harness is None:
        return 0, ""
    rules, problems = harness.load_hook_rules(repo)
    for line in problems:
        print(f"require-claim: hook rule ignored: {line}", file=sys.stderr)
    refused, warned = harness.fire_hook_rules(rules, event, repo)
    if refused:
        return 2, "\n".join(f"{r.id}: {r.says}" for r in refused)
    return 0, "\n".join(f"{r.id}: {r.says}" for r in warned)


def decide(event: dict, make_ikigai=None) -> tuple[int, str]:
    """(exit code, message) for one PreToolUse event: the hook rules first, then, for a file
    edit, the claim. Exit 0 with a message is a warning, passed to the agent as context."""
    code, said = apply_rules(event)
    if code:
        return code, said
    code, message = check_edit(event, make_ikigai)
    return code, "\n".join(m for m in (message, said) if m) if code else said


def check_edit(event: dict, make_ikigai=None) -> tuple[int, str]:
    """(exit code, message) of the claim check for one PreToolUse event."""
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
    if code and isinstance(event, dict):
        trail_refused(event)
    if message and code:
        print(message, file=sys.stderr)
    elif message:  # a warning: the call goes ahead, and the agent is told why it was flagged
        context = {"hookEventName": "PreToolUse", "additionalContext": message}
        print(json.dumps({"hookSpecificOutput": context}))
    return code


if __name__ == "__main__":
    sys.exit(main())
