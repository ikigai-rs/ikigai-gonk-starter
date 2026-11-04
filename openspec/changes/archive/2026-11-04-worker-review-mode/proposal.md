# A review mode for the worker, with findings filed by the worker

## Why
A review job could not run on a worker: its preamble forbids filing items and expects a pull
request. Agents must keep not filing items, so the agent reports and the worker files
(ledger item 3064).

## What changes
An item of class `ledger:ReviewJob` runs beside the change mode with the same claim, lease,
heartbeat, budget and hand-back, in a worktree at its head, under a preamble that forbids any
change. After the agent exits, the worker refuses a changed worktree or an invalid report, files
one keyed `ledger:Finding` per finding, links each to the job, and hands back with their IRIs as
the evidence. A change job is unaffected.

## Impact
Artifacts: none

Touches `tools/worker.py`, its documentation, the workers profile's rules and harness, and the
sample configuration's comments. A loop that takes review jobs needs `write` on its ledger, which a
grant enrolled with `--worker` does not hold.
