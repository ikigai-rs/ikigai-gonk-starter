# profiles/

How a team works, as data: `tools/new.py --process <name>[,<name>…]` generates a repository
with these profiles. The README's *Choosing a process* says what each one is for; this file
says what a profile is made of.

```text
profiles.toml            the default process, every profile in order, every AGENTS.md section
                         in order, and the base (what every repository gets)
sections/<name>.md       one AGENTS.md section each
harness/<name>.ttl       the harness controls of that section: its rules, and the sensors that
                         enforce them (optional, one per section)
<profile>/profile.toml   what the profile adds (below)
<profile>/files/         files it adds to the repository, at the same paths (optional)
<profile>/justfile.recipes   recipes it adds to the justfile (optional)
<profile>/process.toml   settings it adds to the repository's .gonk/process.toml, verbatim (optional)
```

A `profile.toml`:

```toml
summary = "one line: what changes with this profile"
roles = ["who: what they do"]             # printed by `tools/gonk.py init`
sections = ["roles-workers", "workers"]   # AGENTS.md sections it adds, from sections/
hooks = ["git:post-commit"]               # hooks it installs, by id
checks = ["worker-config"]                # what `tools/gonk.py init` checks for it, by id
starter_files = ["tools/worker.py"]       # this starter's own files it carries
starter_dirs = ["worker/units"]           # ... and directories
recipes = ["worker"]                      # this starter's justfile recipes it carries

[conflicts]
review-every-commit = "why the two cannot be combined"
```

Every key but `summary` is optional. A hook or a check is an id from a fixed set in
`tools/new.py` (`HOOKS`, `CHECKS`), so a misspelled one is refused by the test that checks
this directory (`tests/test_profiles.py`, `new.profile_problems()`), never silently ignored.

**How a repository is assembled.** `--process` takes a list, in any order, and each profile
is applied in `profiles.toml`'s order. `AGENTS.md` is the base sections and the chosen
profiles' sections, in `profiles.toml`'s section order, joined by one blank line, so a rule a
process does not use is not in it. The justfile is this starter's recipes, without the
starter's own and without those a profile not chosen carries, then the chosen profiles'
`justfile.recipes`, then the target's. `.gonk/process.toml` records the profiles, the hooks
and checks they ask for, each one's summary and roles, then each one's `process.toml`
settings; `tools/gonk.py init` and `status` read it, and nothing else says what the process
is. A conflict, declared on either side, refuses the pair with its reason.

**How a profile names its controls.** A profile names no controls directly: it names
sections, and each section's harness fragment, `harness/<section>.ttl`, describes that
section's rules as guides (`guide:<id>`, with `harness:section` naming its `## ` heading) and
the sensors that enforce them (`ctl:<id>`, with `harness:enforcedAt` naming the file and
function and `harness:statedBy` the rule). `.gonk/harness/harness.ttl` is the chosen sections'
fragments in `AGENTS.md`'s order, so a repository's graph describes exactly the rules it states
and the sensors it carries. A sensor in a profile's own tool goes in that profile's section
(`workers.ttl` holds `tools/worker.py`'s). Its code names it at the refusal
(`gonk.refuse("<id>", …)`, `gonk.fire("<id>", …)` or `gonk.sensor("<id>")`), which records the
firing; `python3 tools/harness.py check` fails until the two agree, and a sensor that records
firings needs a test in `tests/test_harness.py`'s `SensorsFire` named for it, which its
`harness:firedBy` names. An id never changes once firings carry it. Terms:
`.gonk/harness/vocabulary.ttl`.

**The starter's own `AGENTS.md` and `.gonk/process.toml` are the default process**, so a
repository made by copying this starter as a template has the same rules and record as one
`new.py` generates without `--process`. So is its
`.gonk/harness/harness.ttl`. After changing a section, a fragment or a profile, run
`python3 tools/new.py --sync-starter`; a test fails until they agree.

**A new profile** is a directory here with a `profile.toml`, its name in `profiles.toml`'s
`profiles`, and any new section's name in `sections`. If it needs a hook or a check `init`
does not know, that is code: an id in `tools/new.py`, and what it means in `tools/gonk.py`'s
`process_rows` or `review_rows`.
