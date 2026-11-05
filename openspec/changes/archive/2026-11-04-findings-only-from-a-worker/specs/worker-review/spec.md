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

## ADDED Requirements

### Requirement: Only a worker's hand-back is filed
`gonk.py findings N` MUST file a block only from a hand-back whose author the ledger marks verified
(`attribution.author_status`) and which the `workers` list of the dispatcher's git-ignored
`.gonk/dispatcher.toml` names, because no gonk door lists enrolled clients and their authority. The
newest such hand-back MUST be the one read; a newer hand-back by anyone else MUST be passed over and
its author named. With no such hand-back the command MUST file nothing, not close, and say in one
sentence who posted the latest hand-back, why that identity is not trusted, and how to trust it,
printing the `workers` line to use when the author is verified. An unverified author MUST NOT be
trusted, whatever it spells and whatever the list says.

#### Scenario: A trusted worker
- **WHEN** the latest hand-back's verified author is in the list
- **THEN** its block is filed as before

#### Scenario: Another identity
- **WHEN** the only hand-back's verified author is not in the list
- **THEN** no item is filed, and the command names the author and prints the `workers` line to use

#### Scenario: An unverified author
- **WHEN** the only hand-back's author is free text or a claim, even one spelling a listed client
- **THEN** no item is filed, the command names the author, and it prints no `workers` line

#### Scenario: A later trusted hand-back
- **WHEN** a hand-back by another identity is followed by one from a trusted worker
- **THEN** the trusted worker's block is filed

#### Scenario: A later untrusted hand-back
- **WHEN** a trusted worker's hand-back is followed by one from another identity
- **THEN** the trusted worker's block is filed, and the command names the other author in a warning
