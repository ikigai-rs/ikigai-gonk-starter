## 1. Claim the item before you write

- **No claim, no write.** Before changing a file, claim the ledger item the change is for.
  A change with no item gets one first (`python3 tools/gonk.py file "<title>"`).
- **A claim is a lease.** `claim` takes the item for 30 minutes (`--lease 2h` for longer).
  Renew it before the lease runs out with `claim 12 --renew`; claiming the item this tree
  holds again is the same renewal. A renewal never takes: once the claim has changed hands
  or been released it is refused, and does not take the item back. For long work, take a
  longer lease and renew it as you go, at each commit or push.
- **An expired lease is not free.** When a lease runs out, `next` stops offering the item and
  names the holder whose lease expired. You can still renew your own. Nobody else may take
  it except by a **takeover** (`claim 12 --takeover-from <holder>`), which succeeds only if
  that holder still has it and the lease has really run out. Take over only work that is
  clearly abandoned, and say so in a comment.
- **If your claim is gone, stop.** If someone took it over, `status` says you no longer
  hold it, or a renewal, `report` or `release` exits 3 (the ledger's conflict), stop
  writing. Put what you have on a branch, say where in a comment, and claim something else.
- **Hand it back when you stop.** `report 12 --evidence <URL>` hands back an item you
  finished; `release 12 --note "<why>"` hands back one you will not finish, saying why.
  Either way the item is free for the next holder, or for the dispatcher to close.
- **Someone else's claim is a fence.** A claim held by another holder is refused, with the
  holder's name. Pick another item; never work around a claim.

Coming from kata, three things work differently here:

| In kata | Here |
| --- | --- |
| An expired claim is unassigned, and `next` offers it again. | An expired lease is still held. `next` names it `lease-expired`, a plain claim is refused, and only a takeover takes it. |
| Closing keeps the owner. | `report`, `release` and `close` end the claim. Closed work is held by nobody. |
| Force takes a claim from its owner. | There is no force. A takeover takes only an expired lease, from the holder it names; the dispatcher releases a live one. |
