## ADDED Requirements

### Requirement: Pull request aliases
A worker MUST attach each discovered PR as `gh:owner/repo#number` to its claimed item
before posting hand-back evidence. It MUST preserve server refusals in the hand-back and
MUST NOT delete or reassign an alias. Retrying the same mapping MUST be safe; the ledger
MUST refuse attaching a previously held alias to a second item.

#### Scenario: Hand-back names its item
- **WHEN** a worker discovers a PR on its branch
- **THEN** it writes the percent-encoded gh alias with the qualified ledger item as content
- **AND** records success or refusal with its PR evidence

### Requirement: CLI item references
Every CLI item argument MUST accept `gh:owner/repo#number` as well as an integer.
Resolution MUST use the configured ledger and return its numeric item identity before
claim comparisons or mutations. Unknown, foreign, denied or malformed aliases MUST fail
without an item mutation.

#### Scenario: A PR resolves to an item
- **WHEN** the dispatcher uses a hand-back's gh alias in an item command
- **THEN** the action addresses its original ledger item

#### Scenario: A duplicate alias
- **WHEN** another item tries to take the same alias
- **THEN** the ledger refuses and the original mapping remains
