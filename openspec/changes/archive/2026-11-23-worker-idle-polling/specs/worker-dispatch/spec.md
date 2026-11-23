## ADDED Requirements

### Requirement: Idle polling
A loop that took nothing at a poll MUST wait `poll` (default 60 s) before the next, doubling the
wait at each such poll in a row up to `poll_max` (default 5 minutes; `poll_max` MUST NOT be less
than `poll`, and defaults to `poll` when `poll` is longer). A poll whose `next` answer differs
from the last one's in its items, their claims, their awaiting state, their labels or their
comment counts, and any poll at which the loop claimed an item, MUST start the wait again from
`poll`.

#### Scenario: Nothing to take
- **GIVEN** a loop with `poll = "60s"` and the default `poll_max`, and nothing it may take
- **WHEN** it polls for an hour
- **THEN** it waits 60, 120 and 240 seconds, then 300 seconds between polls

#### Scenario: A dispatch while idle
- **GIVEN** a loop that has backed off to `poll_max`
- **WHEN** an item is dispatched to it
- **THEN** it takes the item at its next poll, within `poll_max`
