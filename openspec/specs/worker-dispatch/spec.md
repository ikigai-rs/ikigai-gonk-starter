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

### Requirement: Shared pool
A loop whose labels name an `agent:` MUST read `next` with its own labels first, then with each
label of its `pool` in place of its `worker:<name>`, keeping its other labels. `pool` MUST default
to `worker:pool`, with `worker:mac` before it on macOS; `[]` opts out, and a loop naming no `agent:`
MUST NOT read a pool. A shared label MUST route an item only through a router's newest dispatch
naming that label set, as a machine's label does. Dispatch MUST target `worker:pool` when `--labels`
names no `worker:` label, keep `worker:<host>` for pinning, and refuse a shared target without an
`agent:` label. A loop MUST publish its pool on its status line and label that item for it, and
coverage MUST count such a loop for a dispatch to the pool. Two loops MUST NOT hold one item: the
ledger's compare-and-set claim decides, and the loser MUST go on to its next candidate.

#### Scenario: Two loops read one pool item as free
- **GIVEN** an item dispatched to `worker:pool,agent:claude` and two claude loops on two machines
- **WHEN** both read it as free and both claim it
- **THEN** one holds it, the other is refused and takes its next candidate

#### Scenario: Pinning takes an item out of the pool
- **GIVEN** an item that carries `worker:pool`
- **WHEN** its newest dispatch names one machine's `worker:<name>` instead
- **THEN** only a loop on that machine takes it

#### Scenario: The pool without an agent
- **WHEN** a dispatch names a shared target and no `agent:` label
- **THEN** it is refused before anything is written, naming `agent:<kind>`

### Requirement: Machine names are not shared labels
Writing a loop's configuration MUST refuse a machine named `pool` or `mac`.

#### Scenario: Naming a machine after the pool
- **WHEN** `init-agents` or `onboard` is given the name `pool`
- **THEN** it refuses, saying `worker:pool` is shared

### Requirement: A build per worktree
A worker MUST run each agent with `CARGO_TARGET_DIR` set to that worktree's `target`, over the
account's environment, so that no two worktrees share a cargo target directory.

#### Scenario: A shared directory in the account's environment
- **GIVEN** `CARGO_TARGET_DIR` names a shared directory in the worker's environment
- **WHEN** the worker runs an agent
- **THEN** the agent sees its own worktree's `target`

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
