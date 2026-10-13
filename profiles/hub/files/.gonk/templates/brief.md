<!-- An arc's brief: the body of its ledger item. `python3 tools/hub.py arc "<title>"
     --brief <this file, filled in>` files it. One arc touches one repository. -->

Repository: `<name>`. Start from `main`. Part of <the parent item, or "nothing">.

## Why

<What is wrong or missing, and who said so. A finding, a bug report or a measurement is a
claim: say so, and the arc reproduces it before changing anything.>

## The work

1. <A step that can be checked, with the files or names it touches.>
2. <...>

Tests: <what proves it, and what must not change>.

## Rules

- Claim this item before writing; one working tree, one holder (AGENTS.md rules 1 and 2).
- Every change through a pull request with green CI. Merge your own pull request once every
  check has passed; never merge red.
- Do only what this brief asks. Anything else you notice goes in the report, not the change.

## Report

`python3 tools/gonk.py report <item> --evidence <PR URL>`, with a note saying what merged,
what you decided and why, what you skipped and why, and what you noticed that is not this
arc's to fix.
