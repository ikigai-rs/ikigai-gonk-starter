# Pull request aliases

## Purpose
Name closed items by final PR evidence, using the trusted dispatcher identity while
keeping unattended worker grants narrow.

Artifacts: none

Implemented by repository tooling (`tools/`). Requires ikigai-ledger 0.18.0 or later
and operator enrollment of the dispatcher identity.

## Requirements

### Requirement: Pull request aliases
After a successful close, the dispatcher MUST attach every GitHub pull request URL in
the close evidence as `gh:owner/repo#number` to that item. It MUST register the gh scheme
as its authenticated principal on first use. Registration and alias refusals MUST be
reported in plain close output and MUST NOT fail an otherwise successful close.
It MUST NOT delete or reassign aliases; retries of the same mapping MUST be safe.
Workers MUST NOT register schemes, write aliases, or gain alias grants through enrollment.

#### Scenario: Close names its item
- **WHEN** the dispatcher closes an item with one or more GitHub PR URLs in the evidence
- **THEN** it writes each percent-encoded gh alias with the qualified ledger item as content
- **AND** non-PR evidence is retained without creating aliases

#### Scenario: Refused alias
- **WHEN** an alias belongs to another item or the dispatcher lacks a grant
- **THEN** the close still succeeds and reports the refusal
- **AND** the original alias mapping remains

#### Scenario: Worker hand-back
- **WHEN** a worker discovers a PR and hands back its evidence
- **THEN** it neither registers gh nor writes an alias
- **AND** enrollment output contains no alias grant or alias option

### Requirement: CLI item references
Every CLI item argument MUST accept `gh:owner/repo#number` as well as an integer.
Resolution MUST use the configured ledger and return its numeric item identity before
claim comparisons or mutations. Unknown, foreign, denied or malformed aliases MUST fail
without an item mutation.

#### Scenario: A PR resolves to an item
- **WHEN** the dispatcher uses a closed item's gh alias in an item command
- **THEN** the action addresses its original ledger item

#### Scenario: A foreign alias
- **WHEN** a PR alias belongs to a different ledger
- **THEN** the action fails without addressing the same number in the configured ledger
