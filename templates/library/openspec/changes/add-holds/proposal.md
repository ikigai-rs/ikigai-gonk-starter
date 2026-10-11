# Holds: a place in line for a title that is out

## Why

When every copy of a title is out, a member has no way to ask for the next one, so whoever
happens to be at the desk when a copy comes back gets it. A hold puts members in line, keeps a
returned copy for the first of them, and stops the current borrower renewing past the line.

## What Changes

- A member can hold a title whose copies are all on loan.
- A returned copy of a held title is kept for the first holder: nobody else can borrow it.
- A loan of a held title cannot be renewed.
- The client gains `hold`, and `copy` shows who a kept copy is waiting for.

## Impact

- Artifacts: `service`, `cli`
- Affected specs: `holds` (new), `renewals`
