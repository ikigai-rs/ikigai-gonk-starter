# Report free disk, hold under a floor, and prune kept work

## Why
Each worktree builds Rust, often several GB, and a worker keeps the worktree of every run
without evidence and every item's log, with nothing pruning either. Nothing read free disk
after the probe at setup, so a full disk showed up only as an ordinary failed build
(ledger item 2816).

## What changes
Before each item the worker reads the free space on the volume holding its work root, posts
it in a `Disk:` line on its status item, and under `[disk] floor_gb` (default 30) takes no new
item until space returns. At start and once a day it prunes kept worktrees and logs older than
`[disk] prune_after` (default 7 days), never one whose branch has an open pull request.

## Impact
Artifacts: none

Touches `tools/worker.py`, the worker's sample configuration and documentation.
