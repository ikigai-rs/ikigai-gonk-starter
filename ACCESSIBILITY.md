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
| Last reviewed | 2026-11-12 |

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
  never by color alone; progress is a `<progress>` with its count in text; a wide table scrolls
  in its own box so the page does not scroll sideways at 320 px, and the project view's table
  becomes a list of labeled cards at 600 px and narrower, its narrow columns never breaking
  inside a word. A count is a chip: its label, then its number in an element of its own.
  `app.css` carries gonk's light, dark and high-contrast palettes under `[data-theme=…]`, the
  attribute gonk's own script sets on a face's page, and follows `prefers-color-scheme` and
  `prefers-contrast: more` when the attribute is absent or `auto`. Every color is a token: text
  meets 4.5:1 on its backgrounds (7:1 in high contrast), control borders, the focus ring and
  column colors 3:1. A face offers no theme chooser, breadcrumb or link back to gonk of its
  own: gonk draws that frame around a face (ledger item 3279).
  The board's column colors are decoration beside each column's written name, chosen for 3:1
  against the page and the column in every theme. Its picker is a labeled `<select>`: picking
  an option redraws the board without moving focus and says so in the status region, and the
  number input beside it reaches any item.

## How it is tested

Every named check has a result: Pass, Fail or Blocked, with evidence. A Pass covers only
the stated check, not overall accessibility. Historical browser runs below were recorded on
2026-11-08; their Chromium version and invocation were not recorded, and CI does not repeat
them. They remain context, not current verification.

| Check | Result | Evidence |
|---|---|---|
| Generated accessibility record and agent rule, across targets and profiles | Pass | `python3 -m unittest discover -s tests -p test_new.py` and `python3 -m unittest discover -s tests -p test_profiles.py`, Python 3.12.3, 2026-11-12. Checks presence, reporting instructions and generated text; not an accessibility evaluation. |
| Face markup and theme contrast | Pass | `python3 -m unittest discover -s tests -p test_faces.py`, Python 3.12.3, 2026-11-12. Static checks of CSP restrictions, language, heading, polite status region, servable extensions, token contrast and automatic theme fallback; does not render pages. |
| Face logic | Pass | `python3 -m unittest discover -s tests -p test_faces.py` runs `deno test --no-config --no-lock --quiet tests/faces/`, Deno 2.9.7, 2026-11-12. Checks column/step membership, unread-member messages and count-chip order; renders nothing. |
| Board browser tab order, CSP, 320 px overflow and column colors | Blocked | No current browser run. Historical scripted headless Chromium run on 2026-11-08 used a stand-in for gonk under its CSP, with long titles in light and dark. Browser version and command were not recorded; no person's keyboard pass. |
| All faces browser CSP and overflow; project layout and themes | Blocked | No current browser run. Historical scripted headless Chromium run on 2026-11-08 covered all five faces at 320 and 687 px in dark; project measurements and screenshots at 375, 687 and 1280 px covered light, dark, high contrast and emulated automatic theme preferences. Browser version and command were not recorded; no keyboard pass. |
| Manual keyboard | Blocked | No person has performed a keyboard pass; no interactive browser session was available for this update. |
| Automated accessibility checker (axe) | Blocked | No axe or other automated accessibility checker has been run on this output; no browser checker was configured for this update. `faces/README.md` lists deployment checks. |
| Screen reader | Blocked | No screen reader has been used on this output; no screen reader session was available for this update. |
| Screen magnifier | Blocked | No screen magnifier has been used on this output; no magnifier session was available for this update. |
| Speech input | Blocked | No speech-input software has been used on this output; no speech-input session was available for this update. |
| Switch access | Blocked | No switch-access software has been used on this output; no switch-access session was available for this update. |
| Braille display | Blocked | No braille display has been used on this output; no braille display was available for this update. |

## Known limitations

Found by reading the files on 2026-10-15, not by testing:

- Several READMEs lay out two columns with spaces inside a `text` code block (this README's
  *What is in it*, `profiles/README.md`, `templates/README.md`), so a screen reader reads each
  row as one run of preformatted text rather than as a table (SC 1.3.1).
- Nothing above has been tried with a screen reader, magnifier or braille display.

## Contributing

Read this file before changing anything listed above. Keep the rules above. In the pull
request's `## Accessibility` section, give each check a row in a Check | Result | Evidence
table: Pass, Fail or Blocked, with the test or command, tool and version used, or why it could
not run. Keep unrun checks as Blocked, including a screen reader nobody ran; never call it
"not applicable".

## Reporting a barrier

[Open an issue](https://github.com/ikigai-rs/ikigai-gonk-starter/issues/new) with the
`accessibility` label: what you were doing, what happened, and your terminal or browser and any
assistive technology, if you are comfortable sharing them. You never need to disclose a
disability. Anything security-sensitive goes through
[private vulnerability reporting](https://github.com/ikigai-rs/ikigai-gonk-starter/security/advisories/new).
**We acknowledge a report within 7 days**, and promise nothing further.
