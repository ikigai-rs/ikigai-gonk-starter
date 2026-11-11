## ADDED Requirements

### Requirement: Existing PR rework evidence
Before running a coding agent, the worker MUST snapshot same-repository PR heads named on the first line of the latest previous `Evidence:` hand-back. If no PR exists on the fresh branch after the agent exits, the worker MUST discover those previous PRs whose full head SHA changed during the run, read their CI, and include their PR URL and new full head SHA in its hand-back. Unchanged, unreadable or other-repository PRs MUST NOT become fallback evidence. Review jobs MUST NOT use this discovery.

The brief MUST permit an existing PR rework requested by the pinned task and explicitly require its URL on the first line of the report file and its pushed head commit on subsequent lines.

#### Scenario: Rework pushes without reporting
- **GIVEN** the previous hand-back names an existing PR in the configured repository
- **WHEN** the agent pushes a new head to that PR branch and writes no report
- **THEN** the hand-back carries that PR, its new full head SHA and its CI

#### Scenario: Previous PR unchanged
- **WHEN** the agent leaves the previous PR head unchanged and writes no report or new PR
- **THEN** the previous PR is not evidence for this run
