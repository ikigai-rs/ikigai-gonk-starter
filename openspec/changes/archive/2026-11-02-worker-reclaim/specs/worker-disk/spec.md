## ADDED Requirements

### Requirement: Reclaiming kept work
At each poll, a worker MUST ask the ledger about the item of each kept worktree under its work
root, and log what it reclaims with its size. It MUST remove a worktree whose item is closed, or
whose item's claim is held by another holder than the one that made it, whatever its age, unless
`keep` or a `[disk] prune_after` of `0s` says to keep it. While the free space is under
`[disk] floor_gb`, after those removals, it MUST drop the `target/` of worktrees whose item is
open and unclaimed, one at a time until a reading is at or over the floor. It MUST NOT touch a
worktree whose item is claimed by this loop's holder or by the holder that made it, one held by
another holder when who made it is not known, one the ledger does not answer for, one whose item
does not carry its repository's `repo:` label, or any in a repository with an item running on the
machine.

#### Scenario: A closed item
- **GIVEN** a kept worktree a day old, whose item the dispatcher has closed
- **WHEN** the worker polls
- **THEN** the worktree is removed and what it held is logged

#### Scenario: A claim that holds it
- **GIVEN** a kept worktree whose item is claimed by the holder that made it
- **WHEN** the worker polls, with the free space under the floor
- **THEN** the worktree and its `target/` stay

#### Scenario: Under the floor
- **GIVEN** a kept worktree whose item is open and unclaimed
- **WHEN** the worker polls, with the free space under the floor
- **THEN** its `target/` is dropped and the rest of it stays
