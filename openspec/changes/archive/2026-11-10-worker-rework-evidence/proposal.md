# Preserve evidence from existing PR reworks

## Why
A rework can push to an existing PR branch and write no report. Looking only at the fresh worker branch loses its evidence.

## What changes
Snapshot same-repository PRs from the latest previous Evidence hand-back before the agent runs; rediscover changed heads afterward. Clarify the rework and report instructions. Never treat an unchanged or unreadable previous PR as new evidence.

## Impact
Artifacts: none

Repository tooling: `tools/worker.py`, its hermetic worker tests and worker documentation.
