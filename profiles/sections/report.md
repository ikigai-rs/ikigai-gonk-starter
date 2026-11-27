## 5. Report with evidence; the dispatcher closes

Finished work is reported with the evidence that it is done: the pull request's URL, or the
reproduction that shows it is not a defect. `report` and `close` both refuse without
`--evidence <URL>`, and both record it as a comment, so a later reader can check it. The
dispatcher closes the item only after checking that evidence (the pull request merged, with
green CI); a close is the dispatcher's statement that the evidence holds, not the worker's.
A rejection says why and where the item goes next: `reject` refuses without `--note`, so the
next holder is not left guessing, and without `--redispatch` (dispatch it again) or `--park` (take
its `worker:` labels off), because a reject alone leaves it skipped by the worker that handed it back.
