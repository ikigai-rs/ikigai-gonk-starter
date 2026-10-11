"""The specs and the scenario vectors name the same scenarios, so the tests are the spec.

Every `#### Scenario:` in openspec/specs/ has exactly one entry in openspec/vectors.json, and
every entry names a scenario in the specs or in a change still in flight (a change's new
scenarios get their vectors while it is being built, before it is archived into the specs).
A scenario is named by the IRI the OpenSpec mapping gives it,
`urn:openspec:spec:<capability>:requirement:<slug>:scenario:<slug>`, which is also how the
lifted spec graph and the ledger's items name it.

This file reads only Markdown and JSON, so it holds for every language this repository's
artifacts are written in.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gonk  # noqa: E402  (the path above)

OPENSPEC = ROOT / "openspec"
REQUIREMENT = re.compile(r"^### Requirement: (.+?)\s*$")
SCENARIO = re.compile(r"^#### Scenario: (.+?)\s*$")


def scenarios_in(spec: Path, capability: str) -> tuple[list[str], list[str]]:
    """(the scenario IRIs in one spec file, the requirement names that have no scenario)."""
    found, bare, requirement, count = [], [], None, 0
    for line in spec.read_text(encoding="utf-8").splitlines():
        m = REQUIREMENT.match(line)
        if m:
            if requirement is not None and count == 0:
                bare.append(requirement)
            requirement, count = m.group(1), 0
            continue
        m = SCENARIO.match(line)
        if m and requirement is not None:
            iri = gonk.requirement_iri(capability, requirement)
            found.append(f"{iri}:scenario:{gonk.slug(m.group(1))}")
            count += 1
    if requirement is not None and count == 0:
        bare.append(requirement)
    return found, bare


def current() -> list[str]:
    found = []
    for spec in sorted((OPENSPEC / "specs").glob("*/spec.md")):
        found += scenarios_in(spec, spec.parent.name)[0]
    return found


def in_flight() -> list[str]:
    found = []
    for change in sorted((OPENSPEC / "changes").iterdir()):
        if change.is_dir() and change.name != "archive":
            for delta in sorted(change.glob("specs/*/spec.md")):
                found += scenarios_in(delta, delta.parent.name)[0]
    return found


class SpecsAndVectors(unittest.TestCase):
    vectors = json.loads((OPENSPEC / "vectors.json").read_text(encoding="utf-8"))

    def test_every_scenario_in_the_specs_has_one_vector(self):
        ids = Counter(s["id"] for s in self.vectors["scenarios"])
        missing = [s for s in current() if s not in ids]
        self.assertEqual(missing, [], "scenarios in openspec/specs/ with no vector")
        twice = sorted(s for s, n in ids.items() if n > 1)
        self.assertEqual(twice, [], "scenarios with more than one vector")

    def test_every_vector_names_a_scenario_in_the_specs_or_a_change_in_flight(self):
        known = set(current()) | set(in_flight())
        orphans = [s["id"] for s in self.vectors["scenarios"] if s["id"] not in known]
        self.assertEqual(orphans, [], "vectors for no scenario: renamed, or removed from a spec?")

    def test_every_requirement_has_a_scenario(self):
        for spec in sorted((OPENSPEC / "specs").glob("*/spec.md")):
            _, bare = scenarios_in(spec, spec.parent.name)
            self.assertEqual(bare, [], f"{spec.relative_to(ROOT)}: requirements with no scenario")

    def test_every_fixture_a_scenario_names_exists(self):
        fixtures = set(self.vectors.get("fixtures", {}))
        for s in self.vectors["scenarios"]:
            self.assertLessEqual(set(s.get("given", [])), fixtures, s["id"])

    def test_every_task_cites_a_requirement_its_change_adds_or_modifies(self):
        layout = gonk.read_toml(ROOT / ".gonk" / "layout.toml")
        for change in gonk.open_changes(ROOT, layout):
            for task in change.tasks:
                if task.cites:
                    self.assertIsNotNone(
                        task.requirement,
                        f"{change.id} {task.number} cites {task.cites!r}, which the change's "
                        "delta neither adds nor modifies: a typo files the item about the "
                        "task alone",
                    )


if __name__ == "__main__":
    unittest.main()
