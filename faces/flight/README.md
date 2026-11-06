# flight: a plan's steps in landing order

`?ledger=<name>&record=<n>`. The steps are the record item's `item.children` (closed ones too: a
landed step stays in the plan) and the targets of its `related` links, all from the record's
own JSON face; the ledger is never listed to find them. They are listed in the order they can land: topological by `blocks` within the plan,
ties broken by priority (lower first, none last) then number. The first step that is open,
not handed back and blocked by nothing open is marked "Next to land" in words as well as by
its border. Steps caught in a blocking cycle are listed apart, since none of them can land.

The ordering logic is `plan.js`, apart from the page; `tests/faces/logic_test.js` runs it
against a recorded item (`tests/fixtures/faces/plan.json`).
