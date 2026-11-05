## MODIFIED Requirements

### Requirement: The dispatcher files one item per finding
`gonk.py findings N` MUST read review job N's latest hand-back by a trusted worker (see "Only a
worker's hand-back is filed"), validate its block by the worker's rules with the one shared
validator, and for each finding append one item of class
`https://ikigai-rs.dev/ns/ledger#Finding` to the job's ledger, with `revision` the head, `about` the
file's IRI, the job as its parent, the labels `severity:<severity>` and the job's `repo:<name>`, a
key made from the job's number and a digest of path, line and title, the finding's title, and a body
with its problem, fix and location. It MUST print each finding's number and IRI. A rerun MUST file no
duplicates. A malformed or missing block, a job that is not a review job, and a hand-back already
answered or rejected MUST file nothing and say why; an older hand-back MUST NOT be used instead. A
block whose revision contradicts a job's sha revision is malformed.

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

## ADDED Requirements

### Requirement: A rejected hand-back is not filed
`gonk.py findings N` MUST collect the comment IRIs named by the `rejects` of every comment on the
job, whoever posted it, because honoring a reject can only stop a filing, never cause one. It MUST
never file a hand-back whose comment IRI is among them. When the latest hand-back by a trusted worker
is among them, the command MUST file nothing, not close, not use an older hand-back, and say in one
sentence which comment rejected it and that the worker must hand back again. A dispatcher's ordinary
comment that the ledger recorded as a reject MUST count like any other. A ledger that sends no
`rejects` key MUST be read as before, with nothing inferred from a comment's words, and the warning
for a passed-over hand-back MUST say that a ledger older than ikigai-ledger PR 87 cannot tell rejected
hand-backs apart until it is upgraded.

#### Scenario: Rejected, then someone else's hand-back
- **WHEN** a trusted worker's hand-back is rejected and someone untrusted hands back
- **THEN** no item is filed, and the command names the rejecting comment

#### Scenario: Rejected, then the worker's new hand-back
- **WHEN** a trusted worker's hand-back is rejected and the worker hands back again
- **THEN** the new hand-back's block is filed

#### Scenario: A reject naming two hand-backs
- **WHEN** one comment's `rejects` names two trusted hand-backs and someone untrusted hands back
- **THEN** no item is filed

#### Scenario: A dispatcher's ordinary comment
- **WHEN** a dispatcher's ordinary comment on an awaiting job recorded `rejects`
- **THEN** no item is filed until the worker hands back again

#### Scenario: A reject by anyone
- **WHEN** the comment that rejected a trusted hand-back is by an untrusted or unverified author
- **THEN** no item is filed, as for any other reject

#### Scenario: A ledger with no rejects
- **WHEN** no comment on the job has a `rejects` key and a trusted worker's hand-back is followed by
  one from another identity
- **THEN** the trusted worker's block is filed, and the warning says what the ledger cannot tell
