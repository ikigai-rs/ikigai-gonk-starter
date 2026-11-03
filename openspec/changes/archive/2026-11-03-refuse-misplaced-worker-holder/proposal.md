# Refuse misplaced worker settings

## Why
TOML keeps keys after a table header inside that table. A misplaced holder silently
falls back to the machine holder, mixing per-agent history.

## What changes
Refuse holder in any table with a placement remedy, and refuse unknown repository keys.
Document the required holder placement in the sample.

## Impact
Artifacts: none

Touches tools/worker.py, worker/worker.sample.toml and worker configuration tests.
