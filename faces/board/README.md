# board: an epic's children in five columns

`?ledger=<name>&epic=<n>`. The children are what the epic links to as `child`, and what links
to it as `parent`. Columns: **blocked** (an open sibling `blocks` it), **ready**, **in flight**
(claimed; an expired lease is said in words beside the holder), **awaiting** (handed back,
`awaiting: dispatcher`) and **done** (closed). A `<progress>` bar counts done of all, and a
label tally links each label to gonk's filter, `/l/{ledger}?labels=`.
