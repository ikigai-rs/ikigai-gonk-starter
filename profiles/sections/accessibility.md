## 7. Read ACCESSIBILITY.md before changing what people see

Read `ACCESSIBILITY.md` before any change to a user interface or to rendered output: a page,
a view, terminal text, generated markup or a picture. Keep the rules it states. In every PR
and `ACCESSIBILITY.md`, give each named accessibility check a result: Pass, Fail or Blocked,
with evidence (the test or command, tool and version used, or why it could not run). Keep
unrun checks as Blocked; a screen reader nobody ran is Blocked, never "not applicable".
For changes to what people see, the PR description has a `## Accessibility` section with a
Check | Result | Evidence table, one row per check. Never claim testing that did not happen,
by a person or a tool, and never weaken a check to make a change pass.
