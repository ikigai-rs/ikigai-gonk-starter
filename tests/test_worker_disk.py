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


class Reclaiming(unittest.TestCase):
    """What the ledger says of a kept worktree's item decides, whatever the worktree's age
    (ledger item 2937). A fake ledger answer and a scratch work root."""

    def setUp(self):
        self.rig = Rig(self)
        self.rig.configure()
        self.ledger = FakeLedger()
        self.w = self.rig.worker(self.ledger)
        self.w.wc.labels = ["worker:none"]  # polls here take nothing
        self.w.wc.pool = []
        self.repo = self.w.wc.repos["lib"]
        self.me = self.w.cfg.holder

    def kept(self, made_by: str | None = None, label: str = "repo:lib"):
        """An open item and the worktree a run kept of it, with a build in it."""
        number = self.ledger.file("fix", ["worker:other", label])
        ws = worker.make_workspace(self.w.root, self.repo, number)
        ws.holder.write_text(f"{made_by or self.me}\n")
        ws.prompt.write_text("the brief\n")
        ws.log.write_text("the agent's output\n")
        (ws.path / "target").mkdir()
        (ws.path / "target" / "big.rlib").write_bytes(b"0" * 4096)
        return number, ws

    def worktrees(self) -> str:
        return worker.git("worktree", "list", cwd=self.w.root / "lib" / ".source").stdout

    def under_the_floor(self, reads: list[int] | None = None) -> None:
        """Under the floor: at each of `reads` in turn, or for good."""
        self.w.disk.floor_gb = 30.0
        left = reads
        self.w.disk.read = lambda root: left.pop(0) if left else 10 * GB

    def test_a_closed_items_worktree_goes_however_recent(self):
        number, ws = self.kept()
        self.ledger.items[number]["status"] = "closed"
        self.w.run_once()
        self.assertFalse(ws.path.exists())
        self.assertNotIn(str(ws.path), self.worktrees())
        out = worker.git("branch", "--format=%(refname:short)", cwd=ws.source).stdout
        self.assertNotIn(ws.branch, out)
        for gone in (ws.prompt, ws.holder):
            self.assertFalse(gone.exists(), gone)
        self.assertTrue(ws.log.exists())  # prune_after's to take

    def test_a_second_attempts_worktree_is_the_same_items(self):
        number, first = self.kept()
        second = worker.make_workspace(self.w.root, self.repo, number)
        self.assertEqual(second.path.name, f"item-{number}.2")
        self.ledger.items[number]["status"] = "closed"
        self.w.run_once()
        self.assertFalse(first.path.exists())
        self.assertFalse(second.path.exists())

    def test_a_closed_item_with_an_open_pull_request_still_goes(self):
        # Its pushed branch stays on GitHub; only age-based pruning waits on an open PR.
        number, ws = self.kept()
        self.ledger.items[number]["status"] = "closed"
        doc = self.rig.gh()
        doc.setdefault("prs", []).append(
            {"number": 1, "url": "https://github.com/acme/lib/pull/1", "state": "OPEN",
             "headRefName": ws.branch, "headRefOid": "0" * 40}
        )  # fmt: skip
        self.rig.gh_state.write_text(json.dumps(doc))
        self.w.run_once()
        self.assertFalse(ws.path.exists())

    def test_a_claim_that_moved_to_another_holder_goes(self):
        number, ws = self.kept()
        self.ledger.force_claim(number, "someone-else")
        self.w.run_once()
        self.assertFalse(ws.path.exists())

    def test_a_claim_this_loop_holds_is_never_touched(self):
        number, ws = self.kept()
        self.ledger.force_claim(number, self.me)
        self.under_the_floor()
        self.w.run_once()
        self.assertTrue((ws.path / "target" / "big.rlib").exists())

    def test_the_claim_of_the_holder_that_made_it_is_never_touched(self):
        # A sibling loop's: it died holding this item, and this worktree is that claim's work.
        number, ws = self.kept(made_by="sibling")
        self.ledger.force_claim(number, "sibling")
        self.under_the_floor()
        self.w.run_once()
        self.assertTrue((ws.path / "target" / "big.rlib").exists())

    def test_another_holders_claim_on_a_worktree_of_unknown_origin_is_left(self):
        # Made before the record of who made it existed; it could be a sibling's.
        number, ws = self.kept()
        ws.holder.unlink()
        self.ledger.force_claim(number, "someone-else")
        self.under_the_floor()
        self.w.run_once()
        self.assertTrue((ws.path / "target" / "big.rlib").exists())

    def test_no_answer_from_the_ledger_keeps_it(self):
        number, ws = self.kept()
        del self.ledger.items[number]
        self.under_the_floor()
        self.w.run_once()
        self.assertTrue((ws.path / "target" / "big.rlib").exists())

    def test_an_item_with_no_status_keeps_it(self):
        number, ws = self.kept()
        del self.ledger.items[number]["status"]
        self.under_the_floor()
        self.w.reclaim()
        self.assertTrue((ws.path / "target" / "big.rlib").exists())

    def test_a_number_that_is_not_this_repositorys_item_keeps_it(self):
        number, ws = self.kept(label="repo:other")
        self.ledger.items[number]["status"] = "closed"
        self.w.run_once()
        self.assertTrue(ws.path.exists())

    def test_an_open_unclaimed_item_keeps_its_worktree_and_build_over_the_floor(self):
        _, ws = self.kept()
        self.w.disk.floor_gb = 30.0
        self.w.disk.read = lambda root: 40 * GB
        self.w.run_once()
        self.assertTrue((ws.path / "target" / "big.rlib").exists())

    def test_under_the_floor_an_open_unclaimed_item_loses_its_build_only(self):
        _, ws = self.kept()
        self.under_the_floor()
        self.w.run_once()
        self.assertFalse((ws.path / "target").exists())
        self.assertTrue((ws.path / ".git").exists())
        self.assertTrue(ws.prompt.exists())

    def test_builds_go_one_at_a_time_until_the_floor_is_met(self):
        _, first = self.kept()
        _, second = self.kept()
        # Read at the start of the reclaim's thinning, then again after the first build goes.
        self.under_the_floor([10 * GB, 40 * GB, 40 * GB, 40 * GB])
        self.w.run_once()
        self.assertFalse((first.path / "target").exists())
        self.assertTrue((second.path / "target").exists())

    def test_removing_closed_work_comes_before_dropping_builds(self):
        closed, gone = self.kept()
        self.ledger.items[closed]["status"] = "closed"
        _, open_one = self.kept()
        self.w.disk.floor_gb = 30.0
        self.w.disk.read = lambda root: 10 * GB if gone.path.exists() else 40 * GB
        self.w.run_once()
        self.assertFalse(gone.path.exists())
        self.assertTrue((open_one.path / "target").exists())

    def test_the_floor_reports_what_reclaiming_left(self):
        status = self.ledger.file("status: w1", ["worker:status"], dispatched_by=None)
        self.w.disk.status_item = status
        number, ws = self.kept()
        self.ledger.items[number]["status"] = "closed"
        self.w.disk.floor_gb = 30.0
        self.w.disk.read = lambda root: 10 * GB if ws.path.exists() else 40 * GB
        self.assertNotIn("under the floor", self.w.run_once().summary)
        [line] = [c["text"] for c in self.ledger.human_comments(status)]
        self.assertIn("state=taking", line)

    def test_an_item_running_on_this_machine_is_left_alone(self):
        number, ws = self.kept()
        self.ledger.items[number]["status"] = "closed"
        lock = worker.RepoLock(self.w.wc.state_dir, "lib")
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        self.w.reclaim()
        self.assertTrue(ws.path.exists())

    def test_keep_and_a_prune_after_of_zero_keep_the_worktree(self):
        number, ws = self.kept()
        self.ledger.items[number]["status"] = "closed"
        self.w.wc.keep = True
        self.w.run_once()
        self.assertTrue(ws.path.exists())
        self.w.wc.keep = False
        self.w.prune_after = 0
        self.w.run_once()
        self.assertTrue(ws.path.exists())

    def test_a_run_records_who_made_its_worktree_and_a_lost_claim_is_reclaimed(self):
        self.w.wc.labels = ["worker:w1"]
        number = self.ledger.file("fix", ["worker:w1", "repo:lib"])
        seen = {}
        real = self.w.watch

        def lost_while_running(proc, started, beat):
            seen["holder"] = (self.w.root / "lib" / f"item-{number}.holder").read_text().strip()
            self.ledger.force_claim(number, "someone-else")
            beat.lost.set()
            beat.why = "someone-else holds it"
            return real(proc, started, beat)

        self.w.watch = lost_while_running
        outcome = self.w.run_once()
        self.assertEqual(seen["holder"], self.me)
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK)
        tree = self.w.root / "lib" / f"item-{number}"
        self.assertTrue(tree.exists())
        self.assertIn("until the item closes or another holder claims it",
                      self.ledger.human_comments(number)[-1]["text"])  # fmt: skip
        self.w.run_once()
        self.assertFalse(tree.exists())


if __name__ == "__main__":
    unittest.main()
