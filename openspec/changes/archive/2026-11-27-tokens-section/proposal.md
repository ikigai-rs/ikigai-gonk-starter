# A pull request that adds a bearer token says what limits it

## Why
Step 2 of rolling out the holder-bound token rule (ledger #4514, ledger #4546). A credential
ikigai issues or accepts is holder-bound; a bearer token works for whoever copied it, so a pull
request that adds one should say what it authorizes, why it cannot be bound and what limits it.
The hub's merge check already prints credential-shaped lines in a pull request with no
`## Tokens` section, as advice, and will later refuse; workers should write the section first.

## What changes
- The worker brief's rule 5, `AGENTS.md` section 4 and `profiles/sections/pull-requests.md`
  state the rule beside `## Accessibility`.
- `tools/checks.py pr` reads the lines the diff adds and advises (or, with `tokens = "refuse"`,
  refuses) a credential-shaped one in a pull request with no `## Tokens` section. The five shapes
  are listed once, in `CREDENTIAL_SHAPES`.
- The harness graph gains `guide:tokens-section` and `ctl:pr-tokens-section` (a warning).

## Impact
Artifacts: none

Touches `tools/checks.py`, `tools/worker.py`, their tests, `AGENTS.md`,
`profiles/sections/pull-requests.md`, `profiles/harness/pull-requests.ttl` and the assembled
harness. Guidance and advice only: no persisted state changes, nothing is refused by default, and
a worker on an older starter keeps working without writing the section.
