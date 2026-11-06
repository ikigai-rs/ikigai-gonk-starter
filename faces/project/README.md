# project: open items across a project's ledgers

`projects.json` names each project and its ledgers (the names in it are examples; put yours).
`?project=<name>&labels=a,b&without=c` shows the open items carrying every label in `labels`
and none in `without`, with counts by ledger, priority and label. The address is the view, so
a bookmark is a saved view.

It reads one listing per ledger, a few at a time. ikigai-ledger's `ledgers=` union (ledger
PR 88) answers every ledger in one read; once it is released, it replaces that loop.
