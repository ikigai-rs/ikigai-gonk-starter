<!--
A starting point, written by ikigai-gonk-starter's tools/new.py. It is true of the library
sample as generated. Grow it with the project, keep these rules, and delete this comment once
the file describes your project rather than the sample:

- Every sentence is TRUE of this repository today, with evidence you can point at. Run every
  check you name, on the main branch, and read its skips and exemptions before you write
  "Known limitations".
- Target WCAG 2.2 Level AA. Status "Evaluation in progress". Never a conformance claim.
- "How it is tested" lists ONLY what was actually run, with its version. While no screen
  reader, magnifier, speech-input or switch-access software has been used, say so in bold.
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

**Nothing in this repository has been tested for accessibility yet**: no automated checker,
no browser, and no screen reader, magnifier, speech-input or switch-access software.

## Known limitations

None recorded yet. That is not the same as none existing; please report what you find.

## Contributing

Read this file before changing anything a person sees. Keep the rules above, and say in the
pull request what was tested, with what, and what was not.

## Reporting a barrier

Open an issue in this repository with the `accessibility` label: what you were doing, what
happened, and your terminal or browser and any assistive technology, if you are comfortable
sharing them. You never need to disclose a disability. Anything security-sensitive goes
through the repository's private channel (on GitHub, private vulnerability reporting), never
a public issue. **We acknowledge a report within 7 days**, and promise nothing further.
