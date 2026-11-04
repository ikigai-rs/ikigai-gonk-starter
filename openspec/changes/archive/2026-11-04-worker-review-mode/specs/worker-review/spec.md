## ADDED Requirements

### Requirement: A review job is told by its class
A worker MUST treat an item of class `https://ikigai-rs.dev/ns/ledger#ReviewJob` as a review job
and every other item as a change job, whose run MUST NOT change. Routing MUST NOT differ between
them. A review job's `revision` is its head, and its body MUST name a range `base..head` ending at
that commit, since a dispatch pins the body and not the revision; otherwise the worker MUST run no
agent and MUST hand the job back saying why.

#### Scenario: A review job runs under the review preamble
- **WHEN** a worker takes a dispatched review job
- **THEN** its agent runs in a fresh worktree at the head
- **AND** its brief forbids changing, committing, pushing, opening a pull request or filing items

#### Scenario: A revision the pinned body does not name
- **WHEN** a review job's revision is not the head of any range in its body
- **THEN** no agent runs and the job is handed back without evidence, naming the revision

### Requirement: A review changes nothing
After a review job's agent exits, the worker MUST check that the worktree has no changed or
untracked file, that HEAD has not moved, and that no local branch or stash has moved. Otherwise it
MUST file nothing, keep the worktree, and hand the job back saying what changed. It MUST also file
nothing for an agent that overran its budget, was stopped, or exited other than 0.

#### Scenario: A changed worktree
- **WHEN** the agent leaves an untracked file, or makes a commit, and writes a valid report
- **THEN** no item is filed and the job's comment names the change

### Requirement: A review's report is validated
The worker MUST read the report as JSON of the form `{"findings": [{"severity", "title", "problem",
"fix", "path", "line"}]}`, with `line` optional, severity one of critical, major and minor, and a
path inside the repository. A report that is missing or invalid in any part MUST file nothing, and
the job MUST be handed back saying what was wrong.

#### Scenario: An invalid report
- **WHEN** one finding in the report has an unknown severity, or a path outside the repository
- **THEN** no item is filed, not even for the valid findings
- **AND** the job's comment names the finding and the fault

### Requirement: The worker files one item per finding
For each finding the worker, never the agent, MUST append one item of class
`https://ikigai-rs.dev/ns/ledger#Finding` to the job's ledger, with `revision` the head, `about` the
file's IRI, the job as its parent, the labels `severity:<severity>` and the job's `repo:<name>`, a
key made from the job's number and a digest of path, line and title, the finding's title, and a
body with its problem, fix and location. A rerun MUST file no duplicates.

#### Scenario: A valid report
- **WHEN** the report has two findings
- **THEN** two items are filed with those fields, each linked to the job as its parent

#### Scenario: A rerun
- **WHEN** the same job runs again and reports the same findings
- **THEN** no new item is filed, and the hand-back names the same items

### Requirement: A review hands back with its findings as the evidence
The worker MUST hand a finished review back with `handback=true` and an `Evidence:` line naming each
finding item's IRI, or one saying the review found nothing. It MUST NOT look for a pull request,
and MUST NOT close the job. A ledger refusal part way MUST hand the job back without evidence,
naming what was filed.

#### Scenario: Nothing found
- **WHEN** the report's list of findings is empty
- **THEN** nothing is filed, and the job awaits its dispatcher with a line saying so
