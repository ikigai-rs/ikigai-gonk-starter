## ADDED Requirements

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
