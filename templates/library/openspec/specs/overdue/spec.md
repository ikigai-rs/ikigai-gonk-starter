# Overdue loans

## Purpose

What happens when a copy is kept past its due date: when a loan counts as overdue, what that
stops the member doing, and what a late return owes.

Artifacts: `service`, `cli`

## Requirements

### Requirement: A loan is overdue from the day after its due date

A loan SHALL be overdue on every day after its due date, and SHALL NOT be overdue on the due
date itself.

#### Scenario: On its due date a loan is not overdue

- **WHEN** a member lists their loans on a loan's due date
- **THEN** the loan is not overdue

#### Scenario: The day after its due date a loan is overdue

- **WHEN** a member lists their loans the day after a loan's due date
- **THEN** the loan is overdue by one day

### Requirement: A member with an overdue loan cannot borrow

The library MUST refuse a new loan to a member who has an overdue loan.

#### Scenario: Borrowing while a loan is overdue is refused

- **WHEN** a member with an overdue loan asks for another copy
- **THEN** it is refused as `member-has-overdue`

### Requirement: A late return owes a fee

A return after the due date SHALL owe 25 cents for each day late.

#### Scenario: Returning on the due date owes nothing

- **WHEN** a copy is returned on its due date
- **THEN** the return is zero days late and owes 0 cents

#### Scenario: Returning late owes 25 cents a day

- **WHEN** a copy due on 2026-10-22 is returned on 2026-10-25
- **THEN** the return is three days late and owes 75 cents

### Requirement: Overdue loans are listed

The library SHALL list every overdue loan, the latest first, then by copy id, with its
member, due date and days late.

#### Scenario: Listing overdue loans

- **WHEN** two loans are overdue by different numbers of days and a third is not overdue
- **THEN** the two overdue loans are listed, the one more days late first
