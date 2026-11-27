## ADDED Requirements

### Requirement: A reject says where the item goes next
`gonk.py reject N --note "<why>"` MUST take exactly one of `--redispatch` and `--park`, and MUST
refuse a bare reject with a sentence naming both flags and saying that a reject alone leaves the
item skipped by the worker that handed it back. `--redispatch` MUST check everything `dispatch`
checks (the item open, one `repo:` label, not blocked unless `--allow-blocked` with a
`--dispatch-note`, valid routing labels, who it is from) BEFORE writing the reject, and refuse
having written nothing; then write the reject and a dispatch, by `dispatch`'s own writes, naming
the `worker:` and `agent:` labels the item carries, or `--labels`, which replace them. With no
`--dispatch-note` the dispatch says to do the fixes in the reject note, on the same pull request's
branch, then hand back. A dispatch write that fails after the reject landed MUST say the item is
rejected and NOT dispatched, print the `gonk.py dispatch` command that finishes it, and exit
non-zero. `--park` MUST write the reject, then take off every `worker:` label and no other, and
dispatch nothing. Both notes keep the note bound.

#### Scenario: A bare reject
- **WHEN** `reject N --note "<why>"` names neither flag
- **THEN** it is refused, naming `--redispatch` and `--park`, and nothing is written

#### Scenario: Sent back to its worker
- **GIVEN** an item a worker handed back
- **WHEN** the dispatcher runs `reject N --note "<why>" --redispatch`
- **THEN** a router's dispatch naming the same routing labels follows the reject, newer than the
  hand-back, so the worker that handed it back takes it again

#### Scenario: A blocked item
- **WHEN** `reject --redispatch` is run on an item an open item blocks, without `--allow-blocked`
- **THEN** it is refused naming the blocker, and not even the reject is written

#### Scenario: Parked for a person
- **WHEN** the dispatcher runs `reject N --note "<why>" --park`
- **THEN** the item's `worker:` labels are taken off, its `agent:` and other labels stay, and no
  dispatch is written
