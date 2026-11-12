## ADDED Requirements

### Requirement: Accessibility check results
For changes touching what people see, the worker brief MUST require a `## Accessibility` section in the PR description with a Check | Result | Evidence table. Every accessibility check named by a PR or ACCESSIBILITY.md MUST have a result of Pass, Fail or Blocked and evidence identifying the test or command, tool and version when used, or why it could not run. Unrun checks MUST be Blocked and retained; an unrun screen reader MUST NOT be called not applicable. Generated agent instructions and harness guidance MUST carry the same rule.

#### Scenario: A check cannot run
- **WHEN** a named accessibility check cannot run, including a screen reader check
- **THEN** the record retains it as Blocked with the reason

#### Scenario: Generate a repository
- **WHEN** a repository is generated for any target or process profile
- **THEN** its accessibility record has a Check | Result | Evidence table and its agent instructions require the three results with evidence
