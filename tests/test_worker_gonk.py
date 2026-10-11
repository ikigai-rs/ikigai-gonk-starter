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
from worker_support import Rig, worker


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

    def file(self, title: str, labels: str) -> int:
        code, out = tool(self.rig.checkout, "file", title, "--labels", labels)
        self.assertEqual(code, 0, out)
        return int(out.split()[0].split("#")[1])

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
        texts = [c["text"] for c in sorted(item["comments"], key=lambda c: c["time"])]
        self.assertEqual(len(texts), 1, texts)  # the release added no comment of its own
        # (The stub gh files every pull request under its state's slug, acme/lib.)
        self.assertTrue(texts[0].startswith("Evidence: https://github.com/acme/lib/pull/1\n"))
        self.assertTrue(worker.awaiting_dispatcher(item))
        self.assertEqual(self.rig.gh()["forbidden"], [])

        # Handed back and awaiting its dispatcher: not offered again, here or to another worker.
        take, skip, _ = self.rig.worker(self.ikigai).candidates()
        self.assertEqual(take, [])
        self.assertIn("waits for its dispatcher", dict(skip)[f"acme#{other}"])

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
        texts = [c["text"] for c in item["comments"]]
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
