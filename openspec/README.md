# openspec/

What this system is supposed to do, written where the code is, in
[OpenSpec](https://github.com/Fission-AI/OpenSpec)'s layout (1.x). It is the default spec
format here; `.gonk/layout.toml` declares it as a `[[spec]]` entry, so another format can
sit beside it later with its own mapping.

```text
openspec/
├── config.yaml     OpenSpec's project file
├── specs/          what the system does NOW, one folder per capability: specs/<capability>/spec.md
└── changes/        what someone PROPOSES, one folder per change: proposal.md, specs/…, tasks.md
    └── archive/    finished changes, moved here intact by `openspec archive`
```

The starter keeps requirements for its own tooling here; the library-lending sample fills
both halves for its lending behavior. Nothing here
needs OpenSpec's own CLI: the format is Markdown with fixed heading prefixes
(`### Requirement:`, `#### Scenario:`), and the
[gonk Book's spec part](https://ikigai-rs.github.io/ikigai-tutorial/gonk/spec/openspec.html)
teaches it, lifts it to a graph and turns a change's tasks into ledger items.
`python3 tools/gonk.py seed` does the last part without a graph: one item per open task,
keyed by the task's IRI, as the Book's sync does.

## Two conventions that are ours, not OpenSpec's

- **Artifacts.** A spec names the artifacts that implement it, and a change names the
  artifacts it touches, on one line that starts with `Artifacts:` and lists each name in
  backticks. In a spec it goes in `## Purpose`; in a change, in `proposal.md`'s `## Impact`:

  ```markdown
  ## Impact
  - Artifacts: `service`, `cli`
  ```

  Each name is a directory under `artifacts/`. `python3 tools/gonk.py init` refuses a name
  with no directory. OpenSpec reads the line as ordinary prose.
- **A task names its requirement.** A task that ends in a requirement's name in parentheses,
  `- [ ] 1.2 Return the turn (Players alternate)`, says which requirement it implements, so it
  can become a ledger item *about* that requirement (the gonk Book's convention).
