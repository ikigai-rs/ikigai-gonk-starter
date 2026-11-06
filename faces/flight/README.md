# flight: a plan's steps in landing order

`?ledger=<name>&record=<n>`. The steps are the record item's children and its `related`
items. They are listed in the order they can land: topological by `blocks` within the plan,
ties broken by priority (lower first, none last) then number. The first step that is open,
not handed back and blocked by nothing open is marked "Next to land" in words as well as by
its border. Steps caught in a blocking cycle are listed apart, since none of them can land.
