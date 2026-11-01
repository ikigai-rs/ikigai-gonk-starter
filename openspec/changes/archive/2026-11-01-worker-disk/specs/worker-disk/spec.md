## ADDED Requirements

### Requirement: Free disk floor
Before taking an item, a worker MUST read the free space on the volume holding its work root.
Under `[disk] floor_gb` (default 30; 0 turns it off) it MUST claim nothing, say why in its log
and in a `Disk:` line on its status item, and take items again by itself once a reading is at
or over the floor. A reading that fails MUST be reported as unknown and MUST NOT hold the loop.

#### Scenario: Under the floor
- **WHEN** the free space is under the floor
- **THEN** the worker claims nothing
- **AND** its status item carries `Disk: … state=held` with the reason

#### Scenario: Space returns
- **WHEN** a later reading is at or over the floor
- **THEN** the worker posts `state=taking` and claims the next item

### Requirement: Pruning kept work
At start and once a day, a worker MUST remove each worktree, log, prompt and report under its
work root whose newest file is older than `[disk] prune_after` (default 7 days). It MUST keep
a worktree whose branch has an open pull request, one whose pull requests cannot be listed,
and any in a repository with an item running on the machine.

#### Scenario: A scratch work root
- **GIVEN** an old kept worktree, a recent one, and an old one with an open pull request
- **WHEN** the worker prunes
- **THEN** only the old one without an open pull request is removed
