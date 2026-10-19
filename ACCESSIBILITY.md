# Accessibility

What `ikigai-gonk-starter` shows people and what it generates, the rules that output follows,
how it is tested, and how to report a barrier. It is not a conformance claim. Adapted from the
[ACCESSIBILITY.md template](https://github.com/mgifford/ACCESSIBILITY.md) (MIT).

| Field | Value |
|---|---|
| Target | WCAG 2.2 Level AA, for what this repository shows people and what it generates |
| Status | Evaluation in progress. No conformance claim. |
| Owner | the maintainers of this repository |
| Report a barrier | [a GitHub issue](https://github.com/ikigai-rs/ikigai-gonk-starter/issues/new) with the `accessibility` label; anything security-sensitive through [private vulnerability reporting](https://github.com/ikigai-rs/ikigai-gonk-starter/security/advisories/new) |
| Last reviewed | 2026-10-15 |

## What it shows people

- **Documentation**: this README, `AGENTS.md`, the READMEs under `profiles/`, `templates/`,
  `worker/` and `.gonk/`, and `docs/windows.md`: Markdown, read on GitHub or in an editor.
- **Terminal text** from `tools/gonk.py`, `tools/new.py`, `tools/harness.py`, `tools/worker.py`
  and `scripts/gates.sh`: plain lines, with no color and no full-screen redraw.
- **What it generates.** `tools/new.py` writes a repository holding the library-lending sample,
  whose client prints plain text and whose service answers JSON, and gives that repository an
  `ACCESSIBILITY.md` of its own to grow (`templates/library/ACCESSIBILITY.md`) and an
  `AGENTS.md` rule that points every agent at it.
- No web page, no HTML and no picture.

Not this repository's: the ledger and review pages a generated repository works with are
[ikigai-gonk's](https://github.com/ikigai-rs/ikigai-gonk/blob/main/ACCESSIBILITY.md), and each
agent's own interface is its maker's.

## The rules that output follows

- Meaning is in words, never in color or layout alone. A refusal is a sentence that says
  what was refused and why.
- Every repository `tools/new.py` generates, for every target and every process profile,
  carries `ACCESSIBILITY.md` and the `AGENTS.md` rule that says to read it before any change to
  what people see.

## How it is tested

- `tests/test_new.py` and `tests/test_profiles.py` check that every generated repository, for
  each target and each profile, carries that `ACCESSIBILITY.md`, with its target and no
  conformance claim, and the `AGENTS.md` rule. That is a check that the file is there, not of
  accessibility.
- No automated accessibility checker, no browser and no assistive technology has been used on
  any of this output. **No screen reader, screen magnifier, speech-input or switch-access
  software has been used to test this repository.**

## Known limitations

Found by reading the files on 2026-10-15, not by testing:

- Several READMEs lay out two columns with spaces inside a `text` code block (this README's
  *What is in it*, `profiles/README.md`, `templates/README.md`), so a screen reader reads each
  row as one run of preformatted text rather than as a table (SC 1.3.1).
- Nothing above has been tried with a screen reader, magnifier or braille display.

## Contributing

Read this file before changing anything listed above. Keep the rules above, and say in the pull
request what was tested, with what, and what was not.

## Reporting a barrier

[Open an issue](https://github.com/ikigai-rs/ikigai-gonk-starter/issues/new) with the
`accessibility` label: what you were doing, what happened, and your terminal or browser and any
assistive technology, if you are comfortable sharing them. You never need to disclose a
disability. Anything security-sensitive goes through
[private vulnerability reporting](https://github.com/ikigai-rs/ikigai-gonk-starter/security/advisories/new).
**We acknowledge a report within 7 days**, and promise nothing further.
