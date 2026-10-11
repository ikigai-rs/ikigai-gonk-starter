# .gonk/scripts/

Workflow scripts, one file each. A `.plan.ttl` file is a plan (an `ik:Process` graph,
[ikigai-script](https://github.com/ikigai-rs/ikigai-script)'s `plan` language): a fixed set of
requests whose authority is derived from what they name, checked before anything runs.

| script | does |
| --- | --- |
| `next-for-artifact.plan.ttl` | the ledger's own `next`, narrowed to one artifact's items (`labels=artifact:service`) |

Like a saved query, a script names its ledger as `LEDGER`, and `python3 tools/gonk.py init`
publishes it as `urn:script:<ledger>-<name>` with `LEDGER` replaced, when gonk offers
`urn:script:`:

```sh
ikigai --mount urn:gk:=<socket> -c 'source urn:gk:script:<ledger>-next-for-artifact:result labels=artifact:cli'
```

Until gonk offers it, `init` says the script is ready but unpublished, and it stays a file.
