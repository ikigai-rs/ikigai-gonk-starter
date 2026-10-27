"""The worker's usage report and its hold above a threshold (tools/worker_usage.py), hermetic."""

from __future__ import annotations

import json
import sys
import unittest

import worker_usage  # noqa: E402  (tests/support puts tools/ on the path)
from support import gonk
from worker_support import FakeLedger, Rig, worker

NOW = "2026-10-17T10:00:00Z"


class Parsing(unittest.TestCase):
    def test_a_reading_keeps_only_its_fields(self):
        text = json.dumps(
            {
                "plan": "Max weekly",
                "used": 4200,
                "limit": 5000,
                "resets_at": "2026-10-20T09:00:00Z",
                "token": "sk-secret",
                "account_id": "acct-1",
            }
        )
        line = worker_usage.parse("claude", text, NOW).line()
        self.assertEqual(
            line,
            'Usage: agent=claude plan="Max weekly" used=4200 limit=5000 percent=84 '
            "resets=2026-10-20T09:00:00Z read=" + NOW,
        )
        self.assertNotIn("secret", line)
        self.assertNotIn("acct", line)

    def test_a_plan_that_is_not_plain_words_is_dropped(self):
        r = worker_usage.parse("x", json.dumps({"percent": 10, "plan": 'a\nb=sk-1"'}), NOW)
        self.assertIsNone(r.plan)

    def test_nothing_to_trust_is_unknown(self):
        for text in ["", "not json", "[1]", "{}", '{"percent": "high"}', '{"percent": -1}']:
            r = worker_usage.parse("codex", text, NOW)
            self.assertFalse(r.known, text)
            self.assertEqual(r.line(), f"Usage: agent=codex unknown read={NOW}")


class Watching(unittest.TestCase):
    def watch(self, outputs, status_item=None, ledger=None):
        self.logs, self.outputs = [], list(outputs)
        return worker_usage.UsageWatch(
            "claude",
            ["usage"],
            85.0,
            3600,
            status_item,
            ledger,
            self.logs.append,
            run=lambda argv: self.outputs.pop(0),
        )

    def test_above_the_threshold_holds_and_says_so_once(self):
        w = self.watch(['{"percent": 90}'])
        self.assertIn("90% of its weekly limit", w.check())
        self.assertIn("over 85%", w.check())
        self.assertEqual(sum("taking no new item" in m for m in self.logs), 1)

    def test_at_the_threshold_and_unknown_do_not_hold(self):
        self.assertIsNone(self.watch(['{"percent": 85}']).check())
        self.assertIsNone(self.watch(["garbage"]).check())
        self.assertIsNone(self.watch([None]).check())

    def test_a_reset_time_in_the_past_releases_the_hold(self):
        text = '{"percent": 99, "resets_at": "2020-01-01T00:00:00Z"}'
        self.assertIsNone(self.watch([text]).check())

    def test_at_most_once_an_hour(self):
        w = self.watch(['{"percent": 10}', '{"percent": 99}'])
        self.assertIsNone(w.check())
        self.assertIsNone(w.check())
        self.assertEqual(len(self.outputs), 1)
        w.last -= 3601
        self.assertIsNotNone(w.check())

    def test_back_under_takes_items_again(self):
        w = self.watch(['{"percent": 99}', '{"percent": 10}'])
        self.assertIsNotNone(w.check())
        w.last -= 3601
        self.assertIsNone(w.check())
        self.assertTrue(any("taking items again" in m for m in self.logs))

    def test_no_command_reads_nothing(self):
        w = worker_usage.UsageWatch("x", None, 85.0, 3600, None, None, print)
        self.assertIsNone(w.check())


class InTheLoop(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        self.status = self.ledger.file("status: w1", ["worker:status"], dispatched_by=None)
        self.item = self.ledger.file("fix", ["worker:w1", "repo:lib"])

    def run_with(self, usage_json: str):
        path = self.rig.configure()
        script = self.rig.tmp / "usage.py"
        script.write_text(f"print({usage_json!r})\n")
        with path.open("a") as f:
            f.write(
                f"\n[usage]\ncommand = {json.dumps([sys.executable, str(script)])}\n"
                f'agent = "claude"\nstatus_item = {self.status}\n'
            )
        w = self.rig.worker(self.ledger)
        return w, w.run_once()

    def test_the_config_is_checked(self):
        path = self.rig.configure()
        for bad in ["threshold = 0", "threshold = 101", 'status_item = "x"', "command = []"]:
            path.write_text(path.read_text().split("\n[usage]")[0] + f"\n[usage]\n{bad}\n")
            with self.assertRaises(gonk.ConfigError, msg=bad):
                worker.load_worker(gonk.load(self.rig.checkout))

    def test_over_the_threshold_posts_the_report_and_takes_nothing(self):
        w, outcome = self.run_with('{"plan": "Max", "used": 90, "limit": 100}')
        self.assertEqual(outcome.code, worker.NOTHING)
        self.assertIn("90% of its weekly limit", outcome.summary)
        [comment] = self.ledger.human_comments(self.status)
        self.assertIn("agent=claude", comment["text"])
        self.assertIn("percent=90", comment["text"])
        self.assertIsNone(self.ledger.items[self.item]["claim"])
        # The item dispatched here is told why it waits, once (ledger item 2393).
        [note] = self.ledger.human_comments(self.item)
        self.assertIn("90% of its weekly limit", note["text"])

    def test_under_the_threshold_works_the_item(self):
        w, outcome = self.run_with('{"percent": 10}')
        self.assertNotEqual(outcome.code, worker.NOTHING)
        self.assertEqual(outcome.number, self.item)
        [comment] = self.ledger.human_comments(self.status)
        self.assertIn("percent=10", comment["text"])


if __name__ == "__main__":
    unittest.main()
