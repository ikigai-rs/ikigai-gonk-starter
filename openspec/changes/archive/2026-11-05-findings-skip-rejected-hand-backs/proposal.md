# A rejected hand-back is not filed

## Why
After a dispatcher rejects a hand-back, anyone who may comment can post a new one, the job reads as
awaiting again, and `gonk.py findings` takes the newest hand-back by a trusted worker, which can be
the rejected block (ledger item 3158). The ledger now records which hand-backs a comment rejected
(ikigai-rs/ikigai-ledger PR 87).

## What changes
`gonk.py findings` reads the `rejects` of every comment on the job, whoever posted it, and never files
a hand-back they name. When the latest trusted hand-back is rejected it files nothing and names the
rejecting comment. A ledger with no `rejects` behaves as before, and the warning for a passed-over
hand-back says what such a ledger cannot tell.

## Impact
Artifacts: none

Repository tooling in `tools/`, worker documentation and the harness guide. After a dispatcher's
ordinary comment on an awaiting job, which the ledger records as a reject, the worker must hand back
again before `findings` files anything. Close-then-reopen and rejects from before the ledger's upgrade
stay unmarked until a later reject names them (worker/README.md, "Review jobs").
