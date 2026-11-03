# Close with structured evidence

## Why
Close currently sends the PR URL only in comment text. Ledger 0.18.0 can attach
repeatable evidence IRIs to the close StatusEvent, making the decision inspectable.

## What changes
- Forward every `gonk.py close --evidence` URL to the ledger close resource.
- Keep the worker hand-back PR URL as proposed evidence; workers never close or merge.
- Add a hub close step that reads a merged GitHub PR and passes its canonical PR URL
  and merge commit IRI to close. Existing merge authorization and CI rules still apply.

## Impact
Artifacts: none

Repository tooling in `tools/`, the generated hub profile, and their tests.
Requires a gonk carrying ikigai-ledger 0.18.0 or later; older ledgers cannot store
structured close evidence.
