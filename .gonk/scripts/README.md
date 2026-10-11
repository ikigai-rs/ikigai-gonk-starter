# .gonk/scripts/

Workflow scripts: the steps this team runs over the ledger and the specs (sync a change's
tasks into items, a weekly report), one file each.

**Where they are going.** Like a saved query, a script is meant to be a resource on gonk,
`urn:script:{name}` from [ikigai-script](https://github.com/ikigai-rs/ikigai-script): a plan
or Lisp, published once, run with `sink urn:script:{name}:runs` for its effects, with every
run recorded (who, which version, under what authority), and never with more authority than
its publisher held. `urn:nl:script` in [ikigai-nl](https://github.com/ikigai-rs/ikigai-nl)
is the planned drafter for these; `urn:nl:sparql` drafts queries today.

`python3 tools/gonk.py init` publishes every script here when gonk offers `urn:script:`, as
`urn:script:<ledger>-<name>` with each `LEDGER` in the text replaced by the ledger's name. The
language is the file's: `<name>.plan.ttl` is a plan, `<name>.rq` a query, `<name>.scm` or
`<name>.lisp` Lisp. Until gonk offers it, `init` says they are ready but unpublished.

Empty in the starter; the library-lending sample (`just new <dir> --target python`) ships
one, `next-for-artifact.plan.ttl`.
