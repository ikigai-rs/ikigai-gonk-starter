# Worker review mode

## Purpose
Let a worker review a range of commits without changing it: the agent writes its findings to a
report, the worker validates the report and hands the findings back, and the dispatcher files the
items, so agents and workers still never file and the worker's grant stays narrow.

Artifacts: none

Implemented in repository tooling (`tools/worker.py`, `tools/gonk.py`, `tools/review_findings.py`).
The worker needs only its `--worker` grant; the dispatcher's write access to the ledger does the
filing (worker/README.md, "Review jobs").

## Requirements

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
MUST carry no findings, keep the worktree, and hand the job back saying what changed. It MUST also
carry no findings for an agent that overran its budget, was stopped, or exited other than 0.

#### Scenario: A changed worktree
- **WHEN** the agent leaves an untracked file, or makes a commit, and writes a valid report
- **THEN** no findings are handed back and the job's comment names the change

### Requirement: A review's report is validated
The worker MUST read the report as JSON of the form `{"findings": [{"severity", "title", "problem",
"fix", "path", "line"}]}`, with `line` optional, severity one of critical, major and minor, and a
path inside the repository. A report that is missing or invalid in any part MUST carry no findings,
and the job MUST be handed back saying what was wrong.

#### Scenario: An invalid report
- **WHEN** one finding in the report has an unknown severity, or a path outside the repository
- **THEN** no findings are handed back, not even the valid ones
- **AND** the job's comment names the finding and the fault

### Requirement: A review hands back with its findings on the comment
The worker MUST hand a finished review back with `handback=true`, an `Evidence:` line saying how many
findings there are or that the review found nothing, and one fenced JSON block in the report's format
with the range's `base` and `revision`, its list empty when there are none. The worker MUST make no append
and no link, its grant MUST stay read, claim and comment, it MUST NOT look for a pull request, and it
MUST NOT close the job. A hand-back too large for one comment MUST NOT be truncated: the job goes back
without evidence, saying so, with the report file kept with the worktree.

#### Scenario: Findings
- **WHEN** the report has two findings
- **THEN** the job awaits its dispatcher, its comment carries both in one block, and no item is filed

#### Scenario: Nothing found
- **WHEN** the report's list of findings is empty
- **THEN** the job awaits its dispatcher with a line saying so, and the block's list is empty

#### Scenario: Too large
- **WHEN** the findings do not fit in one comment
- **THEN** no hand-back is made, the comment says so, and the report file stays

### Requirement: The dispatcher files one item per finding
`gonk.py findings N` MUST read review job N's latest hand-back, validate its block by the worker's
rules with the one shared validator, and for each finding append one item of class
`https://ikigai-rs.dev/ns/ledger#Finding` to the job's ledger, with `revision` the head, `about` the
file's IRI, the job as its parent, the labels `severity:<severity>` and the job's `repo:<name>`, a
key made from the job's number and a digest of path, line and title, the finding's title, and a body
with its problem, fix and location. It MUST print each finding's number and IRI. A rerun MUST file no
duplicates. A malformed or missing block, a job that is not a review job, and a hand-back already
answered MUST file nothing and say why; an older hand-back MUST NOT be used instead. A block whose
revision contradicts a job's sha revision is malformed.

#### Scenario: A valid hand-back
- **WHEN** the hand-back has two findings
- **THEN** two items are filed with those fields, each linked to the job as its parent

#### Scenario: A rerun
- **WHEN** the command runs again on the same hand-back
- **THEN** no new item is filed, and it prints the same items

#### Scenario: A malformed block
- **WHEN** one finding in the block has an unknown severity
- **THEN** no item is filed, not even for the valid findings, and the command says why

#### Scenario: A ledger refusal part way
- **WHEN** the ledger refuses a finding after filing another
- **THEN** the command prints what was filed, says what stopped it, fails, and does not close

### Requirement: The dispatcher closes a review on its findings
`gonk.py findings N --close` MUST, once every finding is filed, close the job with evidence naming
each finding's IRI, or with no evidence IRI and a note saying the review found nothing.

#### Scenario: Findings filed
- **WHEN** the command files two findings with `--close`
- **THEN** the job is closed with both IRIs as evidence
