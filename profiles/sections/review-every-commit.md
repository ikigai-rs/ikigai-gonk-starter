## Review on every commit

Every commit asks gonk to review the files it touched. A reviewer reads them, read-only, and
writes **pending findings**; a person triages them, and a finding worth acting on becomes a
ledger item.

- **The hook** is `hooks/git/post-commit`, installed with `git config core.hooksPath hooks/git`
  (`just hooks`). It drops one review request per changed file into gonk's review queue and
  returns at once: a commit never waits on gonk or on a model, and never fails because of
  either.
- **What it needs on gonk**, without which a request waits forever: this repository served as
  a browse root (`gonk.browse.root = "<name>=<path>"`, the name in `.gonk/process.toml`'s
  `[review]`, by default this directory's name), a model mounted (`gonk.mount`), and review
  armed (`gonk.review.space`, `gonk.review.grant` and `gonk.review.arm = true`).
  `python3 tools/gonk.py init` checks each one it can see.
- **On gonk's machine only.** A request is dropped into a queue on gonk's own machine, which
  no network door reaches, so a working tree that reaches gonk over QUIC queues nothing; the
  hook says so and the commit goes ahead.
- **A finding is a claim** (rule 3): reproduce it before acting on it. Triage happens in
  gonk's Queue; file each finding you will act on as one item, keyed by the finding
  (`python3 tools/gonk.py file "<title>" --key <finding IRI>`), so filing it twice files once.
- **Nothing is published except by a person.** The reviewer cannot publish a finding; a
  person decides each one in the Queue.
- **Another reviewer can take gonk's place.** A per-commit reviewer of your choosing, such as
  roborev, can file its findings as ledger items instead, through gonk's own bridge for it
  (`ikigai-gonk roborev file`, in gonk's README). This process needs no such tool, and nothing
  in this repository reads one's reports.
