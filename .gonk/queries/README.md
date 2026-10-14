# .gonk/queries/

Saved SPARQL over the ledger and the spec graph, one query per `<name>.rq` file: the
questions this team asks again (*which requirement has the most open work*, *which open
item is about a requirement a change removed*).

**Where they are going.** A saved query is a script whose language is `sparql`, and gonk can
hold it as a resource, `urn:script:{name}` from
[ikigai-script](https://github.com/ikigai-rs/ikigai-script): versioned by content, run with
`source urn:script:{name}:result` under the runner's own authority, and listed for an agent
in its tool manifold. A draft can come from a sentence: `urn:nl:sparql` from
[ikigai-nl](https://github.com/ikigai-rs/ikigai-nl) grounds a model in what the caller may
read, drafts and checks a query, and saves it as a DRAFT script for a person to publish.

`python3 tools/gonk.py init` asks gonk whether it offers `urn:script:`, and publishes every
query here when it does, as `urn:script:<ledger>-<name>` (so one gonk can hold several
repositories' queries), with each `LEDGER` in the text replaced by the ledger
`.gonk/gonk.toml` names: write `<urn:iki:ledger:graph:LEDGER>` for this repository's ledger
graph and the file stays valid SPARQL as written. Until gonk offers it, `init` says the
queries are ready but unpublished, and they stay files.

**The harness questions**, `harness-*.rq`, are in every repository: which sensors have never
fired, which rules no computational control checks, which sensors state no rule, and how often
each control fired. They read `urn:iki:harness:graph:LEDGER`, which `python3 tools/gonk.py
harness` loads (the README's *Your harness*), and that command asks them through gonk's store.
`init` does not publish them: gonk runs a script under a ceiling of ledger reads, so a
published one could only be refused.

The library-lending sample (`just new <dir> --target python`) adds three queries of its own.
