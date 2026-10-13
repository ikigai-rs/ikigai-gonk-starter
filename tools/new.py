#!/usr/bin/env python3
"""Generate a new repository from this starter, with the library-lending sample in it.

    python3 tools/new.py <dir> [--target python] [--process solo,spec]
                                                     (or: just new <dir> --target python)

The result is a repository that works the way this starter teaches: AGENTS.md and its rules,
`tools/gonk.py`, the `.gonk/` wiring, and a sample worth working on:

  openspec/specs/         what the library does now (catalog, members, loans, renewals, overdue)
  openspec/changes/       two changes in flight, ready to `seed` into ledger items
  openspec/vectors.json   every scenario of the specs as steps any language's tests run
  artifacts/service/      an HTTP JSON API over the lending rules (time is an input)
  artifacts/cli/          a command-line client of that API
  .gonk/queries/          saved SPARQL over the ledger and the spec graph
  .gonk/scripts/          a workflow script (a plan): next, for one artifact
  tools/worker.py         the worker loop, for a machine that takes items unattended

Four layers are copied in order, a later one replacing a file an earlier one wrote: this
starter's own files (so a generated repository always carries the current tools), what each
chosen process profile adds (`profiles/<name>/`), the sample's language-neutral half
(`templates/library/`), and one target's artifacts and tests (`templates/<target>/`).

`--process` names the profiles, how the team works: who dispatches the work, how it is
defined, how it is reviewed. AGENTS.md is assembled from the sections those profiles use, the
justfile from their recipes, and `.gonk/process.toml` records them for `tools/gonk.py`.
Without the flag it is `profiles/profiles.toml`'s `default`, what this generated before
profiles existed. `profiles/README.md` says what a profile is.

Standard library only, Python 3.11 or later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
SAMPLE = TEMPLATES / "library"
PROFILES = ROOT / "profiles"

# This starter's files that every generated repository carries, unchanged. The justfile is
# assembled (see `justfile`). CLAUDE.md is a one-line file, `@AGENTS.md`, which Claude Code
# expands into the rules, never a symlink: a checkout with symlinks off (Git for Windows by
# default) turns a link into a file holding the target's NAME. .gitattributes keeps every
# checkout LF whatever core.autocrlf says. docs/windows.md says why for both.
# AGENTS.md is not here: it is assembled from the chosen profiles' sections (agents_md).
# The worker loop is not here either: the `workers` profile carries it.
STARTER_FILES = [
    "CLAUDE.md",
    ".gitignore",
    ".gitattributes",
    "docs/windows.md",
    "tools/gonk.py",
    "hooks/claude/README.md",
    "hooks/claude/require-claim.py",
    "hooks/claude/settings.sample.json",
    ".gonk/README.md",
    ".gonk/layout.toml",
    ".gonk/gonk.toml",
    ".gonk/queries/README.md",
    ".gonk/scripts/README.md",
    "openspec/README.md",
    "openspec/config.yaml",
    "openspec/specs/README.md",
    "openspec/changes/README.md",
    "artifacts/README.md",
]

# Recipes of this starter's justfile that belong to the starter, not to what it generates.
STARTER_ONLY_RECIPES = {"gates", "new"}

# A target supplies its recipes here; they are appended to the starter's, not copied.
RECIPES = "justfile.recipes"

SKIP = {"__pycache__", ".DS_Store"}


def targets() -> list[str]:
    """Every target this starter can generate: a templates/<name>/ beside the sample."""
    return sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir() and p != SAMPLE)


def files_under(root: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP)
        for name in sorted(filenames):
            if name not in SKIP and not name.endswith(".pyc"):
                found.append(Path(dirpath, name).relative_to(root))
    return found


def recipe_blocks(text: str) -> list[tuple[str | None, str]]:
    """A justfile as (recipe name or None, block) pairs, a block being the comment lines
    above a recipe, its header and its indented body."""
    blocks, current = [], []
    for line in text.splitlines():
        if not line.strip() and current:
            blocks.append("\n".join(current))
            current = []
        elif line.strip():
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    named = []
    for block in blocks:
        header = next((ln for ln in block.splitlines() if not ln.startswith("#")), None)
        m = re.match(r"^([A-Za-z_][\w-]*)\b[^:=]*:(?!=)", header) if header else None
        named.append((m.group(1) if m else None, block))
    return named


# --------------------------------------------------------------------------- process profiles

# What a profile may name, and what each means. Data names them; this is the whole set of
# meanings, so a typo in a profile.toml is refused rather than silently doing nothing.
HOOKS = {
    # hooks/claude/: refuse an edit without a claim. Optional: on once its settings are merged.
    "claude:require-claim",
    # hooks/git/post-commit: ask gonk to review each commit. On once `just hooks` has run.
    "git:post-commit",
}
CHECKS = {
    # `tools/gonk.py init`: is a worker configured here (.gonk/worker.toml)?
    "worker-config",
    # `tools/gonk.py init`: the hook installed, gonk on this machine, the browse root, a
    # model, review armed.
    "review",
}
PROCESS_TOML = Path(".gonk") / "process.toml"
PROCESS_HEADER = """\
# How this repository's team works: the process profiles it was generated with, and what
# each one asks of it. Committed. Written by ikigai-gonk-starter's tools/new.py from its
# profiles/; `python3 tools/gonk.py init` and `status` report it and read nothing else to
# decide it. AGENTS.md holds the rules of exactly these profiles, so change both together."""


def read(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def catalog() -> dict:
    """profiles/profiles.toml: the default, the profiles in order, the AGENTS.md sections."""
    return read(PROFILES / "profiles.toml")


def profile(name: str) -> dict:
    return read(PROFILES / name / "profile.toml")


def resolve(names: list[str] | None) -> list[str]:
    """The profiles to generate with, in the catalog's order: `names` (any order, repeats
    allowed), or the default. Refuses an unknown name and a pair either side says conflicts."""
    known = catalog()["profiles"]
    wanted = list(catalog()["default"] if names is None else names)
    if not wanted:
        raise SystemExit("new.py: --process names no profile; " + f"this starter has {known}")
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise SystemExit(
            f"new.py: no process profile {', '.join(map(repr, unknown))}; "
            f"this starter has {', '.join(known)}"
        )
    chosen = [n for n in known if n in wanted]
    for a in chosen:
        for b, why in profile(a).get("conflicts", {}).items():
            if b in chosen:
                raise SystemExit(f"new.py: --process {a},{b}: {why}")
    return chosen


def agents_md(names: list[str]) -> str:
    """AGENTS.md for these profiles: the base sections and theirs, in the catalog's order."""
    cat = catalog()
    used = set(cat["base"]["sections"])
    for name in names:
        used |= set(profile(name).get("sections", []))
    parts = [
        (PROFILES / "sections" / f"{s}.md").read_text(encoding="utf-8").strip("\n")
        for s in cat["sections"]
        if s in used
    ]
    return "\n\n".join(parts) + "\n"


def toml_list(values: list[str]) -> str:
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "]"


def process_toml(names: list[str]) -> str:
    """.gonk/process.toml for these profiles: their names, the hooks and checks they ask for,
    each one's summary and roles, then each one's own settings (profiles/<name>/process.toml,
    verbatim, comments and all)."""
    cat = catalog()
    hooks = list(cat["base"].get("hooks", []))
    checks: list[str] = []
    tables, fragments = [], []
    for name in names:
        data = profile(name)
        hooks += [h for h in data.get("hooks", []) if h not in hooks]
        checks += [c for c in data.get("checks", []) if c not in checks]
        tables += [
            "",
            f"[profile.{name}]",
            f"summary = {json.dumps(data['summary'], ensure_ascii=False)}",
            f"roles = {toml_list(data.get('roles', []))}",
        ]
        fragment = PROFILES / name / "process.toml"
        if fragment.is_file():
            fragments.append(fragment.read_text(encoding="utf-8").rstrip("\n"))
    lines = [
        PROCESS_HEADER,
        "version = 1",
        f"process = {toml_list(names)}",
        f"hooks = {toml_list(hooks)}",
        f"checks = {toml_list(checks)}",
        *tables,
        *fragments,
    ]
    return "\n".join(lines) + "\n"


def profile_problems() -> list[str]:
    """Everything wrong with profiles/, as sentences. A test asserts there is nothing."""
    bad = []
    cat = catalog()
    order, base = cat["sections"], set(cat["base"]["sections"])
    files = {p.stem for p in (PROFILES / "sections").glob("*.md")}
    bad += [f"sections/{s}.md is in no profile and not in `sections`" for s in files - set(order)]
    bad += [f"`sections` names {s}, which has no sections/{s}.md" for s in set(order) - files]
    bad += [f"base names {s}, which is not in `sections`" for s in base - set(order)]
    bad += [
        f"base hook {h!r} is not one of {sorted(HOOKS)}"
        for h in cat["base"].get("hooks", [])
        if h not in HOOKS
    ]
    bad += [f"default names {n}, not a profile" for n in cat["default"] if n not in cat["profiles"]]
    owners: dict[str, str] = {}
    for name in cat["profiles"]:
        if not (PROFILES / name / "profile.toml").is_file():
            bad.append(f"{name}: no profiles/{name}/profile.toml")
            continue
        data = profile(name)
        if not data.get("summary"):
            bad.append(f"{name}: no summary")
        for s in data.get("sections", []):
            if s not in order:
                bad.append(f"{name}: section {s} is not in `sections`")
            elif s in base:
                bad.append(f"{name}: section {s} is in base already")
            elif s in owners:
                bad.append(f"{name}: section {s} is {owners[s]}'s too")
            owners[s] = name
        bad += [
            f"{name}: hook {h!r} is not one of {sorted(HOOKS)}"
            for h in data.get("hooks", [])
            if h not in HOOKS
        ]
        bad += [
            f"{name}: check {c!r} is not one of {sorted(CHECKS)}"
            for c in data.get("checks", [])
            if c not in CHECKS
        ]
        bad += [
            f"{name}: conflicts with {o}, not a profile"
            for o in data.get("conflicts", {})
            if o not in cat["profiles"]
        ]
        bad += [
            f"{name}: starter file {f} does not exist"
            for f in data.get("starter_files", [])
            if not (ROOT / f).is_file()
        ]
        bad += [
            f"{name}: starter directory {d} does not exist"
            for d in data.get("starter_dirs", [])
            if not (ROOT / d).is_dir()
        ]
        starter_recipes = {n for n, _ in recipe_blocks((ROOT / "justfile").read_text()) if n}
        bad += [
            f"{name}: recipe {r} is not in this starter's justfile"
            for r in data.get("recipes", [])
            if r not in starter_recipes
        ]
    bad += [
        f"section {s} is in no profile and not in base"
        for s in order
        if s not in base and s not in owners
    ]
    return bad


def profile_recipes() -> set[str]:
    """Every recipe of this starter's justfile that a profile carries, chosen or not."""
    return {r for name in catalog()["profiles"] for r in profile(name).get("recipes", [])}


def justfile(target_dir: Path, names: list[str] | None = None) -> str:
    """This starter's recipes, without the starter's own or a profile's not chosen, then the
    chosen profiles' own recipes, then the target's."""
    names = resolve(names)
    carried = {r for name in names for r in profile(name).get("recipes", [])}
    dropped = STARTER_ONLY_RECIPES | (profile_recipes() - carried)
    kept = [
        block
        for name, block in recipe_blocks((ROOT / "justfile").read_text(encoding="utf-8"))
        if name not in dropped
    ]
    for extra in [*(PROFILES / name / RECIPES for name in names), target_dir / RECIPES]:
        if extra.is_file():
            kept.append(extra.read_text(encoding="utf-8").strip("\n"))
    return "\n\n".join(kept) + "\n"


def generate(dest: Path, target: str, process: list[str] | None = None) -> list[Path]:
    """Write the repository into `dest` (absent or empty); answer the files written.

    `process` names the profiles (default: the catalog's `default`)."""
    if target not in targets():
        raise SystemExit(f"new.py: no target {target!r}; this starter has {', '.join(targets())}")
    names = resolve(process)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise SystemExit(f"new.py: {dest} exists and is not empty; name a new directory")
    target_dir = TEMPLATES / target
    plan: dict[Path, Path] = {Path(rel): ROOT / rel for rel in STARTER_FILES}
    for name in names:
        data = profile(name)
        for rel in data.get("starter_files", []):
            plan[Path(rel)] = ROOT / rel
        for top in data.get("starter_dirs", []):
            for rel in files_under(ROOT / top):
                plan[top / rel] = ROOT / top / rel
        own = PROFILES / name / "files"
        if own.is_dir():
            for rel in files_under(own):
                plan[rel] = own / rel
    for layer in (SAMPLE, target_dir):
        for rel in files_under(layer):
            if rel != Path(RECIPES):
                plan[rel] = layer / rel
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, source in sorted(plan.items()):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, dest / rel)  # contents and permission bits
        written.append(rel)
    # newline="\n": written, not copied, so say LF explicitly (Python on Windows writes CRLF).
    assembled = {
        Path("justfile"): justfile(target_dir, names),
        Path("AGENTS.md"): agents_md(names),
        PROCESS_TOML: process_toml(names),
    }
    for rel, text in assembled.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_text(text, encoding="utf-8", newline="\n")
    return sorted([*written, *assembled])


def sync_starter() -> list[Path]:
    """Rewrite this starter's own AGENTS.md and .gonk/process.toml as the default process
    assembles them: a repository made from the starter as a TEMPLATE (a copy, not new.py)
    then carries the same rules and the same record as one new.py generates. A test fails
    while they differ, naming this."""
    names = resolve(None)
    for rel, text in [(Path("AGENTS.md"), agents_md(names)), (PROCESS_TOML, process_toml(names))]:
        (ROOT / rel).write_text(text, encoding="utf-8", newline="\n")
    return [Path("AGENTS.md"), PROCESS_TOML]


def main(argv: list[str] | None = None) -> int:
    cat = catalog()
    p = argparse.ArgumentParser(prog="new.py", description=__doc__.splitlines()[0])
    p.add_argument("dir", nargs="?", help="where to write it: a new or empty directory")
    p.add_argument("--target", default="python", help=f"the language ({', '.join(targets())})")
    p.add_argument(
        "--process",
        help=f"how the team works: one or more of {', '.join(cat['profiles'])}, "
        f"comma-separated (default: {','.join(cat['default'])})",
    )
    p.add_argument(
        "--sync-starter",
        action="store_true",
        help="rewrite this starter's AGENTS.md and .gonk/process.toml from the default process",
    )
    args = p.parse_args(argv)
    if args.sync_starter:
        for rel in sync_starter():
            print(f"wrote {ROOT / rel}")
        return 0
    if not args.dir:
        p.error("name the directory to write")
    process = [n.strip() for n in args.process.split(",") if n.strip()] if args.process else None
    dest = Path(args.dir).expanduser().resolve()
    written = generate(dest, args.target, process)
    names = resolve(process)
    print(
        f"generated the library-lending sample ({args.target}, process {', '.join(names)}) "
        f"in {dest}: {len(written)} files"
    )
    steps = [
        "git init, then commit everything it wrote (the one commit where every file is yours)",
        "python3 scripts/test.py                      every test, as CI runs them",
        "python3 tools/gonk.py init --ledger <name> --socket ~/.ikigai/gonk.sock --holder <you>",
    ]
    if "review-every-commit" in names:
        steps.append("git config core.hooksPath hooks/git     the post-commit review hook")
    steps += [
        "python3 tools/gonk.py seed                   file the open changes' tasks as items",
        "python3 tools/gonk.py next                   what to do first, and why",
    ]
    print("\nnext, in that directory:\n" + "".join(f"  {s}\n" for s in steps))
    print("AGENTS.md holds this process's rules; README.md walks through the sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
