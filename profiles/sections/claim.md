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
