"""The shared pool (ledger item 2793): `worker:pool`, read by every per-agent loop on every
machine after its own machine label, `worker:mac` for the Macs, and `gonk.py dispatch`, whose
default target is the pool. Two loops pulling the pool never claim one item, because the claim
is the ledger's compare-and-set. An agent's cargo target directory is its own worktree's.

Hermetic: the in-memory ledger, the stub gh and the fake agent (tests/worker_support.py).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

from support import gonk
from worker_support import DISPATCHER, FakeLedger, Rig, worker

CLAUDE = {"labels": '["worker:more", "agent:claude"]', "holder": '"more-claude"'}
SECOND_CLAUDE = {"labels": '["worker:second", "agent:claude"]', "holder": '"second-claude"'}


class Quiet(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeLedger()
        self.rig = Rig(self)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def loop(self, rig: Rig | None = None, **settings):
        rig = rig or self.rig
        return rig.worker(self.ledger, rig.configure(**{**CLAUDE, **settings}))

    def taken(self, loop) -> list[int]:
        take, _, err = loop.candidates()
        self.assertIsNone(err)
        return [item["number"] for item, _ in take]


class WhatALoopReads(Quiet):
    def load(self, platform="linux", **settings):
        self.rig.configure(**settings)
        with mock.patch.object(worker.sys, "platform", platform):
            return worker.load_worker(gonk.load(self.rig.checkout))

    def test_a_per_agent_loop_reads_the_pool_after_its_own_label(self):
        wc = self.load(**CLAUDE)
        self.assertEqual(
            wc.selectors,
            [["worker:more", "agent:claude"], ["worker:pool", "agent:claude"]],
        )

    def test_a_mac_also_reads_worker_mac_before_the_pool(self):
        wc = self.load("darwin", **CLAUDE)
        self.assertEqual(
            wc.selectors,
            [
                ["worker:more", "agent:claude"],
                ["worker:mac", "agent:claude"],
                ["worker:pool", "agent:claude"],
            ],
        )

    def test_pool_can_be_set_and_emptied(self):
        self.assertEqual(self.load("darwin", **CLAUDE, pool="[]").pool, [])
        self.assertEqual(self.load(**CLAUDE, pool='["worker:mac"]').pool, ["worker:mac"])

    def test_a_loop_naming_no_agent_reads_no_pool(self):
        # It cannot tell an item for agent:codex from one for its own agent.
        self.assertEqual(self.load().selectors, [["worker:w1"]])
        with self.assertRaises(gonk.ConfigError) as refused:
            self.load(pool='["worker:pool"]')
        self.assertIn("agent:", str(refused.exception))

    def test_a_pool_entry_is_a_worker_label(self):
        with self.assertRaises(gonk.ConfigError):
            self.load(**CLAUDE, pool='["agent:codex"]')

    def test_its_status_line_says_so_and_reads_back(self):
        text = gonk.worker_status_text("h", None, ["worker:a"], ["lib"], pool=["worker:pool"])
        self.assertEqual(gonk.parse_worker_status(text)["pool"], ["worker:pool"])
        self.assertNotIn("pool=", gonk.worker_status_text("h", None, ["worker:a"], ["lib"]))


class TheRouting(Quiet):
    def test_own_label_first_then_the_pool(self):
        pool = self.ledger.file("Pool", ["worker:pool", "agent:claude", "repo:lib"])
        own = self.ledger.file("Own", ["worker:more", "agent:claude", "repo:other"])
        self.assertEqual(self.taken(self.loop()), [own, pool])

    def test_the_pool_keeps_the_agent_a_dispatch_asked_for(self):
        self.ledger.file("For codex", ["worker:pool", "agent:codex", "repo:lib"])
        self.assertEqual(self.taken(self.loop()), [])

    def test_pinning_after_the_pool_takes_it_out_of_the_pool(self):
        n = self.ledger.file("Fix", ["worker:pool", "agent:claude", "repo:lib"])
        item = self.ledger.items[n]
        item["labels"] = sorted([*item["labels"], "worker:second"])
        self.ledger.dispatch(n, labels=["worker:second", "agent:claude", "repo:lib"])
        loop = self.loop()
        self.assertEqual(self.taken(loop), [])
        self.assertEqual(self.taken(self.loop(Rig(self), **SECOND_CLAUDE)), [n])

    def test_sending_a_pinned_item_to_the_pool_lets_any_loop_take_it(self):
        n = self.ledger.file("Fix", ["worker:second", "agent:claude", "repo:lib"])
        item = self.ledger.items[n]
        item["labels"] = sorted([*item["labels"], "worker:pool"])
        self.ledger.dispatch(n, labels=["worker:pool", "agent:claude", "repo:lib"])
        self.assertEqual(self.taken(self.loop()), [n])
        self.assertEqual(self.taken(self.loop(Rig(self), **SECOND_CLAUDE)), [n])

    def test_a_pool_label_alone_routes_nothing(self):
        n = self.ledger.file(
            "Labeled", ["worker:pool", "agent:claude", "repo:lib"], dispatched_by=None
        )
        loop = self.loop()
        self.assertEqual(self.taken(loop), [])
        self.assertEqual(self.ledger.human_comments(n), [])

    def test_an_item_read_by_two_selectors_is_one_candidate(self):
        self.ledger.file("Both", ["worker:more", "worker:pool", "agent:claude", "repo:lib"])
        self.assertEqual(self.taken(self.loop()), [1])

    def test_a_pool_item_edited_after_its_dispatch_is_explained_and_says_the_pool(self):
        n = self.ledger.file("Fix", ["worker:pool", "agent:claude", "repo:lib"])
        self.ledger.items[n]["body"] = "edited"
        self.loop().run_once()
        said = self.ledger.human_comments(n)[0]["text"]
        self.assertIn("changed after its dispatch", said)
        self.assertIn("--labels worker:pool,agent:claude", said)

    def test_a_loop_runs_a_pool_item_end_to_end(self):
        n = self.ledger.file("Fix", ["worker:pool", "agent:claude", "repo:lib"])
        self.assertEqual(self.loop().run_once().code, worker.HANDED_BACK)
        self.assertEqual(self.ledger.items[n]["awaiting"], "dispatcher")

    def test_the_status_item_is_found_through_the_pool(self):
        status = self.ledger.file("Status", ["worker-status:more"], dispatched_by=None)
        self.loop().announce()
        self.assertIn("worker-status:pool", self.ledger.items[status]["labels"])
        posted = self.ledger.items[status]["comments"][-1]["text"]
        self.assertRegex(posted, r"pool=\S*worker:pool")


class TwoLoops(Quiet):
    """The test the item asks for: two loops pulling the pool never claim one item."""

    def test_both_read_it_as_free_and_only_one_claims_it(self):
        n = self.ledger.file("Fix", ["worker:pool", "agent:claude", "repo:lib"])
        one, two = self.loop(), self.loop(Rig(self), **SECOND_CLAUDE)
        self.assertEqual(self.taken(one), [n])
        self.assertEqual(self.taken(two), [n])  # the race: both read it free
        item = dict(self.ledger.items[n])
        self.assertTrue(one.ledger.claim(n, "more-claude", "30m").ok)
        outcome = two.work(item, two.wc.repository("lib")[0])
        self.assertEqual(outcome.code, worker.NOTHING)
        self.assertTrue(outcome.claim_refused)
        self.assertEqual(self.ledger.items[n]["claim"]["holder"], "more-claude")

    def test_the_loser_of_the_race_takes_the_next_item_at_once(self):
        lost = self.ledger.file("Lost", ["worker:pool", "agent:claude", "repo:lib"])
        won = self.ledger.file("Won", ["worker:pool", "agent:claude", "repo:other"])
        loop = self.loop()
        real = self.ledger.answer

        def taken_first(command, stdin):
            if command.split()[1].endswith(":claim") and f"item={lost}" in command.split():
                self.ledger.items[lost]["claim"] = {
                    "holder": "second-claude",
                    "kind": "machine",
                    "expires": "2099-01-01T00:00:00.000Z",
                }
            return real(command, stdin)

        with mock.patch.object(self.ledger, "answer", taken_first):
            outcome = loop.run_once()
        self.assertEqual(outcome.number, won)
        self.assertEqual(outcome.code, worker.HANDED_BACK)

    def test_racing_loops_on_two_machines_claim_each_item_once(self):
        labels = ["worker:pool", "agent:claude"]
        numbers = [self.ledger.file(f"Item {i}", [*labels, f"repo:r{i}"]) for i in range(6)]
        rigs = [self.rig, Rig(self)]
        settings = [CLAUDE, SECOND_CLAUDE]
        loops = []
        for rig, s in zip(rigs, settings, strict=True):
            path = rig.configure(**s)
            text = path.read_text()
            for i in range(6):
                text += f'\n[repos.r{i}]\nremote = "{rig.origin}"\ngithub = "acme/r{i}"\n'
            path.write_text(text)
            loops.append(rig.worker(self.ledger, path))
        granted: dict[int, list[str]] = {}
        real = self.ledger.answer

        def recorded(command, stdin):
            answer = real(command, stdin)
            words = command.split()
            if answer.ok and words[:1] == ["sink"] and words[1].endswith(":claim"):
                if "renew=true" not in words:
                    number = int(next(w[5:] for w in words if w.startswith("item=")))
                    granted.setdefault(number, []).append(stdin.strip())
            return answer

        self.ledger.answer = recorded
        start = threading.Barrier(len(loops))
        outcomes: list[list] = [[] for _ in loops]

        def pull(index: int) -> None:
            start.wait()
            for _ in numbers:
                outcomes[index].append(loops[index].run_once())

        threads = [threading.Thread(target=pull, args=(i,)) for i in range(len(loops))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        worked = [o.number for per in outcomes for o in per if o.code == worker.HANDED_BACK]
        self.assertEqual(sorted(worked), numbers, "every item worked by exactly one loop")
        self.assertEqual(sorted(granted), numbers)
        for number, holders in granted.items():
            self.assertEqual(len(holders), 1, f"item {number} was granted to {holders}")


class TheDispatch(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        self.n = self.ledger.file("Fix", ["repo:lib"], dispatched_by=None)

    def dispatch(self, *args):
        out = io.StringIO()
        argv = ["--repo", str(self.rig.checkout), "dispatch", str(self.n), "--as", DISPATCHER]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = gonk.main([*argv, *args], self.ledger)
            except gonk.ConfigError as e:
                return 2, str(e)
        return code, out.getvalue()

    def named(self) -> list[str]:
        texts = [c["text"] for c in self.ledger.items[self.n]["comments"]]
        return gonk.parse_dispatch(texts[-1])[0]

    def test_the_pool_is_the_default_target(self):
        code, said = self.dispatch("--labels", "agent:codex")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.named(), ["worker:pool", "agent:codex", "repo:lib"])
        self.assertIn("worker:pool", self.ledger.items[self.n]["labels"])

    def test_a_host_label_pins(self):
        code, said = self.dispatch("--labels", "worker:more,agent:codex")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.named(), ["worker:more", "agent:codex", "repo:lib"])
        self.assertNotIn("worker:pool", self.ledger.items[self.n]["labels"])

    def test_worker_mac_is_a_target_too(self):
        code, said = self.dispatch("--labels", "worker:mac,agent:claude")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.named(), ["worker:mac", "agent:claude", "repo:lib"])

    def test_the_pool_needs_an_agent_or_no_loop_would_take_it(self):
        for args in ([], ["--labels", "worker:pool"], ["--labels", "worker:mac"]):
            code, said = self.dispatch(*args)
            self.assertNotEqual(code, 0, args)
            self.assertIn("agent:<kind>", said)
        self.assertEqual(self.ledger.items[self.n]["comments"], [])
        self.assertEqual(self.ledger.items[self.n]["labels"], ["repo:lib"])

    def test_coverage_counts_a_loop_that_reads_the_pool(self):
        status = self.ledger.file("Status", ["worker-status:pool"], dispatched_by=None)
        line = gonk.worker_status_text(
            "more-claude", "claude", ["worker:more", "agent:claude"], ["lib"], pool=["worker:pool"]
        )
        self.ledger.add_comment(status, line)
        code, said = self.dispatch("--labels", "agent:claude")
        self.assertEqual(code, 0, said)
        self.assertNotIn("warning: could not check", said)
        self.assertNotIn("lists repository", said)

    def test_coverage_warns_when_no_loop_reads_the_pool(self):
        status = self.ledger.file("Status", ["worker-status:pool"], dispatched_by=None)
        line = gonk.worker_status_text(
            "more-claude", "claude", ["worker:more", "agent:claude"], ["lib"]
        )
        self.ledger.add_comment(status, line)
        code, said = self.dispatch("--labels", "agent:claude")
        self.assertEqual(code, 0, said)
        self.assertIn("no `Worker:` line for a worker with labels worker:pool", said)


class TheNames(unittest.TestCase):
    def test_a_machine_cannot_be_named_for_a_shared_label(self):
        for name in ("pool", "mac"):
            with self.assertRaises(gonk.ConfigError) as refused:
                worker.agent_config("", name, "claude", "sample", True)
            self.assertIn(f"worker:{name}", str(refused.exception))


class TheCargoTarget(Quiet):
    """One target directory per worktree: an inherited shared one never reaches an agent."""

    def test_the_agent_builds_in_its_own_worktree(self):
        record = self.rig.tmp / "env"
        script = self.rig.tmp / "agent.py"
        script.write_text(
            "import json, os, sys\n"
            f"open({str(record)!r}, 'w').write(json.dumps("
            "[os.environ.get('CARGO_TARGET_DIR'), os.getcwd()]))\n"
        )
        path = self.rig.configure(**CLAUDE)
        text = path.read_text().splitlines()
        at = next(i for i, line in enumerate(text) if line.startswith("command = "))
        text[at] = f"command = {json.dumps([worker.sys.executable, str(script), '{prompt}'])}"
        path.write_text("\n".join(text) + "\n")
        self.ledger.file("Fix", ["worker:pool", "agent:claude", "repo:lib"])
        loop = self.rig.worker(self.ledger, path)
        with mock.patch.dict(os.environ, {"CARGO_TARGET_DIR": "/shared/target"}):
            loop.run_once()
        target, cwd = json.loads(record.read_text())
        self.assertEqual(Path(target).resolve(), Path(cwd).resolve() / "target")


if __name__ == "__main__":
    unittest.main()
