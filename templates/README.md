# templates/

What `tools/new.py` (`just new <dir> --target python`) generates a repository from, in four
layers, a later one replacing a file an earlier one wrote:

```text
(this starter's own files)   tools/gonk.py, .gonk/, hooks/claude/, the openspec/ and
                             artifacts/ READMEs, and the justfile's gonk recipes
(the chosen profiles)        what each process profile adds (../profiles/README.md): its
                             files and recipes; AGENTS.md is assembled from their sections
library/                     the sample's language-neutral half: the specs and the changes in
                             flight, openspec/vectors.json, the saved queries and the workflow
                             script, the README, ACCESSIBILITY.md (a starting file the new
                             project grows), and the check that specs and vectors agree
<target>/                    one language's artifacts and tests (python/, rust/, deno/ and go/ today), its
                             CI, its .gitignore, and its recipes (justfile.recipes, appended
                             to the starter's)
```

A target is a `<target>/` directory beside `library/`, and `tools/new.py` finds it there: it
offers every directory under `templates/` except `library/` as a `--target`, so a new one needs
no change to the generator. What it must supply is the sample's language-specific half — the
two artifacts, their tests running the SAME `library/openspec/vectors.json`, a `scripts/test.py`
that runs them, `.github/workflows/ci.yml`, a `README.md`, its own `.gitignore` lines, and
`justfile.recipes`. The sample is a **library-lending** system: a catalog of titles and copies,
members, loans with due dates, renewals, overdue rules and late fees, with time as an input, so
no target can drift from the spec. CI generates every target into a scratch directory and runs
its tests, so a template that has gone stale fails.

`python/` is the first target: standard-library Python 3.11 or later, nothing to install.
`rust/` is the second: one cargo workspace, edition 2021, on the standard library alone, so
`cargo test` needs no network and no lock file to resolve. `deno/` is the third: TypeScript
on Deno's standard library alone, `Deno.serve` and `fetch` for the service and the client,
every import a relative path, so `deno test` needs no network and no lock file either. `go/` is
the fourth: one Go module on the standard library alone (`net/http`, `encoding/json`), no
requirements and so no `go.sum`, its tests the same vectors against the rules and over HTTP.
