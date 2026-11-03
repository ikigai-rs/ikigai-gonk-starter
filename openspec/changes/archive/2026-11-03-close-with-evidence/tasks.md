# Tasks

- [x] 1. Forward repeatable close evidence and preserve proposed worker evidence (Structured close evidence)
- [x] 2. Add and document the hub merged-PR close step (Merged pull request evidence)
- [x] 3. Test command behavior, add real-ledger history/Turtle coverage, run available checks, update current specs and archive (Structured close evidence)

Validation: the regression first failed because close sent no `evidence=` argument.
The available unit tests pass. The history/Turtle integration test is included but
was not executed locally: `ikigai-gonk` is not installed; CI also skips live-gonk
integration tests. Item Turtle includes the readable note, not StatusEvent triples.
