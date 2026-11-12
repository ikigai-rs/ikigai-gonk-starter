<!--
A starting point, written by ikigai-gonk-starter's tools/new.py. It is true of the library
sample as generated. Grow it with the project, keep these rules, and delete this comment once
the file describes your project rather than the sample:

- Every sentence is TRUE of this repository today, with evidence you can point at. Run every
  check you can, on the main branch, and read its skips and exemptions before you write
  "Known limitations". Record checks you could not run as Blocked with the reason.
- Target WCAG 2.2 Level AA. Status "Evaluation in progress". Never a conformance claim.
- "How it is tested" uses a Check | Result | Evidence table: every named check has a result
  of Pass, Fail or Blocked, with the test or command, tool and version used, or why it could
  not run. Keep unrun checks as Blocked, never omit them. A screen reader nobody ran is
  Blocked, never "not applicable". Never claim testing that did not happen.
- The owner is "the maintainers of this repository". Name no person.
- Reporting: an issue with the `accessibility` label; anything security-sensitive through a
  private channel (on GitHub, private vulnerability reporting), never a public issue.
  Acknowledge a report within 7 days, and promise nothing further.
- When the project gains something people look at (a page, a view, generated markup or
  pictures), this file grows to cover it: what is in scope, each check and what it does not
  cover, the browsers and assistive technology actually used, the known limitations, and how
  severity is judged. Drop a section that does not apply rather than leave a placeholder.
- Link this file from the README, and keep the AGENTS.md rule that points here.
-->
# Accessibility

What this project shows people, the rules that output follows, how it is tested, and how to
report a barrier. It is not a conformance claim.

| Field | Value |
|---|---|
| Target | WCAG 2.2 Level AA, for anything this project shows people |
| Status | Evaluation in progress. No conformance claim. |
| Owner | the maintainers of this repository |
| Report a barrier | an issue in this repository, with the `accessibility` label |

## What it shows people

- **The command-line client** (`artifacts/cli/`): plain lines of text on the terminal. No
  color, no full-screen redraw, no prompt that waits for a key.
- **The service** (`artifacts/service/`) answers JSON. Programs read it, not people.
- Nothing else: no web page, no HTML, no picture.

## The rules that output follows

- Meaning is in words, never in color or layout alone.
- An error is one line on standard error that starts with the program's name (`library:`) and
  says what was refused and why, and the exit status says the same thing to a script.
- Answers go to standard output and errors to standard error, so a person can read either one
  alone.

## How it is tested

Every named check has a result: Pass, Fail or Blocked, with evidence. No accessibility
checks have been run on the generated sample yet.

| Check | Result | Evidence |
|---|---|---|
| Automated accessibility checker | Blocked | No checker has been run on the generated sample yet. |
| Browser | Blocked | No browser check has been run on the generated sample yet. |
| Screen reader | Blocked | No screen reader has been run on the generated sample yet. |
| Screen magnifier | Blocked | No magnifier has been run on the generated sample yet. |
| Speech input | Blocked | No speech-input software has been run on the generated sample yet. |
| Switch access | Blocked | No switch-access software has been run on the generated sample yet. |

## Known limitations

None recorded yet. That is not the same as none existing; please report what you find.

## Contributing

Read this file before changing anything a person sees. Keep the rules above. In the
pull request's `## Accessibility` section, give each check a row in a Check | Result | Evidence
table: Pass, Fail or Blocked, with the test or command, tool and version used, or why it could
not run. Keep unrun checks as Blocked, including a screen reader nobody ran; never call it
"not applicable".

## Reporting a barrier

Open an issue in this repository with the `accessibility` label: what you were doing, what
happened, and your terminal or browser and any assistive technology, if you are comfortable
sharing them. You never need to disclose a disability. Anything security-sensitive goes
through the repository's private channel (on GitHub, private vulnerability reporting), never
a public issue. **We acknowledge a report within 7 days**, and promise nothing further.
