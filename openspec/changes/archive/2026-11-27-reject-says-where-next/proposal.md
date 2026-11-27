# A reject says where the item goes next

## Why
On 2026-11-26/27 the dispatcher sent seven items back with `gonk.py reject N --note "..."` and
dispatched none of them again; for about 12 hours no worker took any of them, and nothing said
so (ledger item 4484). A worker skips an item it handed back until a router dispatches it again
(`tools/worker.py`, `redispatched_since`), and a reject is no dispatch, while `next` listed each
item as ready with its routing labels still on it.

## What changes
- `gonk.py reject` takes exactly one of `--redispatch` and `--park`; a bare reject is refused
  with a sentence naming both, recorded as a firing of `reject-says-where-next`.
- `--redispatch` checks everything `dispatch` checks first, then writes the reject and a
  dispatch by `dispatch`'s own writes, with the item's routing labels (or `--labels`) and
  `--dispatch-note` (or a default note). A dispatch that fails after the reject prints the
  command that finishes it and exits non-zero.
- `--park` writes the reject and takes the `worker:` labels off.

## Impact
Artifacts: none

Touches `tools/gonk.py`, its tests, `profiles/harness/report.ttl` (and the assembled harness),
the report and workers sections of `AGENTS.md` (and their profile sections), `README.md`,
`worker/README.md` and `justfile`. A bare `gonk.py reject N --note ...` is refused after this
change. No persisted state changes.
