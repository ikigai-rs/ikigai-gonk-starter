## Spec first

- **A change in behavior starts as a change folder**, `openspec/changes/<id>/`: its proposal,
  its spec deltas and its `tasks.md`, written before the code. A fix that restores what a spec
  already says needs none; anything that changes what a spec says does.
- **Seeding.** `python3 tools/gonk.py seed` files each open task of a change in flight as a
  ledger item, keyed by the task, so running it again files nothing new, and links each task
  to the next with `blocks`, so `next` offers a change's tasks in the order `tasks.md` lists
  them. Check a task's box in `tasks.md` when its work is done.
- **Take what `next` offers.** The first open task of each change is ready and the rest wait
  behind it, so working from `next` keeps a change's tasks in their written order.
- **Keep the spec true.** When a change is finished, its deltas become what `specs/` says and
  the change folder moves to `changes/archive/`, in the same pull request as its last task.
