## Periodic audits

Bugs are found by periodic **unled audits**, not by reviewing every commit: one repository, a
list of files, a budget, and nothing to lead the auditor toward an answer.

- **An audit is an item.** `just audit <file>… [--budget N]` (or `python3 tools/audit.py`)
  files one from `.gonk/templates/audit.md`: the repository, the file list, the budget of tool
  calls, and these rules. Whoever claims it is the auditor.
- **Unled.** The auditor gets the file list and the budget, and no earlier findings or hints:
  a lead narrows what gets looked at to what someone already suspected.
- **A reproduction for every bug.** A failing test, or a command and its output. A suspicion
  without one is not reported as a bug.
- **One item per finding**, filed by the auditor and labeled `audit-finding`, so each is
  claimed, fixed and closed on its own. The audit item is reported with `--note` listing the
  items it filed, and closed `--reason audit-no-change` when it found nothing.
