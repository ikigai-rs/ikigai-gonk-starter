"""How hard one worker loop polls the ledger (ledger item 4102): an idle loop's wait grows from
`poll` to `poll_max` while nothing it is offered changes, and a kept worktree's item is asked
about at most every `[disk] reclaim_every`. The loop runs for an hour of a fake clock against
the in-memory ledger, and the test counts what it asked, by resource.

Run it with GONK_POLLING_REPORT=1 to print the counts (the numbers the pull request quotes).
"""

from __future__ import annotations

import os
import shlex
import time
import types
import unittest
from collections import Counter
from unittest import mock

from worker_support import DISPATCHER, FakeLedger, Rig, worker

HOUR = 3600


def kind(command: str) -> str:
    """`next`, `items`, `item` or the verb and resource of anything else."""
    verb, resource, *_ = shlex.split(command)
    name = resource.rsplit(":", 1)[-1]
    if resource.split(":")[-2] == "item":
        return "item"
    return name if verb == "source" else f"{verb} {name}"


class FakeClock:
    """A monotonic clock the loop's waits move forward, and that stops the loop at an hour."""

    def __init__(self, w: worker.Worker, until: float) -> None:
        self.now, self.until, self.w = 0.0, until, w
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wait(self, seconds: float | None = None) -> bool:
        self.waits.append(seconds)
        self.now += seconds or 0
        if self.now >= self.until:
            self.w.shutdown.set()
        return self.w.shutdown.is_set()


def an_hour(w: worker.Worker, ledger: FakeLedger) -> tuple[Counter, list[float]]:
    clock = FakeClock(w, HOUR)
    fake_time = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time) if k[0] != "_"})
    fake_time.monotonic = clock.monotonic
    w.shutdown.wait = clock.wait
    ledger.calls.clear()
    with mock.patch.object(worker, "time", fake_time):
        w.loop()
    return Counter(kind(c) for c, _ in ledger.calls), clock.waits


class OneLoopForAnHour(unittest.TestCase):
    """One loop with an `agent:` label, so it reads `next` twice a poll (its own labels and
    `worker:pool`), and one kept worktree whose item another machine's loop now holds: the
    shape of the polling ledger item 4102 measured."""

    def loop(self, **settings) -> tuple[worker.Worker, FakeLedger]:
        rig = Rig(self)
        rig.configure(labels='["worker:w1", "agent:claude"]', poll='"60s"', **settings)
        ledger = FakeLedger()
        w = rig.worker(ledger)
        w.wc.pool = ["worker:pool"]  # as on Linux, wherever the test runs
        number = ledger.file("kept", ["worker:other", "repo:lib"])
        ws = worker.make_workspace(w.root, w.wc.repos["lib"], number)
        ws.holder.write_text("sibling\n")
        ledger.force_claim(number, "sibling")  # the holder that made it: never touched
        return w, ledger

    def report(self, name: str, counts: Counter) -> None:
        if os.environ.get("GONK_POLLING_REPORT"):
            print(f"\n{name}: {sum(counts.values())} requests/hour {dict(sorted(counts.items()))}")

    def test_idle_before_the_backoff_asks_every_minute(self):
        # Today's behaviour, kept by poll_max = poll and reclaim_every = 0s.
        w, ledger = self.loop(poll_max='"60s"', disk='floor_gb = 0\nreclaim_every = "0s"')
        counts, waits = an_hour(w, ledger)
        self.report("idle, before", counts)
        self.assertEqual(set(waits), {60})
        self.assertEqual(counts["next"], 120)  # two selectors, 60 polls
        self.assertEqual(counts["item"], 60)  # the kept worktree's item, every poll

    def test_idle_after_asks_a_twelfth_as_often(self):
        w, ledger = self.loop()
        counts, waits = an_hour(w, ledger)
        self.report("idle, after", counts)
        self.assertEqual(waits[:4], [60, 120, 240, 300])
        self.assertLessEqual(counts["next"], 2 * 15)  # 4 polls to the ceiling, then 12 an hour
        self.assertLessEqual(counts["item"], 6)  # every 10 minutes
        self.assertLessEqual(sum(counts.values()), 40)

    def test_a_dispatch_while_idle_is_taken_within_poll_max(self):
        w, ledger = self.loop()
        counts, waits = an_hour(w, ledger)  # backed off to the ceiling
        self.assertEqual(waits[-1], 300)
        n = ledger.file("new work", ["worker:w1", "agent:claude", "repo:other"])
        took = []
        w.work = lambda item, repo: (
            took.append(item["number"])
            or worker.Outcome(worker.HANDED_BACK, "done", item["number"])
        )
        w.shutdown.clear()
        w.run_once()  # the next poll, at most poll_max after the dispatch
        self.assertEqual(took, [n])

    def test_a_change_in_what_next_offers_starts_again_from_poll(self):
        w, ledger = self.loop()
        an_hour(w, ledger)
        self.assertEqual(w.idle_wait(), 300)
        # An item this loop may not take yet (its repository's claim fences it), but new.
        n = ledger.file("blocked by a claim", ["worker:w1", "agent:claude", "repo:lib"])
        ledger.dispatch(n, DISPATCHER)
        w.run_once()
        self.assertTrue(w.ranking_changed)
        self.assertEqual(w.idle_wait(), 60)
        self.assertEqual(w.idle_wait(), 120)

    def test_an_item_worked_starts_again_from_poll(self):
        w, ledger = self.loop()
        an_hour(w, ledger)
        w.quiet = 5
        outcomes = iter([worker.Outcome(worker.HANDED_BACK, "done", 7)])
        clock = FakeClock(w, 2 * HOUR)
        w.shutdown.clear()
        w.shutdown.wait = clock.wait

        def once():
            try:
                return next(outcomes)
            except StopIteration:
                return worker.Outcome(worker.NOTHING, "nothing to take")

        w.run_once = once
        w.loop()
        self.assertEqual(clock.waits[:3], [60, 120, 240])

    def test_poll_max_under_poll_is_refused(self):
        rig = Rig(self)
        rig.configure(poll='"60s"', poll_max='"30s"')
        with self.assertRaises(worker.gonk.ConfigError):
            worker.load_worker(worker.gonk.load(rig.checkout))

    def test_a_long_poll_of_its_own_is_not_shortened(self):
        rig = Rig(self)
        rig.configure(poll='"10m"')
        wc = worker.load_worker(worker.gonk.load(rig.checkout))
        self.assertEqual((wc.poll, wc.poll_max), (600, 600))

    def test_busy_the_heartbeat_reads_and_renews_once_a_renewal(self):
        # Busy, the loop running the item asks `next` nothing: its heartbeat is all, two
        # requests every renew_every (a third of the lease: 10m for 30m). Unchanged here.
        rig = Rig(self)
        rig.configure()
        ledger = FakeLedger()
        w = rig.worker(ledger)
        n = ledger.file("busy", ["worker:w1", "repo:lib"])
        self.assertTrue(w.ledger.claim(n, w.cfg.holder, "30m").ok)
        beat = worker.Heartbeat(w.ledger, n, w.cfg.holder, "30m", 600)
        ledger.calls.clear()
        for _ in range(HOUR // 600):
            self.assertTrue(beat.beat())
        counts = Counter(kind(c) for c, _ in ledger.calls)
        self.report("busy, the heartbeat", counts)
        self.assertEqual(counts, Counter({"item": 6, "sink claim": 6}))


if __name__ == "__main__":
    unittest.main()
