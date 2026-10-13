# .gonk/

How this repository is wired to gonk, the ledger and review host it works with.

| file | committed | holds |
| --- | --- | --- |
| `layout.toml` | yes | where specs (`[[spec]]`), artifacts, queries and scripts live |
| `gonk.toml` | yes | the ledger this repository's work lives in |
| `process.toml` | yes | the process this repository works by: its profiles, what each asks for, its roles; written by `tools/new.py`, read by `init` and `status` |
| `local.toml` | no (git-ignored) | how THIS working tree reaches gonk (a socket, or QUIC with a client bundle) and the holder name it claims under |
| `state/claim.toml` | no (git-ignored) | the claim this working tree holds; gonk is the truth, this is the note |
| `queries/` | yes | saved SPARQL over the ledger and the spec graph |
| `scripts/` | yes | workflow scripts |

`python3 tools/gonk.py init` (or `just init`) writes `gonk.toml` and `local.toml` from its
flags and prints what is still missing. Nothing is read from environment variables.
