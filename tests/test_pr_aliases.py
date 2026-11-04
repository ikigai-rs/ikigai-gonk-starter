"""PR aliases at the CLI and worker transport boundaries."""

import argparse
import unittest

from support import FakeIkigai, gonk, ok, scratch_repo
from test_gonk import run, wire
from worker_support import FakeLedger, Rig, worker


class CliAliases(unittest.TestCase):
    def test_worker_enrollment_includes_only_the_gh_scheme(self):
        lines = "\n".join(gonk.enrollment_lines("ff00", "worker", "default"))
        self.assertIn("--worker default --alias default=gh", lines)
        for token in (
            "urn:cap:alias:scheme:gh",
            "urn:cap:ledger:alias:default",
            "urn:cap:store:read:graph:urn:iki:alias:graph",
            "urn:cap:store:write:graph:urn:iki:alias:graph",
        ):
            self.assertIn(f'"{token}"', lines)
        self.assertNotIn("urn:cap:alias:scheme:*", lines)

    def test_invalid_aliases_are_rejected_before_transport(self):
        for value in ("gh:acme/lib#0", "gh:acme/lib#1 close=true", "gh:acme/lib#1\n", "gh:x#1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                gonk.item_reference(value)

    def test_status_item_also_accepts_a_pr_alias(self):
        args = gonk.parser().parse_args(["dispatch", "1", "--status-item", "gh:acme/lib#7"])
        self.assertEqual(args.status_item, ["gh:acme/lib#7"])

    def test_every_item_command_accepts_a_pr_alias(self):
        for command in ("claim", "release", "report", "reject", "dispatch", "close"):
            with self.subTest(command=command):
                args = gonk.parser().parse_args([command, "gh:acme/lib#7"])
                self.assertEqual(args.item, "gh:acme/lib#7")

    def test_alias_is_resolved_before_mutation(self):
        repo = scratch_repo(self)
        wire(repo)
        fake = FakeIkigai(
            [
                ("item:gh:acme%2Flib%237", ok('{"item":{"number":12}}')),
                (":comment item=12", ok("rejected")),
            ]
        )
        code, out, err = run(repo, ["reject", "gh:acme/lib#7", "--note", "try again"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("rejected", out)
        self.assertEqual(len(fake.calls), 2)

    def test_unknown_alias_does_not_mutate(self):
        repo = scratch_repo(self)
        wire(repo)
        fake = FakeIkigai()
        code, _, err = run(repo, ["reject", "gh:acme/lib#7", "--note", "try again"], fake)
        self.assertEqual(code, 2)
        self.assertIn("resolve", err)
        self.assertEqual(len(fake.calls), 1)


class WorkerAliases(unittest.TestCase):
    def test_alias_retry_is_safe_and_a_second_item_is_refused(self):
        fake = FakeLedger()
        rig = Rig(self)
        first = fake.file("First", [])
        second = fake.file("Second", [])
        ledger = worker.Ledger(gonk.load(rig.checkout), fake)
        self.assertTrue(ledger.pr_alias(first, "acme/lib", 7).ok)
        self.assertTrue(ledger.pr_alias(first, "acme/lib", 7).ok)
        conflict = ledger.pr_alias(second, "acme/lib", 7)
        self.assertFalse(conflict.ok)
        self.assertIn(f"already names acme#{first}", conflict.text())
        self.assertEqual(list(fake.aliases.values()), [f"acme#{first}"])

    def test_worker_attaches_the_pr_before_handback(self):
        ledger = FakeLedger()
        rig = Rig(self)
        n = ledger.file("Work", ["worker:w1", "repo:lib"])
        rig.configure()
        outcome = rig.worker(ledger).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        commands = [c for c, _ in ledger.calls]
        alias = "sink urn:gk:iki:alias:scheme:gh:value:acme%2Flib%231"
        self.assertIn((alias, f"acme#{n}"), ledger.calls)
        self.assertIn(("sink urn:gk:iki:alias:scheme:gh", ""), ledger.calls)
        self.assertLess(
            commands.index(alias), next(i for i, c in enumerate(commands) if "handback=true" in c)
        )
        self.assertEqual(ledger.aliases[alias.split()[1]], f"acme#{n}")
        # Resolve the resulting PR name through the CLI transport, then mutate its item.
        code, _, err = run(rig.checkout, ["reject", "gh:acme/lib#1", "--note", "review"], ledger)
        self.assertEqual(code, 0, err)

    def test_alias_conflict_is_preserved_in_handback_without_reassignment(self):
        ledger = FakeLedger()
        rig = Rig(self)
        n = ledger.file("Work", ["worker:w1", "repo:lib"])
        alias = "urn:gk:iki:alias:scheme:gh:value:acme%2Flib%231"
        ledger.aliases[alias] = "acme#99"
        rig.configure()
        outcome = rig.worker(ledger).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertEqual(ledger.aliases[alias], "acme#99")
        text = "\n".join(c["text"] for c in ledger.items[n]["comments"])
        self.assertIn("Alias gh:acme/lib#1 was refused: alias already names acme#99", text)
        self.assertFalse(any(c.startswith("delete urn:gk:iki:alias:") for c, _ in ledger.calls))

    def test_missing_grants_are_visible_without_losing_pr_evidence(self):
        ledger = FakeLedger()
        ledger.refuse_aliases = True
        rig = Rig(self)
        n = ledger.file("Work", ["worker:w1", "repo:lib"])
        rig.configure()
        outcome = rig.worker(ledger).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        text = "\n".join(c["text"] for c in ledger.items[n]["comments"])
        self.assertIn("Alias gh:acme/lib#1 was refused: alias grant denied", text)
        self.assertIn("Evidence: https://github.com/acme/lib/pull/1", text)
        self.assertEqual(ledger.aliases, {})
