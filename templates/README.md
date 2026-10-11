# templates/

What `tools/new.py` (`just new <dir> --target python`) generates a repository from, in three
layers, a later one replacing a file an earlier one wrote:

```text
(this starter's own files)   AGENTS.md, tools/gonk.py, .gonk/, hooks/, the openspec/ and
                             artifacts/ READMEs, and the justfile's gonk recipes
library/                     the sample's language-neutral half: the specs and the changes in
                             flight, openspec/vectors.json, the saved queries and the workflow
                             script, the README, and the check that specs and vectors agree
<target>/                    one language's artifacts and tests (python/ today), its CI, and
                             its recipes (justfile.recipes, appended to the starter's)
```

The sample is a **library-lending** system: a catalog of titles and copies, members, loans
with due dates, renewals, overdue rules and late fees, with time as an input. A new target
(Deno, Rust and Go are planned) is a new `<target>/` directory whose tests run the SAME
`library/openspec/vectors.json`, so no target can drift from the spec. CI generates every
target into a scratch directory and runs its tests, so a template that has gone stale fails.
