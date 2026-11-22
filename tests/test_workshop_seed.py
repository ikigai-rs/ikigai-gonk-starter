"""Workshop seed exercises a ledger through the same resource calls as the CLI."""

import json
import unittest
from unittest.mock import patch

from support import ROOT, gonk, ok

# isort: split
import workshop_seed  # noqa: E402


class Ledger:
    def __init__(self):
        self.items = []
        self.links = []
        self.comments = []
        self.claims = []
        self.calls = []
        self.closed = []

    def run(self, command, stdin=None):
        self.calls.append((command, stdin))
        if command.startswith("source "):
            return ok(json.dumps({"items": self.items}))
        if ":append " in command:
            n = len(self.items) + 1
            self.items.append((command, stdin))
            return ok(f"workshop#{n} urn:iki:ledger:workshop:item:0{n}\n")
        if ":link " in command:
            self.links.append((command, stdin))
        elif ":comment " in command:
            self.comments.append((command, stdin))
        elif ":claim " in command:
            self.claims.append((command, stdin))
        elif ":close " in command:
            self.closed.append((command, stdin))
        else:
            raise AssertionError(command)
        return ok()


class WorkshopSeed(unittest.TestCase):
    def test_complete_seed_and_repeat_refused_without_writes(self):
        fixture = json.loads((ROOT / "workshop/ledger.json").read_text())
        cfg = gonk.Config(ROOT, "workshop", None, None, {})
        ledger = Ledger()
        with patch("workshop_seed.time.sleep") as sleep:
            self.assertEqual(workshop_seed.seed(cfg, ledger, fixture, "workshop-library"), 0)
        sleep.assert_called_once_with(2)
        self.assertEqual(len(ledger.items), 31)
        refs = {item["id"]: i for i, item in enumerate(fixture["items"], 1)}
        for (command, content), item in zip(ledger.items, fixture["items"], strict=True):
            self.assertIn(f"priority={item['priority']}", command)
            self.assertIn("labels=" + ",".join(item["labels"]) + ",repo:workshop-library", command)
            self.assertEqual(content, item["title"] + "\n\n" + item["body"])
        for (command, target), (source_id, target_id, link_type) in zip(
            ledger.links, fixture["links"], strict=True
        ):
            self.assertIn(f"type={link_type}", command)
            self.assertIn(f"item=urn:iki:ledger:workshop:item:0{refs[source_id]}", command)
            self.assertEqual(target, f"urn:iki:ledger:workshop:item:0{refs[target_id]}")
        for (command, content), comment in zip(ledger.comments, fixture["comments"], strict=True):
            self.assertIn(f"item={refs[comment['item']]}", command)
            self.assertEqual("handback=true" in command, bool(comment.get("handback")))
            self.assertEqual(content, comment["text"])
        self.assertEqual(len(ledger.closed), 2)
        self.assertIn(f"item={refs['done']}", ledger.closed[0][0])
        self.assertIn(f"item={refs['renewal-decision']}", ledger.closed[1][0])
        self.assertEqual(ledger.closed[1][1], "Decided: a loan may be renewed twice.")
        self.assertIn(
            (
                f"sink {cfg.resource('link')} "
                f"item=urn:iki:ledger:workshop:item:0{refs['design']} type=related",
                f"urn:iki:ledger:workshop:item:0{refs['renewal-decision']}",
            ),
            ledger.links,
        )
        self.assertEqual(len(ledger.claims), 1)
        self.assertIn(f"item={refs[fixture['expired']]} lease=1s", ledger.claims[0][0])
        self.assertEqual(ledger.claims[0][1], "workshop-stalled")
        before = len(ledger.calls)
        self.assertEqual(workshop_seed.seed(cfg, ledger, fixture), 1)
        self.assertEqual(len(ledger.calls), before + 1)
        self.assertTrue(ledger.calls[-1][0].startswith("source "))

    def test_item_requests_do_not_reveal_facilitator_answers(self):
        fixture = json.loads((ROOT / "workshop/ledger.json").read_text())
        answer_phrases = (
            "clarify the reference",
            "resolve the reference before acting",
            "ask a person to decide",
            "ask a person to publish",
            "seek agreement before changing the design",
            "coordinate ownership",
            "check existing work before changing anything",
            "reproduce and investigate before changing it",
        )
        for item in fixture["items"]:
            for field in ("title", "body"):
                for phrase in answer_phrases:
                    with self.subTest(item=item["id"], field=field, phrase=phrase):
                        self.assertNotIn(phrase, item[field].lower())

    def test_failed_append_stops_without_following_writes(self):
        class Refusing(Ledger):
            def run(self, command, stdin=None):
                if ":append " in command:
                    self.calls.append((command, stdin))
                    return gonk.Answer(1, "", "denied")
                return super().run(command, stdin)

        fixture = json.loads((ROOT / "workshop/ledger.json").read_text())
        ledger = Refusing()
        cfg = gonk.Config(ROOT, "workshop", None, None, {})
        self.assertEqual(workshop_seed.seed(cfg, ledger, fixture), 1)
        self.assertEqual(len(ledger.calls), 2)


if __name__ == "__main__":
    unittest.main()
