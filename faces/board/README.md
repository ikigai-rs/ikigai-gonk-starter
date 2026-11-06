# board: an epic's children in five columns

`?ledger=<name>&epic=<n>`. The children are the epic's own `item.children` (its JSON face,
closed children included), each read once; the ledger is never listed to find them, since a
listing answers open items only. Columns: **blocked** (an open sibling `blocks` it), **ready**, **in flight**
(claimed; an expired lease is said in words beside the holder), **awaiting** (handed back:
any non-null `awaiting`) and **done** (closed). A `<progress>` bar counts done of all, and a
label tally links each label to gonk's filter, `/l/{ledger}?labels=`.

The column logic is `board.js`, apart from the page; `tests/faces/logic_test.js` runs it
against a recorded item (`tests/fixtures/faces/plan.json`).
