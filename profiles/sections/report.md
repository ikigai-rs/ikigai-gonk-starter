## 5. Report with evidence; the dispatcher closes

Finished work is reported with the evidence that it is done: the pull request's URL, or the
reproduction that shows it is not a defect. `report` and `close` both refuse without
`--evidence <URL>`, and both record it as a comment, so a later reader can check it. The
dispatcher closes the item only after checking that evidence (the pull request merged, with
green CI); a close is the dispatcher's statement that the evidence holds, not the worker's.
A rejection says why: `reject` refuses without `--note`, so the next holder is not left guessing.
