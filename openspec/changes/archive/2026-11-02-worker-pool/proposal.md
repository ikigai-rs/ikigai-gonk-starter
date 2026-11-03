# A shared pool any free loop claims from, and `worker:mac`

## Why
The hub routes each item to one machine's `worker:<name>`, so an item waits for one machine
while another's loop of the same agent is free, and every new machine is another choice for the
hub. Macs need a target of their own for macOS-only work (ledger item 2793).

## What changes
Per-agent loops read `worker:pool`, and on a Mac `worker:mac`, after their own label. `gonk.py
dispatch` targets the pool unless `--labels` names a `worker:` label. The `Worker:` status line and
coverage know the pool. A loop that loses a claim race goes on to its next candidate. Each agent
runs with `CARGO_TARGET_DIR` in its own worktree. The model stays the dispatcher's choice: the pool
replaces the machine label, not the `agent:` label.

## Impact
Artifacts: none

Touches `tools/gonk.py`, `tools/worker.py`, the worker's sample configuration and documentation.
