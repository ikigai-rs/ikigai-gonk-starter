# Reclaim kept worktrees by what the ledger says of their items

## Why
A worktree kept after a run stays until `[disk] prune_after` (7 days) whatever became of its
item, and a build in one is 50-90 GB. On 2026-11-02 one machine stood at 16 GB free of 908 GB
with seven kept worktrees, about 490 GB, every item closed and its pull request merged, and two
workers failed on the full disk that day. The floor stops new items and frees nothing
(ledger item 2937).

## What changes
At each poll the worker asks the ledger about each kept worktree's item. It removes the worktree
when the item is closed, or when its claim moved from the holder that made the worktree to
another; a new `item-<N>.holder` file beside the worktree records who made it. Under the floor,
after those removals, it drops the `target/` of worktrees whose item is open and unclaimed, until
a reading is back at the floor. A worktree a claim still holds is never touched.

## Impact
Artifacts: none

Touches `tools/worker.py`, the worker's sample configuration and documentation. The age-based
pruning of ledger item 2816 is unchanged.
