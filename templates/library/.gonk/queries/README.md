# .gonk/queries/

Saved SPARQL over the ledger and the spec graph, one query per `<name>.rq` file:

| query | asks | reads |
| --- | --- | --- |
| `open-items-for-artifact` | the open items that touch one artifact, by priority (`artifact=service`) | the ledger |
| `open-work-by-change` | open and total items for each change in flight | the ledger |
| `requirements-with-open-work` | each requirement with open items about it, and how many | the spec graph and the ledger |
| `harness-sensors-never-fired` | the sensors with no firing yet, whether each is wired here, and whether a test shows it can fire | the harness graph |
| `harness-guides-without-sensor` | the rules of AGENTS.md no computational control checks | the harness graph |
| `harness-sensors-without-guide` | the sensors that state no rule | the harness graph |
| `harness-firings-by-control` | how often each control refused or warned, first and last | the harness graph |

A query names its ledger as `LEDGER` (`urn:iki:ledger:graph:LEDGER`): the file is valid
SPARQL as written, and `python3 tools/gonk.py init` publishes it with `LEDGER` replaced by the
ledger `.gonk/gonk.toml` names, as the script `urn:script:<ledger>-<name>` (from
[ikigai-script](https://github.com/ikigai-rs/ikigai-script)), so one gonk can hold several
repositories' queries. A parameter is declared in the leading comment block
(`# @param artifact xsd:string`) and becomes an argument of the published query:

```sh
ikigai --mount urn:gk:=<socket> -c 'source urn:gk:script:<ledger>-open-items-for-artifact:result artifact=service as=text/csv'
```

`init` publishes only when gonk offers `urn:script:`; until it does, it says the queries are
ready but unpublished, and they stay files here. The four `harness-*` questions are never
published: they read the harness graph, which gonk's script ceiling (ledger reads only) does
not admit, so `python3 tools/gonk.py harness` loads that graph and asks them through gonk's
store. `requirements-with-open-work` also needs the
spec graph: the `openspec/` tree lifted with the mapping `.gonk/layout.toml` names, one named
graph per document (the gonk Book's spec part does that).
