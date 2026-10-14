# .gonk/

How this repository is wired to gonk, the ledger and review host it works with.

| file | committed | holds |
| --- | --- | --- |
| `layout.toml` | yes | where specs (`[[spec]]`), artifacts, queries and scripts live |
| `gonk.toml` | yes | the ledger this repository's work lives in |
| `process.toml` | yes | the process this repository works by: its profiles, what each asks for, its roles; written by `tools/new.py`, read by `init` and `status` |
| `harness/harness.ttl` | yes | the process's harness: its rules (guides) and the places its tools enforce them (sensors), assembled by `tools/new.py` like `AGENTS.md`; `tools/harness.py check` holds it to the code |
| `harness/vocabulary.ttl` | yes | the terms it is written in (`https://ikigai-rs.dev/ns/harness#`) |
| `local.toml` | no (git-ignored) | how THIS working tree reaches gonk (a socket, or QUIC with a client bundle) and the holder name it claims under |
| `state/claim.toml` | no (git-ignored) | the claim this working tree holds; gonk is the truth, this is the note |
| `state/harness/firings.log` | no (git-ignored) | one line each time a sensor here refused or warned: an ikigai-log segment, loaded into gonk by `tools/gonk.py harness` |
| `queries/` | yes | saved SPARQL over the ledger and the spec graph, and the four harness questions (`harness-*.rq`) |
| `scripts/` | yes | workflow scripts |

`python3 tools/gonk.py init` (or `just init`) writes `gonk.toml` and `local.toml` from its
flags and prints what is still missing. Nothing is read from environment variables.
