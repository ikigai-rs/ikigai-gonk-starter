# Working in this repository

These rules are for every agent and every person working here. They are short on purpose:
each one exists because work went wrong without it. `CLAUDE.md` imports this file (its one
line is `@AGENTS.md`), so every agent reads the same rules.

The work lives in a **ledger** on gonk: items you file, claim, comment on and close, and
`next`, which answers *what should I do next* with reasons. What the software is supposed
to do lives in `openspec/`. The code lives in `artifacts/<name>/`, one directory per
deployable thing. `.gonk/` says how this repository reaches gonk and which ledger is its.

These are the rules of the process this repository works by: `.gonk/process.toml` names it,
and `python3 tools/gonk.py status` prints it. A rule that process does not use is not here.
