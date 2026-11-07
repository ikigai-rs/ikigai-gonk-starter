# flight: a plan's steps in landing order

`?ledger=<name>&record=<n>`. The steps are the record item's `item.children` (closed ones too: a
landed step stays in the plan) and the targets of its `related` links, all from the record's
own JSON face; the ledger is never listed to find them. `?ledger=` names the RECORD's ledger
only: a step may be an item of any ledger (a run record's steps are often items of each
repository's own), so each step is read by its link target's IRI from its own ledger, shown as
`{ledger}#{number}` and linked to its own ledger's item page, and steps and their `blocks` edges
are keyed by IRI, since numbers repeat across ledgers. They are listed in the order they can land: topological by `blocks` within the plan,
ties broken by priority (lower first, none last), then number, then ledger. The first step that is open,
not handed back and blocked by nothing open is marked "Next to land" in words as well as by
its border. Steps caught in a blocking cycle are listed apart, since none of them can land.
A step that could not be read (no read grant for its ledger, or not found) is left out of the
plan but said under the heading and in the status announcement, counted by reason ("2 steps
could not be read (1 denied, 1 not found): ..."), with only its IRI shown.

The ordering logic is `plan.js`, apart from the page; `tests/faces/logic_test.js` runs it
against recorded items (`tests/fixtures/faces/plan.json`, and `cross-ledger.json` for a record
whose steps are in two other ledgers).
