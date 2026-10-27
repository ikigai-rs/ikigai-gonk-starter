"""A skip the dispatcher can act on is said on the item, and the worker's repositories on its
status item (ledger item 2393).

A dispatched item for a repository the worker is not configured for sat unclaimed for nine hours
(ledger item 2349): the reason was in the worker's journal and nowhere a dispatcher looks. So a
skip of an item DISPATCHED to this worker, for a reason the dispatcher can fix, is posted on that
item once per reason (remembered in the loop's state directory, so a restart does not repeat it),
and the worker's repositories and labels are posted on its `status_item` at start.

Hermetic: the in-memory ledger, the stub gh and the fake agent (tests/worker_support.py).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest

from support import gonk
from worker_support import DISPATCHER, FakeLedger, Rig, worker


class Quiet(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def notes(self, n: int) -> list[str]:
        return [c["text"] for c in self.ledger.human_comments(n)]


class SkipNotices(Quiet):
    def worker(self):
        self.rig.configure()
        return self.rig.worker(self.ledger)

    def test_an_unconfigured_repository_is_explained_once_across_polls_and_restarts(self):
        n = self.ledger.file("Fix the engine", ["worker:w1", "repo:engine"])
        w = self.worker()
        self.assertEqual(w.run_once().code, worker.NOTHING)
        [note] = self.notes(n)
        self.assertIn("gonk worker `w1`", note)
        self.assertIn("repo:engine is not one of this worker's repositories", note)
        self.assertIn("[repos.engine]", note)
        self.assertIn('github = "acme/engine"', note)
        self.assertIn(".gonk/worker.toml", note)
        self.assertNotIn(str(self.rig.tmp), note)  # no path outside the work root
        self.assertNotIn(DISPATCHER, note)
        w.run_once()  # the next poll
        self.worker().run_once()  # a restart
        self.assertEqual(len(self.notes(n)), 1)
        self.assertIsNone(self.ledger.items[n]["claim"])

    def test_a_loop_names_itself_and_its_own_file(self):
        n = self.ledger.file("Fix the engine", ["worker:w1", "repo:engine"])
        path = self.rig.configure("worker-claude.toml")
        self.rig.worker(self.ledger, path).run_once()
        [note] = self.notes(n)
        self.assertIn("(loop `claude`)", note)
        self.assertIn(".gonk/worker-claude.toml", note)

    def test_an_edit_after_the_dispatch_is_explained_and_a_new_reason_is_said_again(self):
        n = self.ledger.file("Fix it", ["worker:w1", "repo:lib"], body="old")
        self.ledger.items[n]["body"] = "new"
        self.worker().run_once()
        [note] = self.notes(n)
        self.assertIn("changed after its dispatch", note)
        self.assertIn("gonk.py dispatch", note)
        # Dispatched again, then edited again: a new dispatch, a new reason, one more note.
        self.ledger.dispatch(n)
        self.ledger.items[n]["body"] = "newer"
        self.worker().run_once()
        self.worker().run_once()
        self.assertEqual(len(self.notes(n)), 2)

    def test_skips_that_are_not_the_dispatchers_to_fix_stay_in_the_journal(self):
        undispatched = self.ledger.file("x", ["worker:w1", "repo:engine"], dispatched_by=None)
        elsewhere = self.ledger.file("y", ["worker:w1", "repo:engine"], dispatched_by=None)
        self.ledger.dispatch(elsewhere, labels=["worker:w2", "repo:engine"])
        by_someone = self.ledger.file("z", ["worker:w1", "repo:engine"], dispatched_by=None)
        self.ledger.dispatch(by_someone, author="urn:iki:gonk:passkey:someone-else")
        claimed = self.ledger.file("c", ["worker:w1", "repo:engine"])
        self.ledger.force_claim(claimed, "someone")
        busy = self.ledger.file("b", ["worker:w1", "repo:lib"])
        held = self.ledger.file("h", ["worker:x", "repo:lib"])
        self.ledger.force_claim(held, "someone")
        self.worker().run_once()
        for n in (undispatched, elsewhere, by_someone, claimed, busy):
            self.assertEqual(self.notes(n), [], n)

    def test_an_item_awaiting_its_dispatcher_gets_no_note(self):
        # On a ledger older than 0.8.0 the hand-back is the newest comment's prefix, so a note
        # after it would make the item ready again.
        self.ledger = FakeLedger(older_comment=True)
        n = self.ledger.file("Fix the engine", ["worker:w1", "repo:engine"])
        self.ledger.add_comment(n, "Evidence: https://example.invalid/pr/1", author="w1")
        self.worker().run_once()
        self.assertEqual(self.notes(n), ["Evidence: https://example.invalid/pr/1"])

    def test_a_usage_hold_is_explained_on_the_item_it_holds(self):
        n = self.ledger.file("fix", ["worker:w1", "repo:lib"])
        path = self.rig.configure()
        script = self.rig.tmp / "usage.py"
        script.write_text("print('{\"percent\": 95}')\n")
        with path.open("a") as f:
            f.write(f"\n[usage]\ncommand = {json.dumps([sys.executable, str(script)])}\n")
        w = self.rig.worker(self.ledger)
        self.assertEqual(w.run_once().code, worker.NOTHING)
        w.run_once()
        self.rig.worker(self.ledger).run_once()
        [note] = self.notes(n)
        self.assertIn("95% of its weekly limit", note)
        self.assertIsNone(self.ledger.items[n]["claim"])

    def test_a_refused_comment_is_tried_again_at_the_next_poll(self):
        n = self.ledger.file("Fix the engine", ["worker:w1", "repo:engine"])
        w = self.worker()
        self.ledger.refuse_comments = 1
        w.run_once()
        self.assertEqual(self.notes(n), [])
        w.run_once()
        self.assertEqual(len(self.notes(n)), 1)


class StatusBlock(Quiet):
    def setUp(self):
        super().setUp()
        self.status = self.ledger.file("status: w1", ["worker:status"], dispatched_by=None)

    def configure(self, **overrides):
        path = self.rig.configure(**overrides)
        with path.open("a") as f:
            f.write(f"\n[usage]\nstatus_item = {self.status}\n")
        return path

    def test_the_repositories_and_labels_are_posted_at_start_and_when_they_change(self):
        self.configure()
        self.rig.worker(self.ledger).announce()
        [block] = self.notes(self.status)
        self.assertEqual(
            gonk.parse_worker_status(block),
            {"holder": "w1", "labels": ["worker:w1"], "repos": ["lib", "other"]},
        )
        self.assertNotIn(str(self.rig.tmp), block)
        self.rig.worker(self.ledger).announce()  # a restart with the same configuration
        self.assertEqual(len(self.notes(self.status)), 1)
        self.configure(labels='["worker:w1", "agent:claude"]')
        self.rig.worker(self.ledger).announce()
        self.assertEqual(len(self.notes(self.status)), 2)
        self.assertEqual(
            gonk.parse_worker_status(self.notes(self.status)[-1])["labels"],
            ["worker:w1", "agent:claude"],
        )

    def test_without_a_status_item_nothing_is_posted(self):
        self.rig.configure()
        self.rig.worker(self.ledger).announce()
        self.assertEqual(self.notes(self.status), [])

    def test_the_block_round_trips(self):
        text = gonk.worker_status_text("w1", "claude", ["worker:w1"], ["lib", "engine"])
        self.assertEqual(
            gonk.parse_worker_status(text),
            {"holder": "w1", "loop": "claude", "labels": ["worker:w1"], "repos": ["lib", "engine"]},
        )
        self.assertIsNone(gonk.parse_worker_status("Usage: agent=claude unknown"))


if __name__ == "__main__":
    unittest.main()
