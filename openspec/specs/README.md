# openspec/specs/

The source of truth: how the system behaves today, one folder per **capability**.

```text
specs/<capability>/spec.md
```

A spec is `## Purpose` (with its `Artifacts:` line) and `## Requirements`. Each
`### Requirement:` states one behavior with an RFC 2119 keyword (SHALL, MUST, SHOULD, MAY)
and has at least one `#### Scenario:` written as **WHEN** / **THEN** steps a test could
check. A spec says what is observable, never how it is built.

Empty in the starter until a first change is archived into it; the library-lending sample
ships five capabilities. OpenSpec looks for `spec.md` files in
folders below this one, so this README is not read as a spec.
