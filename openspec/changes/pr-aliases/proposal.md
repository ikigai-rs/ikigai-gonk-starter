# Name worker items by their pull requests

## Why
Workers currently hand back PR URLs in comments, and the CLI accepts only item numbers.
A PR should identify its item without parsing comments or confusing the two number spaces.

## What changes
Workers attach each discovered PR as a `gh:owner/repo#number` alias before hand-back.
The CLI resolves that spelling to the configured ledger's item number before any action.
Alias conflicts and missing grants remain visible in the hand-back; workers never delete or
reassign aliases. Numeric arguments retain their existing behavior.

## Impact
Artifacts: none

Implemented in repository tooling (`tools/`). Deployment requires ikigai-ledger 0.18.0
(default#2905) and a companion ikigai-gonk grant/config change; see worker/README.md.
This checkout cannot change the gonk deployment or another repository's configuration.
