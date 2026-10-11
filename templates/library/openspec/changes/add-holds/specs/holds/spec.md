## ADDED Requirements

### Requirement: A member may hold a title with no copy on the shelf

The library SHALL accept a hold on a title from a registered member when every copy of that
title is on loan, and MUST refuse a hold when a copy is available to borrow.

#### Scenario: Holding a title whose copies are all out

- **WHEN** a member holds a title whose only copy is on loan to someone else
- **THEN** the hold is accepted, first in line

#### Scenario: Holding a title with a copy on the shelf is refused

- **WHEN** a member holds a title that has an available copy
- **THEN** it is refused as `copy-available`

### Requirement: A returned copy of a held title waits for the first holder

When a copy of a held title is returned, the library SHALL keep it for the member first in
line, and MUST refuse to lend it to anyone else.

#### Scenario: The returned copy is kept for the first holder

- **WHEN** a copy of a held title is returned
- **THEN** the copy is kept for the first holder, who can borrow it

#### Scenario: Another member cannot borrow a kept copy

- **WHEN** a member who is not first in line asks for a kept copy
- **THEN** it is refused as `copy-held`
