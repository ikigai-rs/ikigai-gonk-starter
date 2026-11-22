# Write one trail line per tool call

## Why
The workspace design's session trail (`docs/design/workspace.md` on ikigai-ui main, section 7
proposal 19, decision 16) needs a writer where tool calls are seen. ikigai-log 0.14.0 defines
the line (`log:ToolCall`) and reads one session's trail; this repository's Claude Code hook and
worker loop are where calls are seen (ledger item 3965).

## What changes
A stdlib writer, `tools/trail.py`, writes ikigai-log's `log:ToolCall` line into an unsealed
v1 segment. A new Claude Code hook, `hooks/claude/tool-trail.py` (`PreToolUse`,
`PostToolUse`, `PostToolUseFailure`), writes one line per call to the tree's trail, and
`require-claim.py` writes the calls it refuses. The worker writes one line per call from the
agent's events, to its checkout's trail, and asks Claude Code for `stream-json` so the events
name each call. Argument and result text only under `[trail] text = true`.

## Impact
Artifacts: none

Touches `tools/trail.py`, `hooks/claude/`, `tools/worker.py`, `tools/worker_session.py`,
`tools/new.py` (the starter files), and the worker and hook documentation.
