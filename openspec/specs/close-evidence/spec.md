# Close evidence

## Purpose
Keep a dispatcher's evidence on the close that it supports, and let the hub record
the merged PR and its merge commit together.

Artifacts: none

Implemented by repository tooling (`tools/`) and the generated hub profile (`profiles/hub/`).
Requires gonk carrying ikigai-ledger 0.18.0 or later for structured close evidence.

## Requirements

### Requirement: Structured close evidence
The close command MUST require at least one absolute evidence IRI, accept repeated
`--evidence`, and send each as an `evidence=` argument to the ledger close resource.
It MUST retain readable evidence in the close note. On ikigai-ledger 0.18.0 or later,
the close history MUST retain every evidence IRI as structured evidence. The item's
Turtle face MUST retain both IRIs in the readable close note; exporting StatusEvent
triples is the ledger's responsibility and is not provided by its item face.
A worker hand-back MUST identify its PR URL as proposed evidence and MUST NOT close
or merge the item.

#### Scenario: Two pieces of evidence
- **WHEN** a dispatcher closes with a PR URL and a merge commit URL
- **THEN** both are passed to close and retained on its StatusEvent
- **AND** both are present in history and Turtle

### Requirement: Merged pull request evidence
The hub MUST provide a close step that verifies a GitHub PR is merged, obtains its
merge commit SHA, and passes both its canonical PR URL and commit IRI to close.
It MUST refuse without closing when GitHub fails or the merge cannot be verified.
The dispatcher MUST check green CI and merge using existing authorization before this step.

#### Scenario: A merged pull request
- **WHEN** the hub closes an item using a merged PR with a valid merge commit SHA
- **THEN** close receives the PR URL and `https://github.com/<owner>/<repo>/commit/<sha>`

#### Scenario: An unmerged pull request
- **WHEN** GitHub reports an open or closed unmerged PR, or no merge commit
- **THEN** the hub refuses without changing the ledger
