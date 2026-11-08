# board: an epic's children in five columns

The address is the state, so a bookmark is a saved board:

| Parameter | Meaning |
| --- | --- |
| `ledger` | The ledger of the item shown and of the picker's list. Default `default`. |
| `kinds` | The labels the picker offers, a comma list such as `epic,feature,story`. Each must match the ledger's label grammar, `[A-Za-z0-9._:-]+`; others are ignored. Default `epic`. |
| `epic` | The number of the item shown; any item, whatever its labels. Absent, the board shows the first item the picker offers. |

The **picker** lists the open items in the ledger carrying any of the kinds, read with the
viewer's own access: one bounded page of 50 per kind, merged by number, an item carrying two
kinds offered once under the first listed, ordered by kind as listed and then by number. With
more than one kind each option names its kind. A kind with more open items than the page, or a
kind that could not be listed, is said in the status; the number input reaches every other
item. The list only chooses which item to show, so it is the one listing this face reads. The
kinds are left out of the label tally.

The children are the epic's own `item.children` (its JSON face,
closed children included), each read once; the ledger is never listed to find them, since a
listing answers open items only. `?ledger=` names the EPIC's ledger; each child is read by its
IRI from its own ledger and shown as `{ledger}#{number}`, and siblings are keyed by IRI, so two
children with the same number in different ledgers are told apart. Columns: **blocked** (an open sibling `blocks` it), **ready**, **in flight**
(claimed; an expired lease is said in words beside the holder), **awaiting** (handed back:
any non-null `awaiting`) and **done** (closed). A `<progress>` bar counts done of all, and a
label tally links each label to gonk's filter, `/l/{ledger}?labels=`. A child that could not
be read (no read grant for its ledger, or not found) is in no column and not in the count, but
is said under the heading and in the status announcement, counted by reason, with only its IRI
shown.

The column logic is `board.js`, apart from the page; `tests/faces/logic_test.js` runs it
against recorded items (`tests/fixtures/faces/plan.json` and `cross-ledger.json`).
