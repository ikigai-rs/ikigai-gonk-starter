## ADDED Requirements

### Requirement: Seed a workshop ledger
The starter SHALL provide a command to populate an empty ledger with the workshop fixture, including its items, metadata, links, comments, hand-back, closed item and expired claim. It SHALL refuse a nonempty ledger and stop on errors.

#### Scenario: Fresh ledger
- **WHEN** a facilitator runs the workshop seed against an empty ledger with the necessary permissions
- **THEN** the fixture's items and relationships are filed and its special states are established

#### Scenario: Repeat or interrupted run
- **WHEN** the target ledger already contains an item
- **THEN** the command refuses without writing and directs the facilitator to use a new ledger

### Requirement: Keep facilitator guidance separate
The starter SHALL document setup and reset and provide facilitator notes outside the attendee ledger.

#### Scenario: Facilitation
- **WHEN** a facilitator reads the notes
- **THEN** the intended response and reason for each problematic item are available without being included in its ledger content
