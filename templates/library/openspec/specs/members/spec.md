# Members

## Purpose

Who may borrow: a member, registered under an id the library chooses.

Artifacts: `service`, `cli`

## Requirements

### Requirement: A member is registered once

The library SHALL register a member under an id with a name, and MUST refuse a second member
with an id already registered.

#### Scenario: Registering a member

- **WHEN** a member is registered with an id and a name
- **THEN** the library answers with both

#### Scenario: Registering a member id twice is refused

- **WHEN** a member is registered with an id that is already registered
- **THEN** it is refused as `duplicate-member`
