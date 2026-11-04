"""PR aliases at the CLI and worker transport boundaries."""

import argparse
import unittest

from support import FakeIkigai, gonk, ok, scratch_repo
from test_gonk import run, wire
from worker_support import FakeLedger, Rig, worker


class CliAliases(unittest.TestCase):
    def test_worker_enrollment_has_no_alias_grants(self):
        lines = "\n".join(gonk.enrollment_lines("ff00", "worker", "default"))
        self.assertIn("--worker default", lines)
        self.assertNotIn("--alias", lines)
        self.assertNotIn("urn:cap:alias:", lines)
        self.assertNotIn("urn:cap:ledger:alias:", lines)
        self.assertNotIn("urn:iki:alias:graph", lines)

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


class CloseAliases(unittest.TestCase):
    def test_close_adds_all_pr_aliases_and_resolves_them(self):
        ledger = FakeLedger()
        rig = Rig(self)
        n = ledger.file("First", [])
        argv = ["close", str(n)]
        for url in (
            "https://github.com/acme/lib/pull/7",
            "https://github.com/acme/lib/pull/8",
            "https://github.com/acme/lib/commit/abc",
        ):
            argv += ["--evidence", url]
        code, out, err = run(rig.checkout, argv, ledger)
        self.assertEqual(code, 0, err)
        self.assertIn("Alias: gh:acme/lib#7", out)
        self.assertEqual(len(ledger.aliases), 2)
        for number in (7, 8):
            code, _, err = run(
                rig.checkout, ["reject", f"gh:acme/lib#{number}", "--note", "review"], ledger
            )
            self.assertEqual(code, 0, err)
        self.assertEqual(ledger.closes, [n])

    def test_duplicate_alias_does_not_prevent_close_or_reassign(self):
        ledger = FakeLedger()
        rig = Rig(self)
        first = ledger.file("First", [])
        second = ledger.file("Second", [])
        argv = ["--evidence", "https://github.com/acme/lib/pull/7"]
        self.assertEqual(run(rig.checkout, ["close", str(first), *argv], ledger)[0], 0)
        code, out, err = run(rig.checkout, ["close", str(second), *argv], ledger)
        self.assertEqual(code, 0, err)
        self.assertIn(f"was refused: alias already names acme#{first}", out + err)
        self.assertEqual(list(ledger.aliases.values()), [f"acme#{first}"])
        self.assertEqual(ledger.closes, [first, second])

    def test_missing_grant_is_visible_but_close_succeeds(self):
        ledger = FakeLedger()
        ledger.refuse_aliases = True
        rig = Rig(self)
        n = ledger.file("First", [])
        code, out, err = run(
            rig.checkout,
            ["close", str(n), "--evidence", "https://github.com/acme/lib/pull/7"],
            ledger,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("was refused: alias grant denied", out + err)
        self.assertEqual(ledger.closes, [n])
        self.assertEqual(ledger.aliases, {})

    def test_foreign_alias_cannot_mutate_local_item_with_same_number(self):
        ledger = FakeLedger()
        rig = Rig(self)
        ledger.file("Local", [])
        ledger.aliases["urn:gk:iki:alias:scheme:gh:value:acme%2Flib%237"] = "foreign#1"
        code, _, err = run(
            rig.checkout, ["close", "gh:acme/lib#7", "--evidence", "urn:test:proof"], ledger
        )
        self.assertEqual(code, 2)
        self.assertIn("resolve", err)
        self.assertEqual(ledger.closes, [])

    def test_alias_write_refusal_does_not_prevent_other_aliases(self):
        repo = scratch_repo(self)
        wire(repo)
        fake = FakeIkigai(
            [
                (":close", ok("closed\n")),
                ("scheme:gh:value:acme%2Flib%237", gonk.Answer(1, "", "alias write denied")),
                ("scheme:gh:value:acme%2Flib%238", ok("named")),
                ("scheme:gh", ok("registered")),
            ]
        )
        code, out, err = run(
            repo,
            [
                "close",
                "12",
                "--evidence",
                "https://github.com/acme/lib/pull/7",
                "--evidence",
                "https://github.com/acme/lib/pull/8",
            ],
            fake,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("was refused: alias write denied", err)
        self.assertIn("Alias: gh:acme/lib#8", out)
        self.assertEqual(len(fake.calls), 4)
        self.assertEqual(fake.calls[-1][1], "acme#12")

    def test_repeated_close_is_idempotent(self):
        ledger = FakeLedger()
        rig = Rig(self)
        n = ledger.file("First", [])
        argv = ["close", str(n), "--evidence", "https://github.com/acme/lib/pull/7"]
        for _ in range(2):
            code, _, err = run(rig.checkout, argv, ledger)
            self.assertEqual(code, 0, err)
        self.assertEqual(list(ledger.aliases.values()), [f"acme#{n}"])

    def test_non_pr_evidence_does_not_register_a_scheme(self):
        repo = scratch_repo(self)
        wire(repo)
        fake = FakeIkigai([(":close", ok("closed\n"))])
        argv = ["close", "12"]
        for iri in (
            "urn:test:proof",
            "https://github.com/acme/lib/commit/abc",
            "https://github.com.evil/acme/lib/pull/7",
            "https://github.com/acme/lib/pull/0",
        ):
            argv += ["--evidence", iri]
        code, _, err = run(repo, argv, fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.calls), 1)

    def test_failed_close_does_not_write_aliases(self):
        repo = scratch_repo(self)
        wire(repo)
        fake = FakeIkigai()
        code, _, _ = run(
            repo, ["close", "12", "--evidence", "https://github.com/acme/lib/pull/7"], fake
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(len(fake.calls), 1)


class WorkerAliases(unittest.TestCase):
    def test_worker_handback_never_registers_or_adds_aliases(self):
        ledger = FakeLedger()
        rig = Rig(self)
        n = ledger.file("Work", ["worker:w1", "repo:lib"])
        rig.configure()
        outcome = rig.worker(ledger).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertFalse(any("iki:alias:" in c for c, _ in ledger.calls))
        self.assertEqual(ledger.aliases, {})
        text = "\n".join(c["text"] for c in ledger.items[n]["comments"])
        self.assertIn("Evidence: https://github.com/acme/lib/pull/1", text)
        self.assertNotIn("Alias", text)
