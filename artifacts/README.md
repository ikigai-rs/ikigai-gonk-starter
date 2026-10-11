# artifacts/

One directory per **deployable thing**, any language each:

```text
artifacts/<name>/     a service, a command-line client, a library, a site…
```

`<name>` is lowercase letters, digits, `-` and `_`, so it can appear in a spec, a ledger
label and a path without quoting. Each artifact keeps its own README, build and tests; a
repository with a Python service and a Rust client is two directories here, not a mixed tree.

**Specs name the artifacts they touch.** A spec's `## Purpose` and a change's `## Impact`
carry one line, `Artifacts: `service`, `cli``, and `.gonk/layout.toml` declares both the
root (`[artifacts] root`) and that marker (`[artifacts] marker`). `python3 tools/gonk.py
init` checks that every name on such a line has a directory here.

Empty in the starter; the library-lending sample fills it with `service` and `cli`.
