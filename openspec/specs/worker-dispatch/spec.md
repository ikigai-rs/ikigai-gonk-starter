# Worker repository admission and dispatch

## Purpose
Keep repository admission bounded by worker configuration and make dispatch scheduling and
coverage visible.

Artifacts: none

Implemented in repository tooling (`tools/`) and worker configuration.

## Requirements

### Requirement: Repository admission
Workers MUST admit explicit repositories first, otherwise derive a safe repository name from
`repo:<name>` under the configured `[repos_from] github` organization, except excluded names.
They MUST retain router authorization and check GitHub existence, organization identity and
push permission before taking derived work. Failures MUST be explained once per reason.

#### Scenario: A new organization repository
- **GIVEN** an allowed organization and a valid router dispatch to an unlisted repository
- **WHEN** GitHub confirms the repository identity and push permission
- **THEN** it is a candidate without a configuration change or restart

#### Scenario: The config fence holds
- **WHEN** a name is invalid, excluded, or not admitted, or GitHub refuses access or push
- **THEN** the worker skips it with a reason
- **AND** an explicit stanza overrides derivation, including exclusions

### Requirement: Observable dispatch
Dispatch MUST assign priority 3 (or `--priority N`) only if priority is absent, unless
`--keep-unprioritized` warns and opts out. It MUST preserve the brief and print filtered next
rank or the exclusion reason after dispatch.

#### Scenario: An unprioritized dispatch
- **WHEN** an item without priority is dispatched
- **THEN** priority is assigned through the item sink without changing title or body
- **AND** its rank among the routing labels is shown

### Requirement: Discoverable worker coverage
Workers MUST publish explicit repositories, organization admission and exclusions on their
status item and label that item `worker-status:<name>`. Workers and dispatch MUST discover
existing status items through that label; configured status item numbers remain compatible.
A refused discovery-label write MUST log a dispatcher remedy without suppressing publication.
Dispatch MUST warn with admission reasons if no matching worker admits the repository.

#### Scenario: Coverage without configured numbers
- **GIVEN** a status item labelled for the routing worker
- **WHEN** dispatch reads its newest loop announcements
- **THEN** explicit and derived admission and exclusions determine its coverage warning
