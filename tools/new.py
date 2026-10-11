#!/usr/bin/env python3
"""Generate a new repository from this starter, with the library-lending sample in it.

    python3 tools/new.py <dir> [--target python]     (or: just new <dir> --target python)

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

Three layers are copied in order, a later one replacing a file an earlier one wrote: this
starter's own files (so a generated repository always carries the current tools), the
sample's language-neutral half (`templates/library/`), and one target's artifacts and tests
(`templates/<target>/`). Standard library only, Python 3.11 or later.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
SAMPLE = TEMPLATES / "library"

# This starter's files that every generated repository carries, unchanged. The justfile is
# assembled (see `justfile`), and CLAUDE.md is made as a link to AGENTS.md.
STARTER_FILES = [
    "AGENTS.md",
    ".gitignore",
    "tools/gonk.py",
    # The worker loop, so a generated project can hand items to an unattended machine (the
    # justfile's `worker` recipes run it): the loop, its configuration's commented template,
    # and the service units in STARTER_DIRS. Setting up the worker MACHINE (its accounts and
    # boundary, worker/README.md and worker-setup.sh) stays in this starter.
    "tools/worker.py",
    "worker/worker.sample.toml",
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

# This starter's directories whose every file a generated repository carries, unchanged.
STARTER_DIRS = ["worker/units"]

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


def justfile(target_dir: Path) -> str:
    """This starter's recipes, without the starter's own, then the target's."""
    kept = [
        block
        for name, block in recipe_blocks((ROOT / "justfile").read_text(encoding="utf-8"))
        if name not in STARTER_ONLY_RECIPES
    ]
    extra = target_dir / RECIPES
    if extra.is_file():
        kept.append(extra.read_text(encoding="utf-8").strip("\n"))
    return "\n\n".join(kept) + "\n"


def generate(dest: Path, target: str) -> list[Path]:
    """Write the repository into `dest` (absent or empty); answer the files written."""
    if target not in targets():
        raise SystemExit(f"new.py: no target {target!r}; this starter has {', '.join(targets())}")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise SystemExit(f"new.py: {dest} exists and is not empty; name a new directory")
    target_dir = TEMPLATES / target
    plan: dict[Path, Path] = {Path(rel): ROOT / rel for rel in STARTER_FILES}
    for top in STARTER_DIRS:
        for rel in files_under(ROOT / top):
            plan[top / rel] = ROOT / top / rel
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
    (dest / "justfile").write_text(justfile(target_dir), encoding="utf-8")
    os.symlink("AGENTS.md", dest / "CLAUDE.md")
    return sorted([*written, Path("justfile"), Path("CLAUDE.md")])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="new.py", description=__doc__.splitlines()[0])
    p.add_argument("dir", help="where to write it: a new or empty directory")
    p.add_argument("--target", default="python", help=f"the language ({', '.join(targets())})")
    args = p.parse_args(argv)
    dest = Path(args.dir).expanduser().resolve()
    written = generate(dest, args.target)
    print(f"generated the library-lending sample ({args.target}) in {dest}: {len(written)} files")
    print(
        "\nnext, in that directory:\n"
        "  git init, then commit everything it wrote (the one commit where every file is yours)\n"
        "  python3 scripts/test.py                      every test, as CI runs them\n"
        "  python3 tools/gonk.py init --ledger <name> --socket ~/.ikigai/gonk.sock --holder <you>\n"
        "  python3 tools/gonk.py seed                   file the open changes' tasks as items\n"
        "  python3 tools/gonk.py next                   what to do first, and why\n"
        "\nREADME.md walks through it."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
