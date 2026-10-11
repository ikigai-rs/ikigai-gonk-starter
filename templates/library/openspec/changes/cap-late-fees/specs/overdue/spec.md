## MODIFIED Requirements

### Requirement: A late return owes a fee

A return after the due date SHALL owe 25 cents for each day late, and MUST NOT owe more than
1000 cents (ten dollars) for one loan.

#### Scenario: Returning on the due date owes nothing

- **WHEN** a copy is returned on its due date
- **THEN** the return is zero days late and owes 0 cents

#### Scenario: Returning late owes 25 cents a day

- **WHEN** a copy due on 2026-10-22 is returned on 2026-10-25
- **THEN** the return is three days late and owes 75 cents

#### Scenario: Returning very late owes at most ten dollars

- **WHEN** a copy due on 2026-10-22 is returned on 2027-01-31
- **THEN** the return is 101 days late and owes 1000 cents
