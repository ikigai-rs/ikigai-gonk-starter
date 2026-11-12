# Record accessibility check results

## Why
Worker briefs and accessibility records can omit checks nobody could run, hiding gaps in evidence.

## What changes
Require Pass, Fail or Blocked with evidence for every named accessibility check, including unrun screen reader checks. Carry the rule through worker PR guidance, generated instructions, harness comments and accessibility records.

## Impact
Artifacts: none

Repository tooling and documentation: `tools/worker.py`, profiles, generator templates and their tests.
