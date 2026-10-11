# Working in this repository

These rules are for every agent and every person working here. They are short on purpose:
each one exists because work went wrong without it. `CLAUDE.md` is a link to this file, so
every agent reads the same rules.

The work lives in a **ledger** on gonk: items you file, claim, comment on and close, and
`next`, which answers *what should I do next* with reasons. What the software is supposed
to do lives in `openspec/`. The code lives in `artifacts/<name>/`, one directory per
deployable thing. `.gonk/` says how this repository reaches gonk and which ledger is its.

## The loop

```sh
python3 tools/gonk.py next                         # what is ready, ranked, and why
python3 tools/gonk.py claim 12                     # take item 12, on a 30-minute lease
# reproduce, fix, open a PR, get CI green
python3 tools/gonk.py report 12 --evidence <PR URL>   # the evidence as a comment; hand it back
python3 tools/gonk.py close 12 --evidence <PR URL>    # the dispatcher, once the evidence holds
```

Two roles, and one person may hold both. **Whoever does the work** claims the item, does it,
and reports: the evidence goes on the item as a comment and the claim is handed back.
**Whoever dispatched it** checks that evidence and closes the item. Working alone, you are
your own dispatcher, and `close` is the step after you have checked your own evidence. An
unattended worker on another machine is only ever the first role: its grant lets it claim,
renew, comment and release, and closing stays with the dispatcher.

`just next`, `just claim 12`, `just report 12 <URL>` and `just close 12 <URL>` are the same
commands. An agent that speaks MCP has the same actions as tools (`ledger-next__source`,
`ledger-claim__sink`, `ledger-comment__sink`, `ledger-claim__delete`);
`python3 tools/gonk.py init` prints the command to configure it with.

## 1. Claim the item before you write

- **No claim, no write.** Before changing a file, claim the ledger item the change is for.
  A change with no item gets one first (`python3 tools/gonk.py file "<title>"`).
- **A claim is a lease.** `claim` takes the item for 30 minutes (`--lease 2h` for longer).
  Renew it by claiming the same item again before the lease runs out; a renewal is the same
  command.
- **An expired lease is not free.** When a lease runs out, `next` stops offering the item and
  names the holder whose lease expired. You can still renew your own. Nobody else may take
  it except by a **takeover** (`claim 12 --takeover-from <holder>`), which succeeds only if
  that holder still has it and the lease has really run out. Take over only work that is
  clearly abandoned, and say so in a comment.
- **If your claim is gone, stop.** If someone took it over, or `status` says you no longer
  hold it, stop writing. Put what you have on a branch, say where in a comment, and claim
  something else.
- **Hand it back when you stop.** `report 12 --evidence <URL>` hands back an item you
  finished; `release 12 --note "<why>"` hands back one you will not finish, saying why.
  Either way the item is free for the next holder, or for the dispatcher to close.
- **Someone else's claim is a fence.** A claim held by another holder is refused, with the
  holder's name. Pick another item; never work around a claim.

## 2. One agent per claimed item, one agent per working tree

An item has one holder at a time, and a working tree has one agent at a time. Two agents in
one tree overwrite each other's files no matter what the ledger says. To run agents in
parallel, give each its own working tree (`git worktree add`) and its own holder name
(`python3 tools/gonk.py init --holder <name>` in that tree). A tree holds one claim.

## 3. Reproduce before you fix

A bug report, a review finding, or a measurement someone else made is a **claim**, not a
task. Reproduce it first, preferably as a test that fails because of the defect. If it does
not reproduce, report it as not a defect, with the evidence (the dispatcher closes it with
the reason that fits, such as `--reason wontfix`); closing with no change is a good
outcome. If it is partly true, fix the true part and say what you did not do and why.
Never make a change only because the item expected one.

## 4. Every change through a pull request with green CI

- Work on a branch, open a pull request, and merge only when every CI check has passed.
  Never merge red, and never merge with checks still running.
- Run the checks locally before you push (`just gates`), and read their output.
- A command's exit status is the status of the LAST command in a pipeline: `tests | tail`
  reports `tail`'s success. Never pipe a command whose result you are trusting.
- Stage the files you changed by name. `git add -A` picks up files that are not yours.

## 5. Report with evidence; the dispatcher closes

Finished work is reported with the evidence that it is done: the pull request's URL, or the
reproduction that shows it is not a defect. `report` and `close` both refuse without
`--evidence <URL>`, and both record it as a comment, so a later reader can check it. The
dispatcher closes the item only after checking that evidence (the pull request merged, with
green CI); a close is the dispatcher's statement that the evidence holds, not the worker's.

## 6. Never a bare `#N`

Ledger items and pull requests both use numbers, and the ranges overlap. Write a ledger item
as `<ledger>#N` (`default#12` for the default ledger) or as a link to it, and a pull request
as `PR #N`, or `owner/repo#N` for another repository. In a commit message, write
`ledger item N`.

## Specs, artifacts, queries and scripts

- **Specs** are in `openspec/`: `specs/` is what the system does now, `changes/` what is
  proposed. Read the spec before changing behavior; a change in behavior starts as a change
  folder. See `openspec/README.md`.
- **Artifacts** are `artifacts/<name>/`. A spec or change names the artifacts it touches on
  an `Artifacts:` line; keep it true when you add or move code.
- **Queries and scripts** are files in `.gonk/queries/` and `.gonk/scripts/`. They are meant
  to be published to gonk as script resources (`urn:script:{name}`, from ikigai-script) and
  drafted from a sentence by `urn:nl:sparql` (ikigai-nl). `python3 tools/gonk.py init`
  publishes them when gonk offers `urn:script:`, and until then says they are ready but
  unpublished; do not publish them any other way.
- **Seeding.** `python3 tools/gonk.py seed` files each open task of a change in flight as a
  ledger item, keyed by the task, so running it again files nothing new, and links each task
  to the next with `blocks`, so `next` offers a change's tasks in the order `tasks.md` lists
  them. Check a task's box in `tasks.md` when its work is done.

## Optional: a hook for Claude Code

`hooks/claude/` holds a Claude Code hook that refuses a file edit while this working tree
holds no live claim. It is optional and Claude-only; every rule above holds without it.
