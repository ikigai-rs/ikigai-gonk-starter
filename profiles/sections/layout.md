## Specs, artifacts, queries and scripts

- **Specs** are in `openspec/`: `specs/` is what the system does now, `changes/` what is
  proposed. Read the spec before changing behavior. See `openspec/README.md`.
- **Artifacts** are `artifacts/<name>/`. A spec or change names the artifacts it touches on
  an `Artifacts:` line; keep it true when you add or move code.
- **Queries and scripts** are files in `.gonk/queries/` and `.gonk/scripts/`. They are meant
  to be published to gonk as script resources (`urn:script:{name}`, from ikigai-script) and
  drafted from a sentence by `urn:nl:sparql` (ikigai-nl). `python3 tools/gonk.py init`
  publishes them when gonk offers `urn:script:`, and until then says they are ready but
  unpublished; do not publish them any other way.
