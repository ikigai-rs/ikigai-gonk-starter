## ADDED Requirements

### Requirement: Share workshop tasks across ledgers
The workshop SHALL provide a shared task tuplespace and results channel with separate grants, a seed and a reset, and an example of taking one task and reporting its result.

#### Scenario: Competing participants
- **WHEN** two participants take the same pending task
- **THEN** the host grants one take and records a receipt, and the other participant is told it is gone

#### Scenario: Reset
- **WHEN** the facilitator resets the exercise
- **THEN** pending tasks are removed and receipts and results remain as history
