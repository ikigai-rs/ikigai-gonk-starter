## Design resource-first

- **Before designing a new feature, endpoint family or app, do the resource-first pass** and
  put its result, a `## Resource map`, in the spec change's `proposal.md`, or in the pull
  request when there is no spec.
- **Names first, code last.** The sets and their names, then the few atoms that hold state,
  then how everything else is composed from other names. Code that only assembles other
  resources must say why a composition cannot do it.
- The full method, with a worked example, is in `docs/resource-design.md`.
