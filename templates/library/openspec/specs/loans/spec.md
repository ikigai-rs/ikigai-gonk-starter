# Loans

## Purpose

Lending a copy to a member, and taking it back. Every date here is the date of the request as
the service's own clock gives it; a client never supplies the date.

Artifacts: `service`, `cli`

## Requirements

### Requirement: A loan is due three weeks after it is made

A loan SHALL be due 21 days after the date it is made.

#### Scenario: Lending an available copy

- **WHEN** a member borrows an available copy on 2026-10-01
- **THEN** the loan is due on 2026-10-22, with no renewals
- **AND** the copy is on loan to that member

### Requirement: A copy is lent to one member at a time

The library MUST refuse to lend a copy that is already on loan.

#### Scenario: Lending a copy that is out is refused

- **WHEN** a member asks for a copy another member has borrowed
- **THEN** it is refused as `copy-on-loan`

### Requirement: Only a registered member borrows a known copy

The library MUST refuse a loan to an id that is not a registered member, and a loan of a copy
id that does not exist.

#### Scenario: An unknown member is refused

- **WHEN** a loan is asked for by an id that is not registered
- **THEN** it is refused as `unknown-member`

#### Scenario: An unknown copy is refused

- **WHEN** a loan is asked for of a copy id that does not exist
- **THEN** it is refused as `unknown-copy`

### Requirement: A member has at most three loans

A member SHALL hold at most three loans at once, and the library MUST refuse a fourth.

#### Scenario: A fourth loan is refused

- **WHEN** a member with three loans asks for another copy
- **THEN** it is refused as `loan-limit`

### Requirement: A returned copy can be lent again

Returning a copy SHALL end its loan, and the copy SHALL be available to anyone. The library
MUST refuse to take back a copy that is not on loan.

#### Scenario: Returning a copy

- **WHEN** a member returns a copy on or before its due date
- **THEN** the return owes nothing
- **AND** another member can borrow the copy

#### Scenario: Returning a copy that is not out is refused

- **WHEN** a copy that is not on loan is returned
- **THEN** it is refused as `not-on-loan`

### Requirement: A member's loans are listed

The library SHALL list a member's loans in order of due date, then copy id, with each loan's
title, due date, renewals and whether it is overdue.

#### Scenario: Listing a member's loans

- **WHEN** a member with two loans, due on different dates, asks for their loans
- **THEN** both are listed, the one due first first
