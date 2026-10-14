# hooks/claude/ (optional, Claude Code only)

**The protocol in `AGENTS.md` does not need anything in this directory.** Every agent keeps
the rules by following them; this is a second layer for one agent, Claude Code, that turns
the first rule (claim before you write) into a refusal.

`require-claim.py` is a Claude Code `PreToolUse` hook. For each file edit (`Edit`, `Write`,
`MultiEdit`, `NotebookEdit`) inside this repository it:

1. reads the claim this working tree recorded, `.gonk/state/claim.toml`, which
   `python3 tools/gonk.py claim` writes;
2. asks gonk for that item, over the same mount the tool uses;
3. lets the edit through only if the item is open, held by this tree's holder, and its lease
   (if it has one) has not run out. Otherwise it exits 2, which refuses the edit, and says
   why and what to run.

Edits to `.gonk/state/` and `.gonk/local.toml` are always allowed, since the protocol itself
writes them. A file outside a repository made from this starter is not the hook's business.

Each refusal is a sensor of the repository's harness (`edit-without-live-claim`,
`edit-hook-not-wired`, `edit-hook-unreadable-input` in `.gonk/harness/harness.ttl`) and adds
one line to `.gonk/state/harness/firings.log`, naming the tool and the session but never the
file. Recording is best effort: the hook decides the same whether or not the line lands.

## Turning it on

Merge `settings.sample.json` into `.claude/settings.json` (shared with the team) or
`.claude/settings.local.json` (just you). It runs the hook from the repository, so it is the
version the repository carries.

## What it does not cover

- **Shell writes.** A file written by a `Bash` command (`sed -i`, a redirect, a code
  generator) is not seen: no hook can reliably tell which files a shell command touches.
- **Other agents.** Codex, pi, Hermes and the rest read `AGENTS.md`; this file means nothing
  to them.
- **gonk being down.** The hook cannot check a claim it cannot read, so it refuses. Start
  gonk, or turn the hook off; the rules still apply either way.
