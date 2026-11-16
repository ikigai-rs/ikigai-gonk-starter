"""The spec profile's pull-request check (AGENTS.md, "Spec first"), run by `tools/checks.py pr`:
the spec's folders are whole, and a pull request marked as a behavior change adds or changes a
change folder. tools/checks.py says what marks one and how to configure it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402


def is_behavior_change(pr: dict, config: dict) -> bool:
    labels = {(lab.get("name") or "").lower() for lab in pr.get("labels") or []}
    if labels & {name.lower() for name in config["behavior_labels"]}:
        return True
    title = (pr.get("title") or "").strip().lower()
    return any(title.startswith(p.lower()) for p in config["behavior_title_prefixes"])


def spec_problems(repo: Path) -> list[str]:
    """What the openspec tree lacks: a change folder without its proposal or tasks, a spec
    folder without its spec.md."""
    bad = []
    changes = repo / "openspec" / "changes"
    folders = [d for d in sorted(changes.glob("*")) if d.is_dir() and d.name != "archive"]
    folders += [d for d in sorted((changes / "archive").glob("*")) if d.is_dir()]
    for d in folders:
        for need in ("proposal.md", "tasks.md"):
            if not (d / need).is_file():
                bad.append(f"{d.relative_to(repo).as_posix()} has no {need}")
    for d in sorted((repo / "openspec" / "specs").glob("*")):
        if d.is_dir() and not (d / "spec.md").is_file():
            bad.append(f"{d.relative_to(repo).as_posix()} has no spec.md")
    return bad


def check_spec(pr: dict, changed: list[str], config: dict, repo: Path) -> list[str]:
    out = [f"The spec does not validate: {p}." for p in spec_problems(repo)]
    if is_behavior_change(pr, config) and not any(
        p.startswith("openspec/changes/") for p in changed
    ):
        out.append(
            "This pull request is marked as a behavior change (its label or title) but "
            "changes nothing under openspec/changes/. Add or update the change folder "
            "(proposal, spec deltas, tasks), or remove the mark if behavior does not change."
        )
    if out:
        harness.fire("pr-spec-change-folder", "refusal", repo, tool="ci")
    return out
