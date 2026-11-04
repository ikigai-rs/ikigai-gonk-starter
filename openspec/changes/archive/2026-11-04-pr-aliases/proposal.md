# Name closed items by their pull requests

## Why
PR evidence should identify its item without parsing hand-back comments. Workers have
separate principals; giving them alias grants both expands their authority and makes the
first worker to register gh its permanent owner.

## What changes
The dispatcher records each GitHub PR URL in successful close evidence as a
`gh:owner/repo#number` alias. It registers gh as its own principal on first use.
Refusals remain visible in close output and do not undo or prevent closing.
Workers never register schemes or write aliases, and enrollment keeps its narrow grant.
The CLI continues resolving aliases through the configured ledger before any action.

## Impact
Artifacts: none

Implemented in repository tooling (`tools/`). Requires ikigai-ledger 0.18.0 or later and
operator enrollment of the dispatcher identity. No deployment or grant-file changes.
