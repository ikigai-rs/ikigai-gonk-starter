# Tokens in a pull request

## Purpose
Make every bearer token a pull request issues or accepts visible in its description, with what
limits it, on the way to the rule that a credential ikigai issues or accepts is holder-bound.

Artifacts: none

Implemented in `tools/checks.py` (`check_tokens`, `CREDENTIAL_SHAPES`), the worker brief
(`tools/worker.py`) and the pull-requests section of `AGENTS.md`.

## Requirements

### Requirement: A Tokens section for a bearer token
A pull request that issues or accepts a bearer token (a cookie, a header value, a code or key in
a URL) MUST carry a `## Tokens` section in its description saying what the token authorizes, why
it cannot be bound to a key its presenter holds, and what limits it (lifetime, single use, hashed
at rest, the transport it is accepted on). One that adds a holder-bound credential SHOULD say so
in one line. One that adds neither needs no section. The worker brief and `AGENTS.md` MUST state
the rule beside the `## Accessibility` rule.

#### Scenario: The worker brief
- **WHEN** a worker builds its brief for an item
- **THEN** rule 5 asks for a `## Tokens` section when the change issues or accepts a bearer token

### Requirement: Credential-shaped lines are advised
`tools/checks.py pr` MUST read the lines the pull request's diff adds, outside the
`credential_ignore` globs (default `*.md`, `*.txt`), and MUST match them against the five shapes
listed once in `CREDENTIAL_SHAPES`: a cookie issued or read, a bearer header, an api-key header,
a credential in a URL, a function defined to mint a token, invite, code, session, secret or key.
When one matches and the description has no `## Tokens` section, it MUST print each such line
(path, shape, text) as advice and pass, recording a warning firing of `pr-tokens-section`; with
`.gonk/checks.toml` `[pr] tokens = "refuse"` it MUST refuse instead. Removed lines are not read.

#### Scenario: A cookie without the section
- **WHEN** a pull request adds `resp.set_cookie("sid", sid)` and its description has no `## Tokens`
- **THEN** the check prints the line as advice and exits 0

#### Scenario: With the section
- **WHEN** the same pull request's description has a `## Tokens` section
- **THEN** the check says nothing about tokens

#### Scenario: A repository that refuses
- **WHEN** `.gonk/checks.toml` sets `tokens = "refuse"` and a credential-shaped line is added without the section
- **THEN** the check refuses, naming the line
