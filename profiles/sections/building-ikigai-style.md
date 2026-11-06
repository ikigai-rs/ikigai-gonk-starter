## Building in the ikigai style (optional)

If this repository builds on ikigai, two guides describe how we design and present it.
Neither is required, and a project that is not resource-oriented can ignore both:

- **Resource-first design**, `docs/resource-design.md`: names first and code last, with a
  worked example. If you use it, a `## Resource map` in the change's `proposal.md` (or the pull
  request) is the place to show the result.
- **UI conventions**, `docs/ui-conventions.md`: how an ikigai view is made, escaped, served,
  themed and tested. The record is decided; its shared pieces are not built yet. Whatever
  you build, `ACCESSIBILITY.md` stays the floor.
- **Faces**, `faces/README.md`: a deployment's own pages, served by gonk from a directory
  (`gonk.face = "<name> <dir>"`, ikigai-gonk e08ac9f or later) and reading only gonk's JSON
  routes as the viewer. `faces/template/` is the one to copy; `board`, `flight`, `project`
  and `claims` are samples. `python3 tools/gonk.py init --face <name>` (or
  `tools/new.py <dir> --face <name>`) prints the line for gonk's `config.toml`. No inline
  script or style: `tests/test_faces.py` checks it.
