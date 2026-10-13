Repository: `{repo}`. Budget: {budget} tool calls. Files, and nothing else:

{files}

An **unled audit**: read the files above and find the bugs in them. No earlier findings, no
hints and no suspicions come with it, on purpose: a lead narrows what gets looked at to what
someone already suspected.

- **A reproduction for every bug**: a failing test, or a command and its output. A suspicion
  without one is not reported as a bug.
- **One item per finding**: `python3 tools/gonk.py file "<title>" --labels audit-finding
  --body "<the reproduction, the file and the line>"`, so each is claimed, fixed and closed on
  its own.
- **Stop at the budget.** Say where you stopped and what you did not read.
- **Report** with `--note` listing the items you filed; close `--reason audit-no-change` when
  you found nothing.
