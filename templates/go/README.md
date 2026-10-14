# A lending library, worked with gonk

A small library-lending system, and the way of working that goes with it: what the system does is
written down in `openspec/`, every scenario of that spec is a test, and the work lives in a ledger
on [gonk](https://github.com/ikigai-rs/ikigai-gonk), claimed before anyone writes. It was generated
by [ikigai-gonk-starter](https://github.com/ikigai-rs/ikigai-gonk-starter)'s `tools/new.py`, and
[`AGENTS.md`](AGENTS.md) holds the rules every agent and every person here works by.

## What is in it

```text
AGENTS.md                the rules (CLAUDE.md imports it)
openspec/specs/          what the library does now: catalog, members, loans, renewals, overdue
openspec/changes/        two changes in flight: cap-late-fees (small), add-holds (both artifacts)
openspec/vectors.json    every scenario of the specs, as steps any language's tests run
artifacts/service/       the rules (lending/), an HTTP JSON API over them, and serve.go
artifacts/cli/           a command-line client of that API
tests/                   the check that the specs and the vectors name the same scenarios
.gonk/queries/           saved SPARQL over the ledger and the spec graph
.gonk/scripts/           a workflow script: next, for one artifact
tools/gonk.py            init, seed, file, next, claim, release, report, close, status
tools/worker.py          the worker loop, for a machine that takes items unattended
worker/                  its configuration template, and its launchd and systemd units
```

**Go**, standard library only: `net/http` is the HTTP server and the client, `encoding/json` the
JSON, and `go.mod` lists no dependencies — nothing fetched from a registry, so `go test` needs no
network and no `go.sum`. `go` is the only tool the tests need.

## The rules, briefly

A member borrows a copy for 21 days and holds at most three loans. A loan is renewed from its due
date, twice at most, and never once it is overdue. A loan is overdue from the day after its due
date; a member with an overdue loan cannot borrow, and a late return owes 25 cents a day. **Time is
an input**: no rule reads a clock, and the service dates every request with the clock it was given,
so a test gets the same due dates on any day it runs.

## Try the sample

```sh
python3 scripts/test.py                                       # every test, as CI runs them

go run ./artifacts/service --today 2026-10-01 &               # the clock pinned
go run ./artifacts/cli add-title 9780141439518 "Pride and Prejudice" "Jane Austen"
go run ./artifacts/cli add-copy pp-1 9780141439518
go run ./artifacts/cli add-member ada "Ada Lovelace"
go run ./artifacts/cli lend ada pp-1                          # lent pp-1 to ada, due 2026-10-22
go run ./artifacts/cli loans ada
kill %1                                                       # stop that service
```

The tests (`scripts/test.py`) are one `go test ./...`; they bind a loopback port for the real
service and read `openspec/vectors.json`, and need nothing else.

## Work it with gonk

Point this repository at a gonk and a ledger, file the changes in flight as items, and take one:

```sh
python3 tools/gonk.py init --ledger library --socket ~/.ikigai/gonk.sock --holder me
python3 tools/gonk.py seed          # one item per open task, keyed by the task; again is a no-op
python3 tools/gonk.py next          # what is ready, ranked, and why
python3 tools/gonk.py claim 1       # a 30-minute lease on item 1
```

`seed` files each unchecked task of `openspec/changes/<id>/tasks.md` as an item, labeled
`change:<id>` and `artifact:<name>` for each artifact the change's proposal names, _about_ the task
and the requirement its parentheses cite, and links each task to the next with `blocks`: `next`
offers the first open task of each change, and closing it makes the next one ready. Then the loop in
`AGENTS.md`: reproduce, write the scenario's vector first (it fails), change the code until it
passes, open a pull request, check the task's box, and report with the pull request as evidence.

`cap-late-fees` is the small change: one requirement modified, one artifact. `add-holds` adds a
capability and touches both artifacts, so its items show up under either artifact:

```sh
python3 tools/gonk.py init          # once gonk offers urn:script:, this publishes the queries
ikigai --mount urn:gk:=$HOME/.ikigai/gonk.sock \
  -c 'source urn:gk:script:library-open-items-for-artifact:result artifact=cli'
```

An unattended machine can take items too: `tools/worker.py` claims what `next` offers under a label,
runs an agent on it in a fresh worktree, and hands it back with the pull request as evidence
(`worker/worker.sample.toml` is its configuration, and `just worker-plan` shows what it would take).
Setting up the machine it runs on, as its own account, is the starter's
[worker README](https://github.com/ikigai-rs/ikigai-gonk-starter/blob/main/worker/README.md).

## The spec is the tests

`openspec/vectors.json` holds every `#### Scenario:` in `openspec/specs/` as a list of steps: an
operation, its arguments, the date it happens on, and the whole answer expected (or the refusal).
`artifacts/service/lending/vectors_test.go` runs every one against the rules and again over HTTP, and
`tests/test_specs_and_vectors.py` fails if a scenario has no vector or a vector names no scenario.
The file is language-neutral on purpose: a service written in another language runs the same file,
and cannot quietly disagree with the spec.

To change behavior, start with the spec: a change folder under `openspec/changes/`, its scenarios,
then their vectors, then the code. When the change is done it is archived into `openspec/specs/`.
