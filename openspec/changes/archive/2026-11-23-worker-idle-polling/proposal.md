# Poll the ledger less while a worker is idle

## Why
A hub research spike over gonk's door log (2026-11-11 to 2026-11-23) found that 58% of all
authenticated requests are the two worker machines asking `next`, about 300 an hour each, and
that an item is read every 30 seconds for as long as a kept worktree of it sits on a machine
(ledger item 4102). Both come from the worker loop: `next` once per label set at every 60-second
poll, and `Worker.reclaim` asking about every kept worktree's item at every poll of every loop.

## What changes
- With nothing to take, the loop's wait doubles from `poll` up to a new `poll_max` (default 5
  minutes) while what `next` ranks stays the same; a change in that ranking, or an item claimed,
  starts it again from `poll`.
- `Worker.reclaim` asks about each kept worktree's item at most every `[disk] reclaim_every`
  (default 10 minutes), and at every poll while the free disk is under the floor. A worktree not
  asked about at a poll is left as it is, so every removal is decided on a fresh answer.
- The heartbeat is unchanged: one read and one renewal every `renew_every`.

## Impact
Artifacts: none

Touches `tools/worker.py`, `tests/test_worker_polling.py`, `tests/test_worker_disk.py`, the
worker's README and sample configuration, and the workers section of `AGENTS.md` (and its
profile section).
