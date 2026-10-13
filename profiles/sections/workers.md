## Worker machines

Some items are done by **unattended workers**: machines that run `tools/worker.py`, take the
items the dispatcher marks for them, and work each one in a fresh working tree of their own.

- **The dispatcher marks the work.** A worker takes only items carrying every label its
  `.gonk/worker.toml` names (`worker:<name>` by convention); an item without them is never
  taken by a worker.
- **A worker claims, works and reports; it never closes and never merges.** It opens the pull
  request, reads its CI, and hands the item back with both as the evidence. The dispatcher
  checks that evidence, then merges and closes, or answers with
  `python3 tools/gonk.py reject 12 --note "<why>"`, and the item is ready again.
- **One agent per repository.** A worker takes no item whose repository already has a live
  claim held by anyone, and runs one item per repository on its machine.
