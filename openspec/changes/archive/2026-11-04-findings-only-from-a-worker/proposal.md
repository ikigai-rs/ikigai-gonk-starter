# Only a worker's hand-back is filed

## Why
Since ledger item 3065, `gonk.py findings N` files whatever block sits in job N's latest hand-back.
Anyone who may comment on the job can post one, so a person with comment access could have the
dispatcher file findings under its own authority. The command printed the author for a person to
check, and checked nothing itself (ledger item 3070).

## What changes
`gonk.py findings` files a block only from a hand-back whose author the ledger marks verified and
the dispatcher lists as a worker, and otherwise files nothing and says who posted it, why that
identity is not trusted, and how to trust it. The list is `workers` in a new git-ignored
`.gonk/dispatcher.toml`, because no gonk door lists its enrolled clients and their authority:
`ikigai-gonk client list` reads files on gonk's own machine. The newest hand-back wins among trusted
ones, and a newer one by anyone else is passed over with a warning naming its author.

## Impact
Artifacts: none

Repository tooling in `tools/`, worker documentation and the harness guide. A dispatcher must list
its workers before `findings` files anything. A worker that hands back over the owner's socket, or
on a ledger older than 0.15.0, has no verified author and is refused.
