"""Free disk and kept work (ledger item 2816): the worker reads the free space on the volume
holding its work root before each item, takes nothing under `[disk] floor_gb` and says why, and
prunes kept worktrees and logs older than `[disk] prune_after`, never one with an open pull
request. A fake free-space reader and a scratch work root, so nothing depends on this machine.
"""

from __future__ import annotations

import json
import os
import time
import unittest

from support import gonk
from worker_support import FakeLedger, Rig, worker

GB = worker.GB


class TheConfig(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)

    def test_the_defaults(self):
        self.rig.configure(disk="")
        d = worker.load_worker(gonk.load(self.rig.checkout)).disk
        self.assertEqual(d, {"floor_gb": 30.0, "every": 6 * 3600, "prune_after": 7 * 86400})

    def test_it_is_checked(self):
        for bad in ["floor_gb = -1", 'floor_gb = "30"', "floor_gb = true", 'every = "soon"',
                    'prune_after = "7"', 'floor = 30']:  # fmt: skip
            self.rig.configure(disk=bad)
            with self.assertRaises(gonk.ConfigError, msg=bad):
                worker.load_worker(gonk.load(self.rig.checkout))


class TheFloor(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        self.status = self.ledger.file("status: w1", ["worker:status"], dispatched_by=None)
        self.item = self.ledger.file("fix", ["worker:w1", "repo:lib"])
        path = self.rig.configure(disk="floor_gb = 30")
        with path.open("a") as f:
            f.write(f"\n[usage]\nstatus_item = {self.status}\n")
        self.w = self.rig.worker(self.ledger)
        self.free = [10 * GB]
        self.w.disk.read = lambda root: self.free[0]

    def disk_lines(self) -> list[str]:
        return [
            c["text"]
            for c in self.ledger.human_comments(self.status)
            if c["text"].startswith("Disk:")
        ]

    def test_below_the_floor_it_claims_nothing_and_says_why(self):
        outcome = self.w.run_once()
        self.assertEqual(outcome.code, worker.NOTHING)
        self.assertIn("10.0 GB, under the floor of 30 GB", outcome.summary)
        self.assertIsNone(self.ledger.items[self.item]["claim"])
        self.assertFalse(self.ledger.human_comments(self.item))
        [line] = self.disk_lines()
        self.assertTrue(line.startswith("Disk: free=10.0GB floor=30GB state=held read="), line)
        self.assertIn("taking no new item until space returns", line)
        # A second poll, still under the floor, says nothing new.
        self.assertEqual(self.w.run_once().code, worker.NOTHING)
        self.assertEqual(len(self.disk_lines()), 1)

    def test_above_the_floor_it_claims(self):
        self.free[0] = 31 * GB
        outcome = self.w.run_once()
        self.assertNotEqual(outcome.code, worker.NOTHING)
        self.assertEqual(outcome.number, self.item)
        [line] = self.disk_lines()
        self.assertTrue(line.startswith("Disk: free=31.0GB floor=30GB state=taking"), line)

    def test_it_resumes_on_its_own_once_space_returns(self):
        self.assertEqual(self.w.run_once().code, worker.NOTHING)
        self.free[0] = 40 * GB
        outcome = self.w.run_once()
        self.assertEqual(outcome.number, self.item)
        held, taking = self.disk_lines()
        self.assertIn("state=held", held)
        self.assertIn("state=taking", taking)

    def test_an_unreadable_disk_is_unknown_and_never_holds(self):
        def broken(root):
            raise OSError("no such volume")

        self.w.disk.read = broken
        self.assertEqual(self.w.run_once().number, self.item)
        [line] = self.disk_lines()
        self.assertIn("free=unknown", line)

    def test_a_failed_reading_while_held_releases_the_hold(self):
        self.assertEqual(self.w.run_once().code, worker.NOTHING)

        def broken(root):
            raise OSError("no such volume")

        self.w.disk.read = broken
        self.assertEqual(self.w.run_once().number, self.item)
        self.assertIn("free=unknown", self.disk_lines()[-1])

    def test_a_floor_of_zero_never_holds(self):
        self.w.disk.floor_gb = 0.0
        self.assertEqual(self.w.run_once().number, self.item)

    def test_the_real_reader_reads_the_nearest_existing_parent(self):
        self.assertGreater(worker.free_bytes(self.rig.home / "work" / "not" / "yet"), 0)


class Pruning(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.rig.configure()
        self.w = self.rig.worker(FakeLedger())
        self.repo = self.w.wc.repos["lib"]

    def kept(self, number: int, days: float):
        """A worktree as a failed run keeps it, with its log, last touched `days` ago."""
        ws = worker.make_workspace(self.w.root, self.repo, number)
        ws.log.write_text("the agent's output\n")
        ws.prompt.write_text("the brief\n")
        self.age(days, ws.path, ws.log, ws.prompt)
        return ws

    def age(self, days: float, *paths):
        then = time.time() - days * 86400
        for p in paths:
            os.utime(p, (then, then))

    def open_pr_on(self, branch: str, state: str = "OPEN") -> None:
        doc = self.rig.gh()
        number = len(doc.get("prs", [])) + 1
        doc.setdefault("prs", []).append(
            {"number": number, "url": f"https://github.com/acme/lib/pull/{number}",
             "state": state, "headRefName": branch, "headRefOid": "0" * 40}
        )  # fmt: skip
        self.rig.gh_state.write_text(json.dumps(doc))

    def branches(self) -> list[str]:
        out = worker.git("branch", "--format=%(refname:short)", cwd=self.w.root / "lib" / ".source")
        return out.stdout.split()

    def test_old_kept_work_goes_recent_work_and_open_prs_stay(self):
        old = self.kept(1, days=8)
        recent = self.kept(2, days=1)
        reviewed = self.kept(3, days=30)
        self.open_pr_on(reviewed.branch)
        merged = self.kept(4, days=8)
        self.open_pr_on(merged.branch, state="MERGED")  # only an OPEN pull request keeps it
        stray = self.w.root / "lib" / "item-9.log"  # the log of a worktree removed at hand-back
        stray.write_text("log\n")
        self.age(8, stray)
        fresh_log = self.w.root / "lib" / "item-10.log"
        fresh_log.write_text("log\n")

        removed = self.w.prune()

        self.assertEqual(sorted(p.name for p in removed), ["item-1", "item-4", "item-9"])
        for gone in (old.path, old.log, old.prompt, merged.path, merged.log, stray):
            self.assertFalse(gone.exists(), gone)
        for kept in (recent.path, recent.log, reviewed.path, reviewed.log, fresh_log):
            self.assertTrue(kept.exists(), kept)
        self.assertNotIn(old.branch, self.branches())
        self.assertIn(reviewed.branch, self.branches())
        listed = worker.git("worktree", "list", cwd=self.w.root / "lib" / ".source").stdout
        self.assertNotIn(str(old.path), listed)

    def test_a_worktree_whose_pull_requests_cannot_be_read_is_kept(self):
        ws = self.kept(1, days=8)
        self.w.wc.gh = ["false"]
        self.assertEqual(self.w.prune(), [])
        self.assertTrue(ws.path.exists())

    def test_a_repository_with_an_item_running_here_is_left_alone(self):
        ws = self.kept(1, days=8)
        lock = worker.RepoLock(self.w.wc.state_dir, "lib")
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        self.assertEqual(self.w.prune(), [])
        self.assertTrue(ws.path.exists())

    def test_prune_after_zero_keeps_everything(self):
        ws = self.kept(1, days=80)
        self.w.prune_after = 0
        self.assertEqual(self.w.prune(), [])
        self.assertTrue(ws.path.exists())

    def test_the_loop_prunes_when_it_starts_then_once_a_day(self):
        ws = self.kept(1, days=8)
        polls = []

        def once():
            polls.append(self.w.pruned_at)
            if len(polls) == 2:
                self.w.shutdown.set()
            return worker.Outcome(worker.NOTHING, "nothing to take")

        self.w.run_once = once
        self.w.wc.poll = 0
        self.w.loop()
        self.assertFalse(ws.path.exists())
        first, second = polls
        self.assertIsNotNone(first)
        self.assertEqual(first, second)  # not again within the day


if __name__ == "__main__":
    unittest.main()
