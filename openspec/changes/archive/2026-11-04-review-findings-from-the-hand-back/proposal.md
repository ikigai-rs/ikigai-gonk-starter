# Review findings come back in the hand-back, and the dispatcher files them

## Why
The worker review mode appends one Finding per problem and links it to its job. The published worker
grant is read, claim and comment, so on a real worker every finding is refused, and filing needs
ledger write, which would also let a worker close and relabel (ledger item 3065). As for pull request
aliases, workers keep today's grant and the dispatcher, which already closes, files what is final.

## What changes
The worker keeps its review preamble, unchanged-worktree check and report validation, and puts the
validated findings on its hand-back comment as one fenced JSON block, making no append and no link.
A new `gonk.py findings N [--close]` validates that block by the same rules and files the items as the
worker did. The format, validator and filing move to `tools/review_findings.py`, shared by both.

## Impact
Artifacts: none

Repository tooling in `tools/`, worker documentation and the harness guide. No grant changes: a worker
stays on `--worker`, and a review job now runs on one. A job handed back by an older worker carries no
block; `gonk.py findings` says so and files nothing.
