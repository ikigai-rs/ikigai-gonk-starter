## ADDED Requirements

### Requirement: One line per tool call
Each tool call the Claude Code hook or the worker sees end MUST be written as one
`log:ToolCall` line of an ikigai-log v1 segment, exactly as ikigai-log 0.14.0 renders it: the
subject `urn:log:tool:name:{name}`, then `seq=`, `session=` the session's IRI, `outcome=`
(`ok`, `refused` or `failed`), `dur=` in milliseconds when known, `args=` the SHA-256 of the
arguments, and `result=` the SHA-256 of the result when one was handed over. Writing MUST be
best effort: a line that cannot land changes nothing the hook or the worker decides.

#### Scenario: A call the hook sees
- **WHEN** Claude Code runs a tool with the hook wired, and the tool answers
- **THEN** the tree's `.gonk/state/trail/tools.log` gains one line with `outcome=ok`, `dur=`
  and the session's `urn:claude:session:` IRI

#### Scenario: A call the claim hook refuses
- **WHEN** `require-claim.py` refuses a call
- **THEN** the trail gains one line for it with `outcome=refused`

#### Scenario: A call the worker sees
- **WHEN** a worker's Claude Code or Codex agent calls a tool and its result comes back
- **THEN** the checkout's `.gonk/state/worker/trail/tools.log` gains one line for it

### Requirement: No argument text by default
A line MUST NOT carry the arguments' or the result's text unless the operator opted in for
the whole trail (`[trail] text = true` in `.gonk/local.toml` for the hook, in the worker's
configuration for the worker). An opted-in line MUST carry them as `args-text=` and
`result-text=`, cut at 4096 bytes with the cut named in `cut=`.

#### Scenario: The default
- **WHEN** no opt-in is configured
- **THEN** a line holds the arguments' digest and none of their text

#### Scenario: Opted in
- **WHEN** the operator set `[trail] text = true`
- **THEN** each line also holds `args-text=` with the arguments

### Requirement: One session's lines read back in order
Every line MUST carry its session's IRI in `session=`, and `seq=` MUST be dense within the
segment, so one session's lines read back in the order they were written.

#### Scenario: Two sessions in one trail
- **WHEN** calls of two sessions are written to one trail, interleaved
- **THEN** reading one session's lines answers only its own, in `seq` order
