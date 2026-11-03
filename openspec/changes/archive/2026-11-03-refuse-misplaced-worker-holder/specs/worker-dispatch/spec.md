## ADDED Requirements

### Requirement: Worker configuration key placement
Loading a worker configuration MUST refuse `holder` inside any table, naming that table
and saying to move the line above the first table header, beside `labels`. A top-level
`holder` MUST remain supported. Repository tables MUST refuse keys other than `remote`,
`github` and `base`, naming the table, unknown keys and known keys.

#### Scenario: A misplaced holder
- **WHEN** `holder` occurs in a repository, agent, usage or other table
- **THEN** loading fails with the table name and the placement remedy

#### Scenario: An unknown repository setting
- **WHEN** a repository table contains an unknown key
- **THEN** loading fails and names `remote`, `github` and `base` as the known keys
