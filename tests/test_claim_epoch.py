"""The claim's fencing epoch (ledger #3205): gonk.py and the worker read it from both answer
shapes, present it as `epoch=` on every write about the item they hold, and stop writing when
gonk names a newer one. A ledger with no epoch still works: nothing is presented to it.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import unittest

from support import FakeIkigai, gonk, ok, scratch_repo
from test_gonk import run, wire
from worker_support import FakeLedger, Rig, worker

UNTIL = "2026-10-10T22:00:00Z"


class TheAnswerShapes(unittest.TestCase):
    def test_the_old_shape_has_no_epoch(self):
        for text, holder in [
            (f"acme#1 claimed by w1 until {UTC_TEXT}", "w1"),
            (f"acme#1 renewed by w1 until {UTC_TEXT}", "w1"),
            (f"acme#1 taken over from old by w2 until {UTC_TEXT}", "w2"),
        ]:
            self.assertEqual(
                gonk.parse_claimed(text), {"holder": holder, "epoch": None, "until": UNTIL}
            )

    def test_the_new_shape_names_the_epoch(self):
        for text, holder, epoch in [
            (f"#1 claimed by worker-1 at epoch 1 until {UNTIL}", "worker-1", 1),
            (f"#1 renewed by worker-1 at epoch 1 until {UNTIL}", "worker-1", 1),
            (f"#1 taken over from worker-1 by w2 at epoch 12 until {UNTIL}", "w2", 12),
        ]:
            self.assertEqual(
                gonk.parse_claimed(text), {"holder": holder, "epoch": epoch, "until": UNTIL}
            )

    def test_a_claim_with_no_lease(self):
        self.assertEqual(
            gonk.parse_claimed("#1 claimed by w1 at epoch 2\n"),
            {"holder": "w1", "epoch": 2, "until": None},
        )
        self.assertEqual(
            gonk.parse_claimed("#1 claimed by w1\n"), {"holder": "w1", "epoch": None, "until": None}
        )

    def test_the_json_face(self):
        self.assertEqual(gonk.claim_epoch({"claim": {"holder": "w", "epoch": 4}}), 4)
        self.assertIsNone(gonk.claim_epoch({"claim": {"holder": "w"}}))
        self.assertIsNone(gonk.claim_epoch({"claim": None}))


UTC_TEXT = UNTIL


class GonkPy(unittest.TestCase):
    def setUp(self):
        self.repo = scratch_repo(self)
        wire(self.repo)

    def state(self):
        return gonk.read_toml(self.repo / ".gonk" / "state" / "claim.toml")

    def test_claim_records_and_prints_the_epoch_and_report_presents_it(self):
        fake = FakeIkigai(
            [
                (":claim item=12 lease=30m renew", ok(f"#12 renewed by agent-1 until {UNTIL}\n")),
                (":claim", ok(f"#12 claimed by agent-1 at epoch 3 until {UNTIL}\n")),
                (":comment", ok("commented\n")),
                ("delete", ok("released\n")),
            ]
        )
        code, out, _ = run(self.repo, ["claim", "12"], fake)
        self.assertEqual(code, 0)
        self.assertIn("item 12 is held at epoch 3", out)
        self.assertEqual(self.state()["epoch"], 3)
        self.assertEqual(self.state()["expires"], UNTIL)

        # A renewal presents the epoch, and keeps it when the answer does not repeat it.
        run(self.repo, ["claim", "12"], fake)
        self.assertTrue(fake.calls[-1][0].endswith(" renew=true epoch=3"), fake.calls[-1])
        self.assertEqual(self.state()["epoch"], 3)

        url = "https://github.com/o/r/pull/3"
        code, out, _ = run(self.repo, ["report", "12", "--evidence", url], fake)
        self.assertEqual(code, 0)
        self.assertIn("reporting item 12 at epoch 3", out)
        comment, release = fake.calls[-2][0], fake.calls[-1][0]
        self.assertIn(" handback=true", comment)
        self.assertTrue(comment.endswith(" epoch=3"), comment)
        self.assertEqual(
            release, "delete urn:gk:iki:ledger:acme:claim item=12 from=agent-1 epoch=3"
        )

    def test_a_stale_epoch_on_the_report_is_a_claim_gone(self):
        fake = FakeIkigai(
            [
                (":claim", ok(f"#12 claimed by agent-1 at epoch 1 until {UNTIL}\n")),
                (
                    ":comment",
                    gonk.Answer(1, "", "conflict: #12: epoch 1 is stale; the claim is at epoch 2"),
                ),
            ]
        )
        run(self.repo, ["claim", "12"], fake)
        code, _, err = run(
            self.repo, ["report", "12", "--evidence", "https://github.com/o/r/pull/3"], fake
        )
        self.assertEqual(code, gonk.CONFLICT_EXIT)
        self.assertIn("no longer holds item 12", err)
        self.assertFalse(any(c.startswith("delete") for c, _ in fake.calls))
        self.assertFalse((self.repo / ".gonk" / "state" / "claim.toml").exists())

    def test_close_presents_this_trees_epoch(self):
        fake = FakeIkigai(
            [
                (":claim", ok(f"#12 claimed by agent-1 at epoch 5 until {UNTIL}\n")),
                (":close", ok("closed\n")),
                ("delete", ok("released\n")),
            ]
        )
        run(self.repo, ["claim", "12"], fake)
        url = "https://github.com/o/r/pull/3"
        code, out, _ = run(self.repo, ["close", "12", "--evidence", url], fake)
        self.assertEqual(code, 0)
        self.assertIn("closing item 12 at epoch 5", out)
        [close] = [c for c, _ in fake.calls if ":close " in c]
        self.assertTrue(close.endswith(" epoch=5"), close)

    def test_the_old_ledger_is_presented_no_epoch(self):
        fake = FakeIkigai(
            [
                (":claim", ok(f"acme#12 claimed by agent-1 until {UNTIL}\n")),
                (":comment", ok("commented\n")),
                ("delete", ok("released\n")),
            ]
        )
        run(self.repo, ["claim", "12"], fake)
        self.assertNotIn("epoch", self.state())
        run(self.repo, ["claim", "12"], fake)
        run(self.repo, ["report", "12", "--evidence", "https://github.com/o/r/pull/3"], fake)
        self.assertFalse(any("epoch" in c for c, _ in fake.calls), fake.calls)

    def test_status_reads_the_json_epoch(self):
        fake = FakeIkigai([(":claim", ok(f"#12 claimed by agent-1 at epoch 1 until {UNTIL}\n"))])
        run(self.repo, ["claim", "12"], fake)
        cfg = gonk.load(self.repo)

        def at(epoch):
            claim = {"holder": "agent-1", "kind": "machine", "epoch": epoch}
            doc = {"display": "acme#12", "status": "open", "claim": claim}
            answer = ok(json.dumps({"schema": 1, "item": doc}))
            return gonk.check_claim(cfg, FakeIkigai([("item:12", answer)])(None))

        allowed, why = at(1)
        self.assertTrue(allowed, why)
        self.assertIn("at epoch 1", why)
        # The same holder's name, at a newer epoch: the claim was taken again since.
        allowed, why = at(2)
        self.assertFalse(allowed)
        self.assertIn("epoch 2", why)


class TheWorker(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeLedger(epochs=True)
        self.rig = Rig(self)
        self.out = io.StringIO()
        self.quiet = contextlib.redirect_stdout(self.out)
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def run_once(self, **config):
        self.rig.configure(**config)
        return self.rig.worker(self.ledger).run_once()

    def writes(self, n: int) -> list[str]:
        return [
            c
            for c, _ in self.ledger.calls
            if f"item={n}" in c and (c.startswith("sink") or c.startswith("delete"))
        ]

    def test_every_write_about_the_item_presents_its_epoch(self):
        self.rig.agent_flags = ["--sleep", "2.5"]
        n = self.ledger.file("Fenced work", ["worker:w1", "repo:lib"])
        outcome = self.run_once(lease='"5s"', renew_every='"1s"')
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        take, *rest = self.writes(n)
        self.assertNotIn("epoch=", take)  # the take is what grants it
        self.assertGreaterEqual(len(rest), 3)  # renewals, the hand-back, the release
        self.assertTrue(all(c.endswith(" epoch=1") for c in rest), rest)
        self.assertIn(f"claimed acme#{n} for `w1` at epoch 1", self.out.getvalue())
        self.assertIsNone(self.ledger.items[n]["claim"])

    def test_a_takeover_mid_run_stops_the_worker_with_no_further_writes(self):
        self.rig.agent_flags = ["--sleep", "60"]
        n = self.ledger.file("Taken over", ["worker:w1", "repo:lib"])
        started = time.monotonic()

        def take_it():
            while not (self.rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            before = len(self.writes(n))
            self.ledger.force_claim(n, "w1")  # the same name, at epoch 2: still not ours
            self.taken_at = before

        thread = threading.Thread(target=take_it)
        thread.start()
        outcome = self.run_once(renew_every='"1s"')
        thread.join()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertLess(time.monotonic() - started, 20, "the agent was not stopped")
        self.assertEqual(self.ledger.items[n]["claim"]["epoch"], 2)
        self.assertEqual(self.ledger.items[n]["claim"]["holder"], "w1")
        # Nothing written after the takeover: no comment, no renewal, no release.
        self.assertEqual(len(self.writes(n)), self.taken_at, self.writes(n))
        self.assertEqual(self.ledger.human_comments(n), [])
        log = self.out.getvalue()
        self.assertIn(f"STOP: item {n}:", log)
        self.assertIn("lane was taken over", log)

    def test_a_stale_epoch_conflict_on_a_write_fences_the_item(self):
        n = self.ledger.file("Fenced", ["worker:w1", "repo:lib"])
        cfg = gonk.Config(self.rig.checkout, "acme", None, "w1", {})
        ledger = worker.Ledger(cfg, self.ledger)
        self.assertTrue(ledger.claim(n, "w1", "30m").ok)
        self.assertEqual(ledger.epochs, {n: 1})
        self.ledger.force_claim(n, "someone-else")
        answer = ledger.comment(n, "late")
        self.assertTrue(answer.conflict)
        self.assertIn("epoch 2", ledger.fenced_why(n))
        calls = len(self.ledger.calls)
        self.assertFalse(ledger.release(n, "w1").ok)
        self.assertFalse(ledger.claim(n, "w1", "30m", renew=True).ok)
        self.assertEqual(len(self.ledger.calls), calls, "a fenced item was written to")
        self.assertEqual(self.ledger.items[n]["claim"]["holder"], "someone-else")
        self.assertEqual(self.ledger.human_comments(n), [])

    def test_the_old_ledger_is_presented_no_epoch(self):
        self.ledger = FakeLedger()
        n = self.ledger.file("Old", ["worker:w1", "repo:lib"])
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertFalse(any("epoch" in c for c in self.writes(n)), self.writes(n))
        self.assertIsNone(self.ledger.items[n]["claim"])


if __name__ == "__main__":
    unittest.main()
