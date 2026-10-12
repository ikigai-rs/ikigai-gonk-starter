# ikigai-gonk-starter

**Work with gonk the way ikigai does: specs in the repository, the work in a ledger, a
claim before every change, and any agent.** This is the empty starting point for a
repository whose work is tracked by [gonk](https://github.com/ikigai-rs/ikigai-gonk), the
ledger and review host the ikigai project builds itself with. Use it as a template, run one
command, and Claude Code, Codex, pi, Hermes and the people on the team all work by the same
short set of rules, against the same ledger, without stepping on each other.

It is for a team, or one person running several agents, who want three things an agent's
chat history does not give them: **what the software is supposed to do**, written down where
the code is; **what to do next**, as a ranked answer with reasons; and **who is doing what
right now**, as a claim that expires rather than a guess.

## The loop

```sh
python3 tools/gonk.py next                            # what is ready, ranked, and why
python3 tools/gonk.py claim 12                        # take it, on a 30-minute lease
# reproduce, fix, open a pull request, get CI green
python3 tools/gonk.py report 12 --evidence <PR URL>   # the evidence on the item; hand it back
python3 tools/gonk.py reject 12 --note "<why not>"    # the evidence did not hold; ready again
python3 tools/gonk.py close 12 --evidence <PR URL>    # whoever dispatched it, once it holds
```

`report` hands the item back with `handback=true`, so the ledger records that it is awaiting
its dispatcher and `next` stops offering it to the next worker (ikigai-ledger 0.8.0, ledger
item 1199). `reject` is the dispatcher's answer when the evidence does not hold: `rejects=true`
clears that state and the item is ready again. Both keep the `Evidence:` prefix in the comment,
so a ledger older than 0.8.0 — which ignores the argument it does not know — reads the hand-back
the same way.

That is the whole protocol, and [`AGENTS.md`](AGENTS.md) states its rules: claim before you
write, one agent per item and per working tree, reproduce before you fix, every change
through a pull request with green CI, evidence on every report and close, and never a bare
`#N`. Every agent reads that one file.

## What is in it

```text
AGENTS.md                the rules, for every agent and every person
CLAUDE.md                `@AGENTS.md`: Claude Code imports the same rules (not a symlink; see below)
openspec/                specs (what the system does now) and changes (what is proposed)
artifacts/<name>/        one directory per deployable thing, any language each
.gonk/                   which ledger, how to reach gonk, and where everything lives
  layout.toml            the spec format(s), the artifacts root, queries, scripts
  gonk.toml              the ledger this repository's work lives in
  queries/  scripts/     saved SPARQL and workflow scripts (reserved; see below)
tools/gonk.py            init, seed, file, next, claim, release, report, close, status
tools/worker.py          the worker loop: take an item, run an agent on it, hand it back
tools/new.py             generate a repository with the library-lending sample in it
templates/               what new.py generates from: the sample, and one directory per language
hooks/claude/            an OPTIONAL Claude Code hook that refuses edits without a claim
worker/                  set up a machine that does agentic work for a hub, check it, run the loop
docs/windows.md          Windows: WSL2, and what each agent does there
.gitattributes           LF in every checkout, whatever core.autocrlf says
```

Everything is empty until you fill it, or until `tools/new.py` fills a copy for you. `tools/gonk.py` is standard-library Python (3.11 or
later) with no dependencies; `just` recipes wrap it one-for-one for those who have `just`.

## Try the sample

`tools/new.py` generates a new repository from this starter with a sample worth working on: a
**library-lending** system (a catalog, members, loans with due dates, renewals, overdue rules
and late fees) as two artifacts, an HTTP JSON service and a command-line client of it, with
its specs in OpenSpec and two changes in flight to file and claim.

```sh
just new ../library --target python        # or: python3 tools/new.py ../library --target python
cd ../library
python3 scripts/test.py                    # every test, as its CI runs them
python3 tools/gonk.py init --ledger library --socket ~/.ikigai/gonk.sock --holder me
python3 tools/gonk.py seed                 # each open task of each change, as a ledger item
python3 tools/gonk.py next                 # what is ready, ranked, and why
python3 tools/gonk.py claim 1
```

**The spec's scenarios are the tests.** `openspec/vectors.json` holds every `#### Scenario:`
of the specs as language-neutral steps (an operation, its arguments, the date it happens on,
and the whole answer expected), the service's tests run each one against the rules and again
over HTTP, and a test fails if a scenario has no vector or a vector names no scenario. Time is
an input: no rule reads a clock, so the due dates are the same on any day the tests run.
Python (standard library only) and Rust (one cargo workspace, standard library only) are the
targets today; Deno and Go are planned, each running the same vector file. This repository's CI
generates every target into a scratch directory and runs its tests, so a template that has gone
stale fails.

`seed` files one item per unchecked task in `openspec/changes/<id>/tasks.md`, keyed by the
task's IRI (`urn:openspec:change:<id>:task:<number>`, the name the
[gonk Book](https://ikigai-rs.github.io/ikigai-tutorial/gonk/spec/ledger.html)'s OpenSpec
mapping gives it), labeled `change:<id>` and `artifact:<name>`, and *about* the requirement the
task's parentheses cite. It then links each task's item to the next task's with `blocks`, since
the order of a tasks list is a dependency: `next` offers the first open task of each change,
and closing it makes the next one ready. A second `seed` files and links nothing: the ledger
answers `existing` for a key it already holds, and the links are already there. The sample
also carries three saved SPARQL queries and a workflow script (below).

## Setting it up

You need gonk running somewhere, and `ikigai`, its command line, on every machine that
works in the repository:

```sh
cargo install --locked --git https://github.com/ikigai-rs/ikigai-gonk ikigai-gonk
cargo install --locked ikigai-cli --features quic
```

gonk's [fresh-machine runbook](https://github.com/ikigai-rs/ikigai-gonk/blob/main/docs/fresh-machine.md)
covers starting it. Then, in your copy of this repository, `init` writes what you tell it and
prints exactly what is still missing. It reads no environment variables; flags and the files
under `.gonk/` are the whole configuration, and a missing piece is an error that names the
command that supplies it.

### gonk on this machine

```sh
python3 tools/gonk.py init --ledger acme --socket ~/.ikigai/gonk.sock --holder claude-1
```

The ledger name goes into `.gonk/gonk.toml` (committed, so every clone works in the same
ledger); the socket and the holder go into `.gonk/local.toml` (git-ignored, because they are
this machine's and this working tree's). A ledger has no create step: it exists once
something is filed in it, and `init` says so. Over the socket every request is gonk's
owner's, so the log cannot tell your agents apart; that is fine on your own machine, and the
reason the remote setup is different.

### gonk on another machine

The remote door is **QUIC with a client certificate per machine**. Each machine makes its
own identity, so its private key never leaves it:

```sh
ikigai cert generate --dir ~/gonk-cert
python3 tools/gonk.py init --ledger acme --holder build-box \
    --quic quic://gonk-host:1060 --cert-dir ~/gonk-cert
```

`init` prints the machine's identity, `urn:iki:host:gonk:client:<fingerprint>` (the name gonk
puts on every request it makes, on every item it files, and in its access log), and the
lines gonk's operator adds for it: `ikigai-gonk client add … --cert client.crt --worker acme`
for an unattended machine or `--ledger acme=write` for a person's, or the `clients.json` and
`grants.json` entries by hand. The operator
restarts gonk and sends back the `server.crt` from the bundle `client add` wrote; copy it
over the one in `~/gonk-cert` (`cert generate` wrote a server identity of its own there,
which is not gonk's, so this machine could not pin gonk with it). Then `init` reports the
machine ready. This works the same for an unattended machine as for a person's laptop,
and a claim from it is stamped a **machine**'s by gonk, never the caller's choice. An
unattended worker is enrolled with `--worker <ledger>` instead of `--ledger acme=write`: that
grant can claim, renew, comment and release, but not close.
Records written before gonk adopted that name (item authors, its access log) spell it
`urn:iki:gonk:client:<fingerprint>`: gonk keeps that spelling as a permanent alias, never
mints it again, and reads both as this one machine, so `init` prints it beside the identity
for searching older records.

**Why not HTTP for a remote machine?** gonk's HTTP door listens on loopback only and refuses
any other bind until it has TLS and a real domain for passkeys. A tunnel to that port would
reach it, but every write through it is `anon`, under the anonymous grant: no name per
machine, so neither claims nor items could be told apart, and the rule that keeps a writer
from naming someone else as an item's author cannot run. HTTP is the right door for a browser
and for `curl` on the gonk machine itself; QUIC is the door for other machines.

### One transport, and MCP for agents

Every request `tools/gonk.py` makes goes through `ikigai` with an **alias mount**:
`--mount urn:gk:=<socket>` or `--mount urn:gk:=quic://host:port --cert-dir <dir>`, and the
ledger's `urn:iki:ledger:acme:claim` is then `urn:gk:iki:ledger:acme:claim`. One client
for both doors means one code path to test, and the alias means no name the tool sends can
resolve against something bound in the local `ikigai` by accident. The `ikigai` client also
fetches each resource's contract from gonk before it sends, so arguments are routed by the
ledger's own declarations rather than by this script's guesses.

An agent that speaks MCP is configured with `ikigai mcp`, which `init` prints for your
transport, for example `ikigai mcp --override urn:iki:ledger:=quic://gonk-host:1060
--cert-dir ~/gonk-cert`. Its tools are the ledger's own actions (`ledger-next__source`,
`ledger-claim__sink`, …) filtered by the capability the session holds, so an agent is never
offered what it may not do. The [gonk Book's MCP chapter](https://ikigai-rs.github.io/ikigai-tutorial/gonk/agent/mcp.html)
walks through it, including the client-side `--grant` ceiling.

## Each agent, and the one instruction file

| agent | reads | what to do |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | nothing: its one line, `@AGENTS.md`, imports the rules ([docs](https://code.claude.com/docs/en/memory)) |
| Codex | `AGENTS.md` | nothing: Codex reads `AGENTS.md` from the project root down to the working directory ([docs](https://developers.openai.com/codex/guides/agents-md)) |
| Hermes Agent | `AGENTS.md` | nothing, unless the project also has a `.hermes.md` or `HERMES.md`, which Hermes ranks first and reads INSTEAD ([docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files)) |
| pi | `AGENTS.md` or `CLAUDE.md` | nothing: pi loads context files from its agent directory, the working directory and its parents ([docs](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/configuration.md)) |

Checked against each project's documentation on 2026-10-10. Two things that documentation
does not say, so this does not claim them: whether pi reads BOTH `AGENTS.md` and `CLAUDE.md`
when one directory holds both, and whether pi or Hermes can be pointed at a differently named
file. Codex can, with `project_doc_fallback_filenames`, but has no need to here. pi's source
answers the first: it takes the first of `AGENTS.md` and `CLAUDE.md` it finds in a directory,
in that order, so it never reads `CLAUDE.md` here; Hermes documents the same order.

`CLAUDE.md` is an import rather than a symlink because of Windows: Git for Windows checks a
symlink out as a file holding the target's name unless `core.symlinks` is on, and Claude Code
would read that one line instead of the rules. [`docs/windows.md`](docs/windows.md) has that,
the line-ending trap `.gitattributes` closes, WSL2 setup, and what each agent is documented to
do on native Windows and in WSL2.

## The optional Claude Code hook

`hooks/claude/require-claim.py` turns the first rule into a refusal for Claude Code: a file
edit in this repository is refused unless this working tree holds a claim that gonk still
says is its own, with time left on the lease. It is a second layer, never the only one: the
protocol works without it, every other agent keeps the rules by reading them, and it cannot
see a file written by a shell command. To turn it on, merge
[`hooks/claude/settings.sample.json`](hooks/claude/settings.sample.json) into
`.claude/settings.json`. See [`hooks/claude/README.md`](hooks/claude/README.md).

## Queries and scripts

`.gonk/queries/` (saved SPARQL over the ledger and the spec graph) and `.gonk/scripts/`
(workflow scripts) are named by `layout.toml`. They are meant to be resources on gonk: a query
or a script published as `urn:script:{name}` with
[ikigai-script](https://github.com/ikigai-rs/ikigai-script) (versioned by content, run under
the runner's own authority, every run recorded), and drafted from a sentence by
`urn:nl:sparql` from [ikigai-nl](https://github.com/ikigai-rs/ikigai-nl). `init` asks gonk
which it offers. When it offers `urn:script:`, `init` publishes each file as
`urn:script:<ledger>-<name>`, with every `LEDGER` in the text (`urn:iki:ledger:graph:LEDGER`)
replaced by this repository's ledger, so the files stay valid as written and one gonk holds
many repositories' scripts. Until it does, `init` says they are ready but unpublished, and they
stay files.

The sample ships `open-items-for-artifact`, `open-work-by-change` and
`requirements-with-open-work` (the spec graph joined with the ledger), and the plan
`next-for-artifact` (the ledger's own `next`, narrowed to one artifact's items).

## Worker machines

A **worker** is a machine that does agentic work for a hub: it claims an item, works in a
fresh worktree as an account of its own, and opens a pull request. [`worker/`](worker/README.md)
sets one up (Linux with systemd, or macOS) and proves its boundary: two accounts, `agent` (the
worker, which never works in a person's checkouts) and `hubdiag` (the hub's read-only eyes),
each closed out of the admin's home; read-only views of the agent's work for the hub; sshd
allowing the hub one tunnel and nothing else; and a self-test that tries every file in the
admin's home as each account and fails on any that opens.

```sh
./worker/worker-setup.sh --dry-run --key hub.pub    # every change it would make, as a list
sudo ./worker/worker-setup.sh --key hub.pub         # make them (idempotent; repairs drift)
sudo ./worker/worker-setup.sh --check               # the boundary self-test, and the facts file
```

[`worker/README.md`](worker/README.md) is the runbook, in order: the OS, the accounts, the
GitHub token, never sleeping, a local model, ikigai and gonk, reaching the worker from the
hub, and the worker loop, plus the two things a hub reads over ssh (the probe's output and the
facts file). CI runs the whole setup for real on throwaway Ubuntu and macOS runners, then
re-creates each mistake it exists to catch and proves the check red.

### The worker loop

`tools/worker.py` is what runs on a worker, as its own account. It pulls `next` narrowed to
the labels the dispatcher marks work with (`worker:<name>`), takes the first item it may,
claims it on a lease and renews the lease while it works, branches a fresh worktree from
`origin/main` under its own home, runs the configured agent (Claude Code, Codex, Hermes, or a
command of your own) with the item as its brief behind a fixed preamble of this file's rules,
and hands the item back: the pull request it opened and that pull request's CI, as an
`Evidence:` comment, then the claim released. **It never closes and never merges**: the
dispatcher checks the evidence and does both.

```sh
cp worker/worker.sample.toml .gonk/worker.toml       # labels, repositories, agent, budget
python3 tools/worker.py plan                         # what it would take, and why not the rest
python3 tools/worker.py run --once                   # one item, then exit
python3 tools/worker.py run                          # the loop; worker/units/ runs it as a service
```

One agent per repository: it takes no item whose repository already has a live claim held by
anyone, and runs one item per repository on its machine. Until gonk's ledger enforces that
rule itself, it is best-effort (two workers can read "free" at the same moment); the worker
README says what else waits on the ledger.

## Coming next

- **More languages** for the generator: Deno and Go, each passing the same vectors (Rust has
  landed: `--target rust`).
- **A second spec format**, beside OpenSpec, lifted to the same graph and ledger items by a
  mapping file rather than code.
- **The worker's gaps, in the ledger.** One agent per repository enforced by the ledger rather
  than read before claiming; and briefs as resources gonk serves, beyond the item's body.
- **The gonk Book's chapters** on a project of your own, walking the generated sample from
  a spec change to a closed item.

## Developing this starter

```sh
python3 -m venv .venv && .venv/bin/pip install ruff==0.16.1
just gates          # or scripts/gates.sh: ruff check, ruff format --check, the tests, then
                    # the sample, generated for every target, running its own tests
```

The tests are standard-library `unittest`, and include the worker scripts' hermetic tests,
the worker loop's (an in-memory ledger, a scratch git origin, a stub `gh` and a fake agent, so
no real agent and no network), and the **public-tree guard** (`tests/test_tree_guard.py`): this repository is public, so no
tracked file may hold a path under a person's home, an mDNS `.local` host name, a non-loopback
IPv4 address, or the name of one of the project's own machines. The integration tests start
a scratch gonk of their own (a temporary directory, an ephemeral port, its own socket) when
`ikigai-gonk` and `ikigai` are on PATH, and skip, saying why, when they are not, as in CI.

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or
[MIT license](LICENSE-MIT) at your option.
