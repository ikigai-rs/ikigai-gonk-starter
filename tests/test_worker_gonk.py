"""The worker loop against a REAL gonk: a scratch one on a scratch port, stopped by PID.

What tests/test_worker_loop.py proves against the in-memory ledger, checked here against
gonk's own ledger, so the commands the worker sends and the answers it parses are the real
ones: the labels filter on next, the JSON faces, a renewal, a release that leaves no comment
(so the hand-back is the newest comment), and a claim lost to another holder mid-run.

Skipped unless `ikigai-gonk` and `ikigai` are both on PATH (CI has neither; see
tests/test_integration.py). The agent is the fake one and gh is the stub: no real agent.
"""

from __future__ import annotations

import contextlib
import io
import threading
import time
import unittest

from support import gonk
from test_integration import GONK, IKIGAI, ScratchGonk
from worker_support import DISPATCHER, Rig, worker


def tool(repo, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = gonk.main(["--repo", str(repo), *argv])
    return code, out.getvalue()


@unittest.skipUnless(
    GONK and IKIGAI,
    "needs `ikigai-gonk` and `ikigai` on PATH to start a scratch gonk (CI has neither)",
)
class TheWorkerAgainstARealGonk(unittest.TestCase):
    def setUp(self):
        self.g = ScratchGonk(quic=False)
        self.addCleanup(self.g.stop)
        self.g.start()
        self.rig = Rig(self, ledger_name="acme", target=str(self.g.socket))
        self.ikigai = gonk.Ikigai(gonk.Target(socket=str(self.g.socket)))
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def file(self, title: str, labels: str, dispatch: bool = True) -> int:
        """File an item and, for one a worker is meant to take, dispatch it as the test's
        router over the socket, where `--as` names the author (the socket names nobody)."""
        code, out = tool(self.rig.checkout, "file", title, "--labels", labels)
        self.assertEqual(code, 0, out)
        number = int(out.split()[0].split("#")[1])
        routing = [w for w in labels.split(",") if not w.startswith("repo:")]
        if dispatch and routing:
            code, out = tool(
                self.rig.checkout, "dispatch", str(number), "--labels", ",".join(routing),
                "--as", DISPATCHER,
            )  # fmt: skip
            self.assertEqual(code, 0, out)
        return number

    def human_texts(self, item: dict) -> list[str]:
        """The item's comment texts, oldest first, less its dispatch."""
        ordered = sorted(item["comments"], key=lambda c: c["time"])
        return [c["text"] for c in ordered if gonk.parse_dispatch(c["text"]) is None]

    def item(self, number: int) -> dict:
        cfg = gonk.load(self.rig.checkout)
        item, err = gonk.item_json(cfg, self.ikigai, number)
        self.assertIsNone(err)
        return item

    def test_claim_renew_hand_back_and_nothing_closed(self):
        busy = self.file("Someone is on lib", "repo:lib")
        lib = self.file("Lib work", "worker:w1,repo:lib")
        other = self.file("Other work", "worker:w1,repo:other")
        # Held by a holder of another name, as another machine's claim would be.
        claim = f"sink urn:gk:iki:ledger:acme:claim item={busy} lease=30m"
        self.assertTrue(self.ikigai.run(claim, stdin="chris").ok)

        self.rig.agent_flags = ["--sleep", "2.5"]
        self.rig.configure(renew_every='"1s"', lease='"10s"')
        w = self.rig.worker(self.ikigai)
        take, skip, err = w.candidates()
        self.assertIsNone(err)
        self.assertEqual([i["number"] for i, _ in take], [other])  # lib is fenced by chris
        self.assertIn("is claimed by `chris`", dict(skip)[f"acme#{lib}"])

        outcome = w.run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertGreaterEqual(outcome.renewals, 1)
        item = self.item(other)
        self.assertIsNone(item["claim"])
        self.assertEqual(item["status"], "open")
        texts = self.human_texts(item)
        self.assertEqual(len(texts), 1, texts)  # the release added no comment of its own
        # (The stub gh files every pull request under its state's slug, acme/lib.)
        self.assertTrue(texts[0].startswith("Evidence: https://github.com/acme/lib/pull/1\n"))
        # The hand-back is the item's STATE on the ledger, not just the comment's prefix
        # (ikigai-ledger 0.8.0, ledger item 1199): the JSON item says it awaits its dispatcher.
        self.assertEqual(item["awaiting"], "dispatcher")
        self.assertTrue(worker.awaiting_dispatcher(item))
        self.assertEqual(self.rig.gh()["forbidden"], [])

        # Handed back and awaiting its dispatcher: not offered again, here or to another worker.
        take, skip, _ = self.rig.worker(self.ikigai).candidates()
        self.assertEqual(take, [])
        self.assertNotIn(f"acme#{other}", dict(skip))  # `next` excluded it entirely

    def test_the_dispatchers_rejection_clears_the_hand_back(self):
        # `rejects=true` is the dispatcher saying the evidence did not hold: the item stops
        # awaiting, and `next` offers it again (ikigai-ledger 0.8.0, ledger item 1199).
        n = self.file("Rejected", "worker:w1,repo:lib")
        self.rig.configure()
        self.assertEqual(self.rig.worker(self.ikigai).run_once().code, worker.HANDED_BACK)
        self.assertEqual(self.item(n)["awaiting"], "dispatcher")
        # `next` excluded it, so the worker is not even offered it.
        take, skip, _ = self.rig.worker(self.ikigai).candidates()
        self.assertEqual(take, [])
        self.assertNotIn(f"acme#{n}", dict(skip))

        cfg = gonk.load(self.rig.checkout)
        note = "The evidence does not hold: the test still fails."
        answer = self.ikigai.run(
            f"sink {cfg.resource('comment')} item={n} rejects=true", stdin=note
        )
        self.assertTrue(answer.ok, answer.text())
        item = self.item(n)
        self.assertIsNone(item["awaiting"])
        self.assertFalse(worker.awaiting_dispatcher(item))
        # `next` offers it again — the ledger, not the worker, decides that now. (This worker
        # still remembers handing it back, so its own `candidates` skips it: `worker.py forget
        # <N>` clears that.)
        doc, err = self.rig.worker(self.ikigai).ledger.next(["worker:w1"], [])
        self.assertIsNone(err)
        self.assertEqual([r["item"]["number"] for r in doc["ranking"]], [n])
        self.assertEqual(self.rig.worker(self.ikigai).candidates()[0], [])

    def test_a_claim_lost_mid_run(self):
        n = self.file("Taken over", "worker:w1,repo:lib")
        self.rig.agent_flags = ["--sleep", "60"]
        self.rig.configure(renew_every='"1s"')

        def take_it():
            while not (self.rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            self.ikigai.run(f"delete urn:gk:iki:ledger:acme:claim item={n}")
            self.ikigai.run(f"sink urn:gk:iki:ledger:acme:claim item={n}", stdin="someone-else")

        thread = threading.Thread(target=take_it)
        thread.start()
        started = time.monotonic()
        outcome = self.rig.worker(self.ikigai).run_once()
        thread.join()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertLess(time.monotonic() - started, 30)
        item = self.item(n)
        self.assertEqual(item["claim"]["holder"], "someone-else")
        self.assertTrue(any("lost its claim" in c["text"] for c in item["comments"]))

    def handed_back_without_evidence(self, n: int, *says: str) -> None:
        """The item is released, still open, and carries exactly one comment: the note."""
        item = self.item(n)
        self.assertIsNone(item["claim"])
        self.assertEqual(item["status"], "open")
        texts = self.human_texts(item)
        self.assertEqual(len(texts), 1, texts)
        for words in says:
            self.assertIn(words, texts[0])

    def test_a_failed_agent_leaves_one_comment_naming_the_failure(self):
        # Ledger item 1229: a release note piped to a DELETE never reached the ledger.
        n = self.file("Gives up", "worker:w1,repo:lib")
        self.rig.agent_flags = ["--no-pr", "--exit", "1"]
        self.rig.configure()
        outcome = self.rig.worker(self.ikigai).run_once()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.handed_back_without_evidence(n, "without evidence", "exited 1")

    def test_an_overrun_leaves_one_comment_naming_the_budget(self):
        n = self.file("Too big", "worker:w1,repo:lib")
        self.rig.agent_flags = ["--sleep", "60"]
        self.rig.configure(budget='"2s"')
        outcome = self.rig.worker(self.ikigai).run_once()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.handed_back_without_evidence(n, "its budget of 0m02s ran out")

    def test_a_failed_step_leaves_one_comment_naming_it(self):
        n = self.file("No origin", "worker:w1,repo:lib")
        self.rig.configure()
        self.rig.origin.rename(self.rig.origin.with_name("moved.git"))
        outcome = self.rig.worker(self.ikigai).run_once()
        self.assertEqual(outcome.code, worker.ERROR, outcome.summary)
        self.handed_back_without_evidence(n, "failed on", "git clone")


if __name__ == "__main__":
    unittest.main()
