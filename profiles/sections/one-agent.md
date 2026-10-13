## 2. One agent per claimed item, one agent per working tree

An item has one holder at a time, and a working tree has one agent at a time. Two agents in
one tree overwrite each other's files no matter what the ledger says. To run agents in
parallel, give each its own working tree (`git worktree add`) and its own holder name
(`python3 tools/gonk.py init --holder <name>` in that tree). A tree holds one claim.
