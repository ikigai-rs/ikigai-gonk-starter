# openspec/changes/

Proposals in flight, one folder per change, kept apart from `specs/` until they are done so
that several can be in flight without editing the truth underneath each other.

```text
changes/<change-id>/
├── proposal.md          why, and what changes; `## Impact` carries the `Artifacts:` line
├── specs/<capability>/spec.md   the change as a delta: ADDED / MODIFIED / REMOVED Requirements
└── tasks.md             the checklist that implements it
```

A `proposal.md` for a new feature, endpoint family or app may also carry an optional
`## Resource map` heading if you design resource-first: the sets, atoms and compositions it
is designed as, names before code (see `docs/resource-design.md`).

When a change is done it is archived: its deltas merge into `specs/`, and its folder moves
to `changes/archive/<date>-<id>/`. So at any moment this directory is exactly the work in
flight, which is what the ledger's items are filed from.

OpenSpec takes only the folders here as changes, so this README is not read as one.
