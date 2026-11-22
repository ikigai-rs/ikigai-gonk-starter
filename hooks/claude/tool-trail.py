#!/usr/bin/env python3
"""OPTIONAL, and Claude Code only: write one line per tool call to this working tree's session
trail, `.gonk/state/trail/tools.log` (tools/trail.py), in ikigai-log's `log:ToolCall` format.

Wired to three Claude Code hook events, for every tool (matcher `*`):

    PreToolUse          notes when the call began (by its tool_use_id), writes no line
    PostToolUse         the call answered: `outcome=ok`, `dur=` since it began, `result=` digest
    PostToolUseFailure  the call failed: `outcome=failed`, its error's digest as `result=`

A call `require-claim.py` refuses is written by that hook, as `outcome=refused`. A call a
person declines at a permission prompt sends no event, so it leaves no line.

The line carries the tool's name, the session's IRI (`urn:claude:session:{session_id}`), the
outcome, the duration and the arguments' SHA-256, and NO argument or result text unless the
operator opted in with `[trail] text = true` in `.gonk/local.toml`.

It never refuses anything and never writes to standard output: it always exits 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUTCOMES = {"PostToolUse": "ok", "PostToolUseFailure": "failed"}


def repo_of(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".gonk" / "layout.toml").is_file():
            return candidate
    return None


def handle(event: dict) -> None:
    repo = repo_of(Path(event.get("cwd") or ".").resolve())
    if repo is None:
        return  # not a gonk-starter repository: none of this hook's business
    sys.path.insert(0, str(repo / "tools"))
    import trail  # the repository's own copy

    name = event.get("hook_event_name")
    if name == "PreToolUse":
        trail.hook_started(repo, event)
    elif name in OUTCOMES:
        trail.hook_ended(repo, event, OUTCOMES[name])


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if isinstance(event, dict):
            handle(event)
    except Exception:  # noqa: BLE001  (a trail never stands in a tool call's way)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
