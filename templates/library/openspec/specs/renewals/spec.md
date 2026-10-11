# Renewals

## Purpose

Keeping a copy longer without returning it.

Artifacts: `service`, `cli`

## Requirements

### Requirement: A renewal extends a loan from its due date

A renewal SHALL move a loan's due date 21 days past its current due date, whatever day the
renewal is asked for, and SHALL count the renewal.

#### Scenario: Renewing a loan

- **WHEN** a loan due on 2026-10-22 is renewed on 2026-10-20
- **THEN** it is due on 2026-11-12, with one renewal

### Requirement: A loan is renewed at most twice

The library MUST refuse a third renewal of the same loan.

#### Scenario: A third renewal is refused

- **WHEN** a loan that has been renewed twice is renewed again
- **THEN** it is refused as `renewal-limit`

### Requirement: An overdue loan is not renewed

The library MUST refuse to renew a loan after its due date; on the due date itself the loan
MAY be renewed.

#### Scenario: Renewing on the due date

- **WHEN** a loan is renewed on its due date
- **THEN** it is renewed

#### Scenario: Renewing after the due date is refused

- **WHEN** a loan is renewed the day after its due date
- **THEN** it is refused as `loan-overdue`
