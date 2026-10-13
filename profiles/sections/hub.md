## Hub and spoke

One **hub** session coordinates, and each piece of work goes to an agent of its own.

- **One arc, one repository, one item.** The hub splits work into arcs that each touch one
  repository and files each as an item whose body is the **brief**: why, the work, the rules,
  and what to report (`.gonk/templates/brief.md`). `python3 tools/hub.py arc "<title>"
  --brief <file> [--parent N] [--after N]` files it and links it: `parent` to the item it is
  part of, and `blocks` from an arc that must land first, so `next` holds it back until then.
- **One working tree and one holder per arc.** `python3 tools/hub.py dispatch 12 ../arc-12`
  makes a worktree for item 12 and gives it a holder of its own (`arc-12`), so two agents
  never share a tree or a claim (rule 2). The agent there claims the item and works from its
  brief.
- **An arc's agent merges its own pull request on green, then reports.** That authority comes
  through the brief: a session that was not started from a brief holds none, and stops at a
  green pull request and reports.
- **The hub asks a person before merging its own work.** One line, with the pull request and
  its CI: "PR 41 is green. Merge?", then merge on the answer. A merge is the moment work
  becomes shared, and the person gets one beat to redirect it.
- **The hub closes an arc** after checking the evidence, as any dispatcher does.
