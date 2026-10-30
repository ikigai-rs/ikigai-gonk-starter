# Derive admitted worker repositories and make dispatch observable

## Why
Explicit repository lists require repeated edits and restarts. Dispatch can leave an item
unprioritized and cannot discover worker coverage without status item numbers.

## What changes
Allow one GitHub organization in `[repos_from]`, with exclusions and explicit overrides.
Verify derived repositories and push permission before taking work. Publish admission on
ledger-discoverable worker status items. Give unprioritized dispatches priority 3 unless
explicitly opted out, and print their filtered `next` rank.

## Impact
Artifacts: none

Touches repository tooling in `tools/`, worker configuration and generated tooling.
Existing explicit configurations continue to work. Organization admission requires an opt-in
configuration edit and restart once; adding repositories then requires neither.
