"""`gonk.py reject` says where the item goes next (ledger item 4484).

A worker skips an item it handed back until a router dispatches it again (tools/worker.py,
`redispatched_since`), and a reject is no dispatch: seven items rejected with a bare
`reject N --note ...` sat for about 12 hours with no worker taking them and `next` listing them
as ready. So `reject` takes exactly one of `--redispatch` and `--park`.

Hermetic: the in-memory ledger, the stub gh and the fake agent (tests/worker_support.py).
"""

from __future__ import annotations

import contextlib
import io
import shlex
import unittest

from support import gonk
from worker_support import DISPATCHER, FakeLedger, Rig, worker

EVIDENCE = "Evidence: https://github.com/acme/lib/pull/1\nCI green"
NOTE = "CI is red on main: fix the failing test"


class FailingDispatch(FakeLedger):
    """A ledger that writes the reject and then refuses the dispatch comment (the one written
    with an `author=` and no `handback`: over the socket the reject names none)."""

    def answer(self, command: str, stdin: str) -> gonk.Answer:
        if ":comment " in command and " author=" in command and "handback" not in command:
            return gonk.Answer(1, "", "error: the store refused the comment")
        return super().answer(command, stdin)


class RejectSaysWhereNext(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeLedger()
        self.rig = Rig(self)

    def gonk(self, *argv: str, ledger: FakeLedger | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gonk.main(["--repo", str(self.rig.checkout), *argv], ledger or self.ledger)
        return code, out.getvalue(), err.getvalue()

    def handed_back(self, labels: list[str], ledger: FakeLedger | None = None) -> tuple[int, str]:
        """An item dispatched with `labels` and handed back by a worker with evidence: its
        number, and the hand-back's time (what a worker's `attempts.json` records)."""
        ledger = ledger or self.ledger
        n = ledger.file("Fix the due dates", labels, body="They are off.")
        answer = ledger.run(
            f"sink urn:gk:iki:ledger:acme:comment item={n} handback=true author=w1", EVIDENCE
        )
        self.assertTrue(answer.ok, answer.err)
        self.assertEqual(ledger.items[n]["awaiting"], "dispatcher")
        return n, ledger.items[n]["comments"][-1]["time"]

    def dispatches(self, n: int, ledger: FakeLedger | None = None) -> list[dict]:
        comments = (ledger or self.ledger).items[n]["comments"]
        return [c for c in comments if gonk.parse_dispatch(c["text"]) is not None]

    # -- a bare reject

    def test_a_bare_reject_is_refused_naming_both_flags_and_writes_nothing(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        before = len(self.ledger.calls)
        code, _, err = self.gonk("reject", str(n), "--note", NOTE)
        self.assertEqual(code, 2, err)
        self.assertIn("--redispatch", err)
        self.assertIn("--park", err)
        self.assertIn("skipped by the worker that handed it back", err)
        self.assertEqual(self.ledger.calls[before:], [])
        self.assertEqual(self.ledger.items[n]["awaiting"], "dispatcher")

    def test_redispatch_and_park_together_are_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gonk.parser().parse_args(["reject", "12", "--note", NOTE, "--redispatch", "--park"])

    def test_park_refuses_the_flags_of_a_dispatch(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        before = len(self.ledger.calls)
        code, _, err = self.gonk("reject", str(n), "--note", NOTE, "--park", "--labels", "a:b")
        self.assertEqual(code, 2, err)
        self.assertIn("--labels is for --redispatch", err)
        self.assertEqual(self.ledger.calls[before:], [])

    # -- --redispatch

    def test_redispatch_rejects_then_dispatches_again_newer_than_the_hand_back(self):
        n, at = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        item = self.ledger.items[n]
        self.assertFalse(worker.redispatched_since(item, [DISPATCHER], at))
        code, out, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER
        )
        self.assertEqual(code, 0, err)
        self.assertIn(f"acme#{n}: rejected and dispatched again to agent:codex,worker:w1", out)
        reject, dispatch = item["comments"][-2:]
        self.assertEqual(reject["text"], NOTE)
        self.assertIsNone(item["awaiting"])  # `rejects=true` cleared the hand-back
        labels, digest = gonk.parse_dispatch(dispatch["text"])
        self.assertEqual(sorted(labels), ["agent:codex", "repo:lib", "worker:w1"])
        self.assertEqual(digest, gonk.brief_digest(item["title"], item["body"]))
        self.assertEqual(dispatch["author"], DISPATCHER)
        self.assertEqual(gonk.dispatch_note(dispatch["text"]), gonk.REDISPATCH_NOTE)
        self.assertIn("same pull request's branch", gonk.REDISPATCH_NOTE)
        # The worker's own functions: routed to it again, and newer than its hand-back.
        self.assertTrue(worker.redispatched_since(item, [DISPATCHER], at))
        self.assertIsNone(worker.dispatch_problem(item, [DISPATCHER], ["worker:w1", "agent:codex"]))
        self.assertEqual(item["labels"], ["agent:codex", "repo:lib", "worker:w1"])

    def test_a_dispatch_note_replaces_the_default(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER,
            "--dispatch-note", "Start over from main.",
        )  # fmt: skip
        self.assertEqual(code, 0, err)
        [_, dispatch] = self.dispatches(n)
        self.assertEqual(gonk.dispatch_note(dispatch["text"]), "Start over from main.")

    def test_redispatch_labels_replace_the_routing_labels(self):
        n, at = self.handed_back(["worker:w1", "agent:codex", "repo:lib", "bug"])
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER,
            "--labels", "worker:w2,agent:claude",
        )  # fmt: skip
        self.assertEqual(code, 0, err)
        item = self.ledger.items[n]
        self.assertEqual(item["labels"], ["agent:claude", "bug", "repo:lib", "worker:w2"])
        labels, _ = gonk.parse_dispatch(self.dispatches(n)[-1]["text"])
        self.assertEqual(sorted(labels), ["agent:claude", "repo:lib", "worker:w2"])
        self.assertTrue(worker.redispatched_since(item, [DISPATCHER], at))
        self.assertIsNone(
            worker.dispatch_problem(item, [DISPATCHER], ["worker:w2", "agent:claude"])
        )
        self.assertIsNotNone(
            worker.dispatch_problem(item, [DISPATCHER], ["worker:w1", "agent:codex"])
        )

    def test_a_blocked_item_refuses_redispatch_before_writing_anything(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        blocker = self.ledger.file("Schema first", ["repo:lib"], dispatched_by=None)
        self.ledger.block(blocker, n)
        before = len(self.ledger.calls)
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER
        )
        self.assertEqual(code, 2, err)
        self.assertIn("nothing was written, not even the reject", err)
        self.assertIn(f"acme#{n} is blocked by acme#{blocker}", err)
        writes = [c for c, _ in self.ledger.calls[before:] if not c.startswith("source ")]
        self.assertEqual(writes, [])
        self.assertEqual(self.ledger.items[n]["awaiting"], "dispatcher")
        self.assertEqual(len(self.dispatches(n)), 1)

    def test_a_blocked_item_redispatches_with_allow_blocked_and_its_note(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        blocker = self.ledger.file("Schema first", ["repo:lib"], dispatched_by=None)
        self.ledger.block(blocker, n)
        argv = ["reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER]
        code, _, err = self.gonk(*argv, "--allow-blocked")
        self.assertEqual(code, 2, err)
        self.assertIn("--allow-blocked needs --dispatch-note", err)
        code, _, err = self.gonk(*argv, "--allow-blocked", "--dispatch-note", "it is ready")
        self.assertEqual(code, 0, err)
        note = gonk.dispatch_note(self.dispatches(n)[-1]["text"])
        self.assertEqual(note, f"Dispatched while blocked by acme#{blocker}: it is ready")

    def test_a_closed_item_refuses_redispatch_before_writing_anything(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        self.ledger.items[n]["status"] = "closed"
        before = len(self.ledger.calls)
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER
        )
        self.assertEqual(code, 2, err)
        self.assertIn("only an open item is dispatched", err)
        self.assertEqual([c for c, _ in self.ledger.calls[before:] if "comment" in c], [])

    def test_an_item_with_no_routing_labels_refuses_redispatch_before_writing(self):
        n, _ = self.handed_back(["repo:lib"])
        before = len(self.ledger.calls)
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER
        )
        self.assertEqual(code, 2, err)
        self.assertIn("carries no worker: or agent: label", err)
        self.assertEqual([c for c, _ in self.ledger.calls[before:] if "comment" in c], [])

    def test_the_dispatch_note_keeps_the_note_bound(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER,
            "--dispatch-note", "n" * (gonk.MAX_NOTE + 1),
        )  # fmt: skip
        self.assertEqual(code, 2, err)
        self.assertIn("--dispatch-note is 8,001 characters, over the limit of 8,000", err)
        self.assertEqual(self.ledger.items[n]["awaiting"], "dispatcher")

    def test_a_dispatch_that_fails_after_the_reject_prints_the_command_that_finishes_it(self):
        ledger = FailingDispatch()
        n, at = self.handed_back(["worker:w1", "agent:codex", "repo:lib"], ledger)
        code, _, err = self.gonk(
            "reject", str(n), "--note", NOTE, "--redispatch", "--as", DISPATCHER, ledger=ledger
        )
        self.assertNotEqual(code, 0)
        self.assertIn(f"acme#{n} is rejected and NOT dispatched", err)
        item = ledger.items[n]
        self.assertEqual(item["comments"][-1]["text"], NOTE)  # the reject landed
        self.assertFalse(worker.redispatched_since(item, [DISPATCHER], at))
        command = err.split("Finish it with:\n", 1)[1].strip().splitlines()[0]
        words = shlex.split(command)
        self.assertEqual(words[:4], ["python3", "tools/gonk.py", "dispatch", str(n)])
        self.assertEqual(
            words[4:],
            [
                "--labels",
                "agent:codex,worker:w1",
                "--as",
                DISPATCHER,
                "--note",
                gonk.REDISPATCH_NOTE,
            ],
        )
        # The command it printed, run against a ledger that answers, finishes the dispatch.
        healthy = FakeLedger()
        healthy.items, healthy.tick = ledger.items, ledger.tick
        code, _, err = self.gonk(*words[2:], ledger=healthy)
        self.assertEqual(code, 0, err)
        self.assertTrue(worker.redispatched_since(item, [DISPATCHER], at))

    # -- --park

    def test_park_takes_off_only_the_worker_labels_and_dispatches_nothing(self):
        n, at = self.handed_back(["worker:w1", "worker:pool", "agent:codex", "repo:lib", "bug"])
        code, out, err = self.gonk("reject", str(n), "--note", NOTE, "--park")
        self.assertEqual(code, 0, err)
        self.assertIn("rejected and parked", out)
        item = self.ledger.items[n]
        self.assertEqual(item["labels"], ["agent:codex", "bug", "repo:lib"])
        self.assertEqual(item["comments"][-1]["text"], NOTE)
        self.assertIsNone(item["awaiting"])
        self.assertEqual(len(self.dispatches(n)), 1)  # only the original
        self.assertFalse(worker.redispatched_since(item, [DISPATCHER], at))
        removed = [c for c, _ in self.ledger.calls if c.startswith("delete ")]
        self.assertEqual(
            removed,
            [
                f"delete urn:gk:iki:ledger:acme:label item={n} content=worker:pool",
                f"delete urn:gk:iki:ledger:acme:label item={n} content=worker:w1",
            ],
        )

    def test_a_park_whose_unlabel_fails_says_the_item_is_not_parked(self):
        n, _ = self.handed_back(["worker:w1", "agent:codex", "repo:lib"])

        class Refusing(FakeLedger):
            def answer(self, command, stdin):
                if command.startswith("delete ") and ":label " in command:
                    return gonk.Answer(1, "", "denied: write")
                return super().answer(command, stdin)

        refusing = Refusing()
        refusing.items, refusing.tick = self.ledger.items, self.ledger.tick
        code, _, err = self.gonk("reject", str(n), "--note", NOTE, "--park", ledger=refusing)
        self.assertEqual(code, 1)
        self.assertIn(f"acme#{n} is rejected but NOT parked: it still carries worker:w1", err)


class TheLoopAfterARedispatch(unittest.TestCase):
    """The worker that handed the item back takes it again once it is rejected with
    `--redispatch`, and not after a bare reject (which is refused) or a park."""

    def setUp(self):
        self.ledger = FakeLedger()
        self.rig = Rig(self)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def reject(self, *flags: str) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return gonk.main(
                ["--repo", str(self.rig.checkout), "reject", "1", "--note", NOTE, *flags],
                self.ledger,
            )

    def handed_back(self) -> int:
        n = self.ledger.file("Fix the due dates", ["worker:w1", "repo:lib"], body="They are off.")
        self.rig.configure()
        self.assertEqual(self.rig.worker(self.ledger).run_once().code, worker.HANDED_BACK)
        self.assertEqual(n, 1)
        return n

    def test_redispatch_hands_the_item_to_the_worker_that_handed_it_back(self):
        n = self.handed_back()
        self.assertEqual(self.rig.worker(self.ledger).candidates()[0], [])
        self.assertEqual(self.reject("--redispatch", "--as", DISPATCHER), 0)
        take, _, _ = self.rig.worker(self.ledger).candidates()
        self.assertEqual([i["number"] for i, _ in take], [n])

    def test_park_leaves_it_offered_to_no_worker(self):
        n = self.handed_back()
        self.assertEqual(self.reject("--park"), 0)
        take, skip, _ = self.rig.worker(self.ledger).candidates()
        self.assertEqual(take, [])
        self.assertNotIn(f"acme#{n}", dict(skip))  # not offered at all: no worker: label


if __name__ == "__main__":
    unittest.main()
