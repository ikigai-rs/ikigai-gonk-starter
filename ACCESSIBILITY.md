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
- **Faces** (`faces/`): a template and four sample web pages (`board`, `flight`, `project`,
  `claims`) that gonk serves as a deployment's own pages. They are HTML, CSS and JavaScript,
  copied into every generated repository.
- No other web page, HTML or picture.

Not this repository's: the ledger and review pages a generated repository works with are
[ikigai-gonk's](https://github.com/ikigai-rs/ikigai-gonk/blob/main/ACCESSIBILITY.md), and each
agent's own interface is its maker's.

## The rules that output follows

- Meaning is in words, never in color or layout alone. A refusal is a sentence that says
  what was refused and why.
- Every repository `tools/new.py` generates, for every target and every process profile,
  carries `ACCESSIBILITY.md` and the `AGENTS.md` rule that says to read it before any change to
  what people see.

- **Faces**: real headings (one `h1`, then `h2`/`h3` per section); every control has a visible
  `<label>`; status is announced politely through one `role="status" aria-live="polite"`
  region, never by moving focus; state is said in words ("lease expired", "Next to land"),
  never by color alone; progress is a `<progress>` with its count in text; light and dark
  follow the system setting, with text colors chosen for 4.5:1; a wide table scrolls in its
  own box so the page does not scroll sideways at 320 px.
  The board's column colors are decoration beside each column's written name, chosen for 3:1
  against the page and the column in light and dark. Its picker is a labeled `<select>`: picking
  an option redraws the board without moving focus and says so in the status region, and the
  number input beside it reaches any item.

## How it is tested

- `tests/test_new.py` and `tests/test_profiles.py` check that every generated repository, for
  each target and each profile, carries that `ACCESSIBILITY.md`, with its target and no
  conformance claim, and the `AGENTS.md` rule. That is a check that the file is there, not of
  accessibility.
- `tests/test_faces.py` checks every face's HTML for what the door's CSP refuses (inline
  script, `<style>`, `style=` and `on…=` attributes), that each page has `lang`, an `h1` and
  the polite status region, and that only servable extensions are present. That is a static
  check of the markup, not of accessibility. It also computes the contrast of the board's
  column colors from `app.css`. Its Deno test (`tests/faces/logic_test.js`)
  checks which column or step each recorded item lands in, and the words that say how many
  members could not be read and why; it renders nothing.
- The `board` face was driven once in headless Chromium (2026-11-08, by script, against a stand-in
  for gonk answering under the door's CSP): tab order, no CSP violation, no sideways scroll at
  320 px with long titles, and the column borders' computed colors in light and dark. That is
  not a person's keyboard pass and CI does not repeat it. The other faces have NOT been opened
  in a browser by this repository, and no axe run has been done on any; `faces/README.md` lists
  those checks for whoever deploys one.
- No automated accessibility checker and no assistive technology has been used on any of this
  output, and no browser on any of it but that one pass. **No screen reader, screen magnifier, speech-input or switch-access
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
