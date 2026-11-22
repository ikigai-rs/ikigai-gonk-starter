"""Offline checks of the workshop layers and their client examples."""

import importlib.util
import json
import re
import sys
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
WORKSHOP = ROOT / "workshop"
sys.path.insert(0, str(WORKSHOP))


def load(name):
    spec = importlib.util.spec_from_file_location(name, WORKSHOP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared_tasks = load("shared_tasks")
take_one = load("take_one")


class Conflict(Exception):
    pass


class Door:
    def __init__(self):
        self.pending = {}
        self.results = []

    def sink(self, resource, content, **kwargs):
        if resource == shared_tasks.TASKS:
            subject = re.search(r"<(urn:layer:workshop-tasks:offer:[^>]+)>", content)[1]
            self.pending[subject] = content
        else:
            self.results.append(content)

    def delete(self, resource, **kwargs):
        subject = kwargs["tuple"]
        if subject not in self.pending:
            raise Conflict("already taken")
        del self.pending[subject]
        return SimpleNamespace(data='{"receipt": "winner"}')

    def source(self, resource, **kwargs):
        return SimpleNamespace(data=json.dumps({"@graph": [{"@id": s} for s in self.pending]}))


class WorkshopLayersTest(unittest.TestCase):
    def test_layer_configs_shapes_and_grants(self):
        grants = tomllib.loads((WORKSHOP / "collab.example.toml").read_text())
        mounts = {entry["layer"]: entry for entry in grants["mount"]}
        for name, model in (("workshop-tasks", "tuplespace"), ("workshop-results", "channel")):
            config = tomllib.loads((WORKSHOP / "layers" / f"{name}.toml").read_text())
            self.assertEqual(config["model"], model)
            context = json.loads((WORKSHOP / "layers" / config["context"]).read_text())
            self.assertIn("@context", context)
            shape = (WORKSHOP / "layers" / config["shapes"]).read_text()
            # The fixture uses a deliberately limited Turtle grammar; parse each declaration,
            # shape header and property list so truncation or malformed punctuation fails.
            prefixes, body = shape.rsplit("\n\n", 1)
            for line in prefixes.splitlines():
                self.assertRegex(line, r"^@prefix [a-z]+: <[^>]+> \.$")
            pattern = (
                r"<[^>]+> a sh:NodeShape ;\s+sh:targetClass \w+:\w+ ; "
                r"sh:closed true ; sh:ignoredProperties \( rdf:type \) ;\s+sh:property\s+"
                r"\[ [^\[\]]+ \](?: ,\s+\[ [^\[\]]+ \])* \.\s*"
            )
            self.assertIsNotNone(re.fullmatch(pattern, body))
            for prop in re.findall(r"\[ ([^\[\]]+) \]", body):
                self.assertRegex(prop, r"^sh:path \w+:\w+ ; ")
        self.assertIn("out", mounts["workshop-tasks"]["facilitator"])
        self.assertEqual(set(mounts["workshop-tasks"]["participant"]), {"read", "feed", "take"})
        self.assertIn("say", mounts["workshop-results"]["participant"])

    def test_seed_reset_and_two_takers(self):
        door = Door()
        tasks = shared_tasks.build_tasks("run1")
        self.assertEqual(len(tasks), 8)
        self.assertEqual(len({subject for subject, _ in tasks}), 8)
        self.assertTrue(all("ik:brief" in body and "ik:about" in body for _, body in tasks))
        shared_tasks.seed(door, "run1")
        subject = tasks[0][0]
        self.assertTrue(take_one.take_and_report(door, subject, "Checked.", (Conflict,)))
        self.assertFalse(take_one.take_and_report(door, subject, "Lost.", (Conflict,)))
        self.assertEqual(len(door.results), 1)
        self.assertIn(subject, door.results[0])
        shared_tasks.reset(door)
        self.assertEqual(door.pending, {})
        self.assertEqual(len(door.results), 1)


if __name__ == "__main__":
    unittest.main()
