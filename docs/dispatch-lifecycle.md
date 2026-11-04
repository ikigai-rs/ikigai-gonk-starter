# Design: the dispatch flow as a declared ledger lifecycle

Design note for ledger item 2948, written before any code. It says what the lifecycle is, who may
make each move, what replaces the awaiting flag and its watcher, how `tools/gonk.py` and
`tools/worker.py` move items, how items in flight migrate, and which items follow. Nothing here is
built yet. Behavior changes still start as an openspec change (the first follow-on item, S1).

**Three findings change the brief.** Each was observed on ikigai-ledger 0.18.0; see "What was
checked".

1. ⚠ **0.18.0 is not enough: a worker cannot move state.** The state Sink needs `write:{ledger}`,
   and the worker grant is read, claim and comment. A declared move also names no principal. So
   "which principal may make each move" needs a ledger change first (follow-on L1).
2. ⚠ **`next state=handed-back` is empty for an item flagged `awaiting`.** `next` sorts an awaiting
   item into `excluded` before the state filter runs. The state must replace the flag, not sit
   beside it.
3. ⚠ **An unfiltered `next` offers every parked state.** Only `awaiting` keeps a handed-back item
   from being offered today. Once the flag goes, each reader must ask for the states it wants.

## The problem, as far as it reproduces

| Claim in the item | Found |
| --- | --- |
| A stopped run and a real hand-back look alike | **Partly.** On the ledger they differ: a stopped run posts no `handback=true`, so `awaiting` stays null (`tools/worker.py:1992`, against `:2095`). They look alike in everything else: both release the claim, both leave a comment saying the worker hands or handed the item back. A reader of comment text or claim events cannot tell them apart |
| Three false alarms from the hub's watcher | **Not reproduced here.** `watch_awaiting.py` is not in this repository, and a code search of the organization did not find it. Which signal it read is unknown |
| A backwards `blocks` link silently held an item | **True, and a lifecycle does not prevent it.** `dispatch` refuses a blocked item, so a link written before the dispatch is caught, and one written after is silent: the worker reads only `next`'s ready rows. The lifecycle makes it visible, because `next state=ready` lists the item as `blocked` (P6). Direction itself stays unchecked |

One more thing surfaced. A stopped item today is ready again at once, so another loop in the pool
can take it with no decision by anyone (`worker/README.md`, "A retry can land elsewhere").

## The lifecycle

The name is `dispatch`, version 1. Claim and state stay separate facts: the claim says **who** and
carries the lease, and the state says **where**.

| State | In flight | Drain | Means | Waiting for |
| --- | --- | --- | --- | --- |
| `filed` | no | `work` | not routed; holds no `ledger:state` | the hub, or a person who takes it |
| `ready` | no | `work` | a router dispatched it | a worker loop |
| `claimed` | yes | | someone is working it | its holder |
| `handed-back` | no | `review` | finished, with evidence | the hub to check the evidence |
| `reviewed` | no | `merge` | the evidence holds | the merge, and for the hub's own work a person's word |
| `merged` | no | `close` | the pull request landed | the close |
| `sent-back` | no | `work` | the evidence did not hold | the hub to dispatch it again |
| `stopped` | no | `work` | a run ended with no evidence | the hub to dispatch it again |

Why each decision:

- **`claimed` is a real state, not derived from the claim.** The doctor reads it. `orphaned` is a
  machine's claim on an item not in flight, so today's worker claims on `filed` items report as
  orphaned (P7), and `abandoned` is an in-flight state nobody holds. With `claimed`, both checks
  mean what they say. A crashed run becomes `lease-expired` or `abandoned`.
- **Every evidence-less ending is `stopped`:** shutdown, budget overrun, a failed step, an agent
  that exited with no pull request and no report. The comment says which. Splitting them adds
  states nobody routes differently.
- **A takeover is not a move.** The holder changes and the state stays `claimed`.
- **`close` stays orthogonal,** as the ledger defines it. A close with no change (`--reason
  wontfix`) leaves the item in `reviewed`, and `merged` means only that a pull request landed.
- **A closed item must not be left in `claimed`.** `abandoned` has no open-items filter, so it
  reports one (P7). `gonk.py close` moves an in-flight item out first.

### The declaration

This parses under `Lifecycle::parse` and behaves as stated on 0.18.0 (P1, P2):

```turtle
@prefix ledger: <https://ikigai-rs.dev/ns/ledger#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix s: <urn:iki:ledger:lifecycle:dispatch:> .

<urn:iki:ledger:lifecycle:dispatch> a ledger:Lifecycle ;
    owl:versionInfo "1" ;
    ledger:hasState s:filed, s:ready, s:claimed, s:handed-back, s:reviewed, s:merged,
                    s:sent-back, s:stopped .

s:filed a ledger:LifecycleState ; rdfs:label "filed" ; ledger:order 0 ;
    ledger:inFlight false ; ledger:drain "work" ;
    ledger:movesTo s:ready, s:claimed ,
                   s:handed-back, s:sent-back, s:stopped .   # the last three: migration, version 1 only
s:ready a ledger:LifecycleState ; rdfs:label "ready" ; ledger:order 1 ;
    ledger:inFlight false ; ledger:drain "work" ;
    ledger:movesTo s:claimed, s:filed .
s:claimed a ledger:LifecycleState ; rdfs:label "claimed" ; ledger:order 2 ;
    ledger:inFlight true ;
    ledger:movesTo s:handed-back, s:stopped .
s:handed-back a ledger:LifecycleState ; rdfs:label "handed-back" ; ledger:order 3 ;
    ledger:inFlight false ; ledger:drain "review" ;
    ledger:movesTo s:reviewed, s:sent-back .
s:reviewed a ledger:LifecycleState ; rdfs:label "reviewed" ; ledger:order 4 ;
    ledger:inFlight false ; ledger:drain "merge" ;
    ledger:movesTo s:merged, s:sent-back .
s:merged a ledger:LifecycleState ; rdfs:label "merged" ; ledger:order 5 ;
    ledger:inFlight false ; ledger:drain "close" ;
    ledger:movesTo s:sent-back .
s:sent-back a ledger:LifecycleState ; rdfs:label "sent-back" ; ledger:order 6 ;
    ledger:inFlight false ; ledger:drain "work" ;
    ledger:movesTo s:ready, s:claimed .
s:stopped a ledger:LifecycleState ; rdfs:label "stopped" ; ledger:order 7 ;
    ledger:inFlight false ; ledger:drain "work" ;
    ledger:movesTo s:ready, s:claimed .
```

## The moves, and who may make each

"Worker" means the holder of the item's live claim, under the worker grant. "Hub" means a principal
holding `write:{ledger}`. Brian is assumed to hold the same grant, which was not checked.

| From | To | Worker | Hub | Brian | Made by | Why |
| --- | --- | --- | --- | --- | --- | --- |
| `filed` | `ready` | no | yes | yes | `gonk.py dispatch` | routed |
| `filed` | `claimed` | no | yes | yes | `gonk.py claim` | a person takes it with no dispatch |
| `ready` | `claimed` | holder | yes | yes | `worker.py`, `gonk.py claim` | taken |
| `ready` | `filed` | no | yes | yes | `gonk.py dispatch --cancel` | routed by mistake |
| `claimed` | `handed-back` | holder | yes | yes | `worker.py`, `gonk.py report` | finished, with evidence |
| `claimed` | `stopped` | holder | yes | yes | `worker.py`, `gonk.py release` | ended with no evidence; the hub after a dead machine |
| `handed-back` | `reviewed` | no | yes | yes | `gonk.py accept` | the evidence holds |
| `handed-back` | `sent-back` | no | yes | yes | `gonk.py reject` | it does not |
| `reviewed` | `merged` | no | yes | yes | `gonk.py close`, the hub's close | the pull request landed |
| `reviewed` | `sent-back` | no | yes | yes | `gonk.py reject` | the merge failed, or a person said no |
| `merged` | `sent-back` | no | yes | yes | `gonk.py reject` | reverted, or reopened |
| `sent-back` | `ready` | no | yes | yes | `gonk.py dispatch` | try again |
| `sent-back` | `claimed` | no | yes | yes | `gonk.py claim` | a person takes it over |
| `stopped` | `ready` | no | yes | yes | `gonk.py dispatch` | try again |
| `stopped` | `claimed` | no | yes | yes | `gonk.py claim` | a person takes it over |

Three things follow:

- **The worker makes exactly three moves, all while holding the claim.** The authority is the claim
  itself, which the ledger already ties to a principal. Holding one item's claim grants nothing on
  any other.
- **No move is Brian's alone in the ledger.** The hub and Brian hold the same grant, and a lifecycle
  cannot tell them apart. Two things stay reserved elsewhere: the hub asking a person before merging
  its own work (a rule, `profiles/sections/hub.md`, with branch protection behind it), and changing
  the lifecycle document (host configuration and a restart).
- **A move is not a dispatch.** A worker still takes an item only on a router's newest dispatch with
  a matching digest. `ready` says the hub wants it run. It does not replace the check that protects
  the brief from an unattributed edit.

### What the ledger must add (L1)

0.18.0 enforces which moves exist and not who makes them. The proposal is one term:

```turtle
s:claimed ledger:holderMovesTo s:handed-back, s:stopped .   # a sub-property of ledger:movesTo
s:ready   ledger:holderMovesTo s:claimed .
```

The state Sink accepts `write:{ledger}` for any declared move, and `claim:{ledger}` for a
`holderMovesTo` move when the caller's principal holds the item's claim, checked in the
compare-and-set. Anyone else gets `Denied`.

Enrolling workers with `write` instead is rejected. It works on 0.18.0 and lets any agent close,
relabel and edit, which is the gap `worker/README.md` already records under "The grant is wider
than the worker".

## What replaces awaiting and the watcher

| Today | Then |
| --- | --- |
| `comment handback=true`, sets `ledger:awaiting` | keyed comment, then `claimed` to `handed-back` |
| `comment rejects=true` | keyed comment, then `handed-back` to `sent-back` |
| `awaiting_dispatcher(item)` with its text fallback | `item.state` |
| `watch_awaiting.py` inferring from the flag or comments | `next state=handed-back` |
| doctor's `awaiting-stale` | the state's `since`, read by the hub's view; a doctor check later if wanted (L2) |
| `attempts.json` and `worker.py forget` | nothing: a worker only reads `ready`, and only the hub moves an item back there |

**The review queue is `source …:next state=handed-back as=application/json`, reading both `ranking`
and `excluded`.** A blocked hand-back and one whose release failed appear only in `excluded` (P6,
P7), so a reader of `ranking` alone would miss them.

**This is still a read on a schedule.** The ledger's documentation names no subscription. What goes
away is the inference: a restart makes items `stopped`, and no stopped item can enter this queue.
Stopped runs get a query of their own and a different message:

| Asker | Query | Answers |
| --- | --- | --- |
| Worker loop | `next labels=… state=ready` | what may I take |
| Hub, review | `next state=handed-back` | what evidence needs checking |
| Hub, or Brian, merging | `next state=reviewed` | what is ready to merge |
| Hub, unfinished runs | `next state=stopped`, `next state=sent-back` | what to dispatch again |
| Hub, stuck routing | `excluded` of `next state=ready` with `why: blocked` | a dispatched item that nothing will take |
| Person, `gonk.py next` | `next drain=work` | what could I start |

The last line keeps `gonk.py next` from offering parked work (finding 3).

## How the tools move items

Each write can fail alone, so the order is what keeps every crash safe. `content=` on the state
Sink is **not** atomic with the move: the comment is a second write after the compare-and-set
(ikigai-ledger's `src/endpoints/flight.rs`). So the evidence goes first, as a keyed comment.

**`worker.py`:**

| Step | Writes, in order | If it fails partway |
| --- | --- | --- |
| Take | claim; read again and check the dispatch; `ready` to `claimed` | claim taken, move failed: release and back off, the item stays `ready` |
| Hand back | keyed evidence comment; `claimed` to `handed-back`; release `from=` holder | after the comment: the lease runs out and the hub sees `lease-expired`. After the move: it is in the queue, as `excluded: claimed` |
| Stop | note comment; `claimed` to `stopped`; release `from=` holder | same |
| Lost claim | a comment, no move | it is no longer this worker's to move |

The compare-and-set on the claim still decides between two loops in a pool. The loser moves nothing.

A `Conflict` naming the target state counts as success, as gonk's kata importer already treats it,
so a retried hand-back is safe (P8).

**`gonk.py`:** `report`, `release`, `reject`, `claim` and `dispatch` make the moves in the table.
`dispatch` moves last, so a `ready` item always has its dispatch, and it refuses `claimed`,
`handed-back`, `reviewed` and `merged` by state. `accept` is new. `status` prints the state.
`close` walks forward from wherever the item stands, each step a compare-and-set, so an interrupted
close resumes. With a pull request it ends at `merged`, joining the check in PR #67 (open at the
time of writing). A dispatcher's `release` of someone's live claim also moves `claimed` to
`stopped`, otherwise the item reads as `abandoned`.

**Both follow the ledger, not a flag in the repository.** The doctor's JSON and an item's state JSON
name the ledger's lifecycle (P8). On `dispatch` the tools use the states. On anything else, such as
a generated repository on `kata-flight`, they do what they do today, and that path stays for as long
as the starter generates repositories that do not assign the lifecycle. So one host setting flips
every loop at once, with no second restart.

**The harness follows.** `worker-skips-awaiting-dispatcher` and `worker-skips-handed-back` stop
firing on `dispatch` ledgers, and an undeclared move gets a sensor of its own.

## Resource map

No new atom. The state, the claim and the comment already hold everything.

| Set | Name | Kind | How |
| --- | --- | --- | --- |
| lifecycle | `urn:iki:ledger:lifecycle:dispatch` | document | Turtle above, assigned with `gonk.ledger.lifecycle = "default <path>"` at boot |
| item state | `urn:iki:ledger:default:item:{id}:state` | ATOM (exists) | Sink is the compare-and-set; gains the holder's authority (L1) |
| claim | `urn:iki:ledger:default:claim` | ATOM (exists) | unchanged: who, and the lease |
| evidence | `urn:iki:ledger:default:comment` | ATOM (exists) | keyed, so a retry posts once |
| a step's queue | `…:next state={state}`, `…:next drain={drain}` | composition (exists) | no labels, no code |
| review queue | `…:next state=handed-back` | exact use of the above | replaces the watcher's inference |

Refusals: an undeclared move or a stale `from=` is `Conflict`, a state outside the lifecycle is
`InvalidArgument`, and a worker moving what it does not hold is `Denied` (new, L1).

Spaces: none new. A lifecycle is one per ledger and fixed by the host. **So it covers every item in
`default`,** including items from other repositories and standing status items, which stay `filed`.

Code that remains, and why it cannot be a composition: the order of writes in the tools, because
it spans three resources; the migration's classifier, because it reads comment text once, and that
is the thing being retired.

## Migrating items in flight

**The order matters.** A worker on the old tools uses an unfiltered `next`, so it would take a
handed-back item that no longer carries `awaiting` (finding 3). All loops must be on the new tools
before the lifecycle is assigned.

1. **Census.** Count `ledger:state` values and open claims in `default`. An item holding a
   `kata-flight` state becomes `unknown-state` under `dispatch`, cannot be moved, and is left out of
   every filter while unfiltered `next` still offers it (P10). It takes one SPARQL update each, and
   its mapping is a decision the census informs. gonk's kata importer also stops working on this
   ledger, since `to=queued` is refused (P10).
2. **Ledger and gonk.** Release L1, and roll gonk onto it.
3. **Tools, dormant.** Release S2 and S3, then pull and restart every loop. They read `kata-flight`
   and behave as today.
4. **Quiet.** Hold new dispatches, and let running items finish within their budget.
5. **Assign.** Set `gonk.ledger.lifecycle` and restart gonk. The ledger now reports `dispatch`, and
   every tool switches together. Items are all `filed` for the moment, and workers take nothing
   until step 6.
6. **Sweep.** Run the migration with `--dry-run`, read it, then run it. It is idempotent, so a run
   that ended during the sweep is caught by running it again.
7. **Watcher.** Point the hub at the queue and retire `watch_awaiting.py`.
8. **Version 2.** Drop the three migration moves and restart gonk. Each state event keeps the
   version it was checked against, so version 1 marks the items migrated (P8).

The sweep classifies each open item, and the first match wins:

| Item | Move | Then |
| --- | --- | --- |
| holds a `kata-flight` state | none | named in the report for its repair |
| `awaiting: dispatcher` | `filed` to `handed-back` | a hub comment to clear the flag |
| any claim, live or expired | `filed` to `claimed` | the holder and lease are untouched |
| no claim; newest worker note says stopped, failed or no evidence; no router dispatch since | `filed` to `stopped` | |
| no claim; an `Evidence:` hand-back, the flag cleared, no router dispatch since | `filed` to `sent-back` | |
| newest router dispatch matches the item | `filed` to `ready` | |
| anything else, status items included | none | stays `filed` |

⚠ **Clear the flag with a separate hub comment.** The move's own note does not clear `awaiting`
(P5), and a worker's comment cannot (P9). Move first and clear second, so the item is excluded from
offers throughout.

**Rollback is not free.** Unassigning the lifecycle leaves items holding `dispatch` states, which
then read as `unknown-state`, and `handed-back` items are offered as ready because the flag is gone.
To undo it, reassert `handback=true` on those items, using the sweep's report. So keep that report.

## Follow-on items

The worker that wrote this may not file items, so these are for the dispatcher. The order is written
as "X blocks Y" throughout. A backwards link is one of the failures this design started from, so
check each link's direction with `next` after writing it.

**ikigai-ledger:**

- **L1. Let a claim's holder make declared moves** (`ledger:holderMovesTo`, above). Blocks S2, S3.
- **L2. Doctor: report a parked state waiting too long.** The replacement for `awaiting-stale`.
  Optional, blocks nothing.

**ikigai-gonk:**

- **G1. Roll onto L1's ledger release.** Blocked by L1, blocks M1.
- **G2. Decide the kata importer on a `dispatch` ledger.** Refuse with a sentence or map its labels.
  Needed only if the census or future imports say so.

**ikigai-gonk-starter:**

- **S1. Propose the change:** `openspec/changes/dispatch-lifecycle/`, its deltas to `worker-dispatch`,
  and the Turtle as a file carried by the `workers` profile. Blocks S2, S3, S4.
- **S2. Move items from `gonk.py`:** the verbs above, `accept`, `next drain=work`. Blocked by S1 and
  L1. Blocks M1. Depends on where PR #67 lands.
- **S3. Move items from `worker.py`:** `state=ready`, the write orders, the harness sensors. Blocked
  by S1 and L1. Blocks M1.
- **S4. Add the migration sweep** to the hub profile, with `--dry-run` and its report. Blocked by S1.
  Blocks M1.
- **S5. Rewrite the rules:** `AGENTS.md`, `profiles/sections/workers.md` and `worker/README.md`,
  including the awaiting wording. Ships with S2 and S3.
- **S6. Retire on `dispatch` ledgers:** `attempts.json`, `forget` and the text fallback. Blocked by M1.

**The hub and operations:**

- **M1. Run the migration:** census, assign, sweep, on `default`. Blocked by G1, S2, S3, S4.
- **H1. Add the review view and retire `watch_awaiting.py`,** with a separate message for `stopped`
  and an alarm for a `blocked` row under `state=ready`. Blocked by M1. Lives wherever the watcher
  does, which is not this repository.
- **M2. Publish version 2:** drop the migration moves. Blocked by M1, after a sweep reports nothing
  left to move.

## Decisions for Brian

1. **Who makes `stopped` to `ready`?** Recommended: the hub only, by `dispatch`. That stops a budget
   overrun from retrying forever, and a run's partial branch gets a decision. It costs one dispatch
   per item after a fleet restart, and it ends today's silent retry in the pool. If restarts prove
   too noisy, the hub can dispatch again automatically where the note says shutdown.
2. **Should `dispatch` cover all of `default`?** Recommended: yes, since a person's filed items stay
   `filed` and behave as before. The census may change this, and a separate ledger for routed work
   is the alternative.
3. **Is L1 acceptable as a precondition?** It adds a release before anything can move, on top of
   default#2905. There is no safe interim: with `write` the grant is too wide, and a hub process
   copying flags into states brings the polling and the inference back.

## What was checked

Probes against the published `ikigai-ledger` 0.18.0 with the Turtle above assigned to `default`, on
an in-memory store, in a scratch crate outside this repository. The source is in the pull request.

| | Probe | Observed |
| --- | --- | --- |
| P1 | parse and read the lifecycle | parses; hyphenated names work; the plain face lists every move |
| P2 | `filed` to `merged`, `ready` to `handed-back` | `Conflict`, naming the legal moves |
| P3 | move under the worker grant, and under read, claim, comment and close | the claim succeeds; both moves `Denied` for `urn:cap:ledger:write:*` |
| P4 | `handed-back` with and without `awaiting` | with: only in `excluded`, as `awaiting-dispatcher`. Without: ranked, **and offered by unfiltered `next`**, not by `drain=work` |
| P5 | a move's note on a flagged item | the move happens; `awaiting` stays set |
| P6 | a `blocks` link against a `ready` item, then handed back | `excluded`, `why: blocked`, in both `state=ready` and `state=handed-back` |
| P7 | the doctor | a claim on `filed`: `orphaned`. Closed in `claimed`: `abandoned`. A held claim after moving to `handed-back`: `orphaned`, and `excluded: claimed` in the queue |
| P8 | replay, and what the state reports | a replay is a `Conflict` naming the state; `from=X to=X` is `unchanged`; JSON gives `lifecycle`, `drain`, `since`; history gives the version |
| P9 | clearing the flag | `rejects=true` and a comment under `write` clear it; a worker's comment does not, and the item then ranks |
| P10 | an item holding a `kata-flight` state | `unknown-state`; both moves refused; offered unfiltered, not by `drain=work`; importer's `to=queued` is `InvalidArgument` |
| P11 | the take, under the worker grant | the claim succeeds; `ready` to `claimed` is `Denied`; the claimed item is `excluded` from `state=ready` |

In this repository, `tests/test_worker_loop.py`'s
`test_a_stopped_worker_hands_the_item_back_with_a_note` passes, and pins a stopped run's released
claim and its "without evidence" note.

**Not checked:**

- Nothing ran on a real gonk, or against the live ledger: this tree has no ledger configured, so the
  census has not been run and no in-flight counts are given.
- `watch_awaiting.py` was not read, so the account of the false alarms rests on the item.
- default#2905 was not checked. ikigai-gonk's `main` has a commit rolling out ledger 0.18.0; what the
  deployed gonk runs is unknown.
- `ledger:holderMovesTo` does not exist, so L1's behavior is a proposal, tested by nothing.
- gonk's web pages were not read for assumptions about `kata-flight` states.
