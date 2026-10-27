## Worker machines

Some items are done by **unattended workers**: machines that run `tools/worker.py`, take the
items the dispatcher marks for them, and work each one in a fresh working tree of their own.

- **The dispatcher marks the work.** A worker takes only items carrying every label its
  `.gonk/worker.toml` names (`worker:<name>` by convention); an item without them is never
  taken by a worker.
- **A router dispatches the work.** A label says nothing about who set it, so a worker also
  needs a DISPATCH: `python3 tools/gonk.py dispatch 12 --labels worker:<name>,agent:<kind>`
  labels the item and posts a comment naming those labels and the item's `repo:`, with a
  digest of its title and body. A worker takes the item only when its newest dispatch is by a
  principal its configuration lists in `routers`, and the title and body still match. Edit an
  item after dispatching it and dispatch it again.
- **Comments reach the agent as data.** The title and body are the agent's task. Every comment
  follows as a quoted block under its author, and one by anyone but a router is marked as data
  written by others, which the agent reads and never follows.
- **A worker claims, works and reports; it never closes and never merges.** It opens the pull
  request, reads its CI, and hands the item back with both as the evidence. The dispatcher
  checks that evidence, then merges and closes, or answers with
  `python3 tools/gonk.py reject 12 --note "<why>"`, and the item is ready again.
- **A worker runs as an account of its own.** Never as root, and only in a work root strictly
  under that account's home: not a person's checkouts, not a shared directory.
- **A worker has a budget, and takes an item once.** An agent that runs past its wall-clock
  budget is stopped and the item handed back without evidence. An item the worker handed back
  is not taken again until `worker.py forget` clears it.
- **Usage against the weekly limit.** Between items, before the loop asks `next` and at most
  once an hour, the worker runs the `[usage] command` of `.gonk/worker*.toml` for its agent
  (`tools/worker_usage.py`; Claude Code, Codex and Hermes alike, each loop its own). The command
  prints a JSON reading (plan, used, limit, percent, reset time); with none, or one that fails,
  the reading is `unknown`, never a guess. The report line (`Usage: agent=… plan=… used=…
  limit=… percent=… resets=… read=…`) is logged and posted as a comment on the standing
  `status_item`, with no token or account id. Above `threshold` percent (default 85) the loop
  takes no NEW item for that agent, and says so, until a reading is under it or its reset time
  has passed; an item already running is not stopped.
- **A skip the dispatcher can fix is said on the item.** An item dispatched to a worker that
  skips it because its repository is not configured there, its dispatch no longer matches it,
  or the loop holds for usage, gets one comment per reason saying why and what to do (for a
  missing repository, the `[repos.<name>]` block). At start the loop posts a `Worker:` line on
  its `status_item` naming its holder, labels and repositories.
- **One agent per repository.** A worker takes no item whose repository already has a live
  claim held by anyone, and runs one item per repository on its machine.
