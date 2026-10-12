"""Unit tests for tools/gonk.py: no gonk, no `ikigai`, a fake runner instead."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from support import ROOT, FakeIkigai, gonk, ok, scratch_repo


def run(repo: Path, argv: list[str], fake=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gonk.main(["--repo", str(repo), *argv], make_ikigai=fake or FakeIkigai())
    return code, out.getvalue(), err.getvalue()


def wire(repo: Path, ledger="acme", holder="agent-1", socket=None):
    (repo / ".gonk" / "gonk.toml").write_text(f'ledger = "{ledger}"\n')
    sock = socket or str(repo / "g.sock")
    lines = ['transport = "socket"', f'socket = "{sock}"']
    if holder:
        lines.append(f'holder = "{holder}"')
    (repo / ".gonk" / "local.toml").write_text("\n".join(lines) + "\n")


def item(holder=None, expires=None, status="open", number=12):
    claim = {"holder": holder, "kind": "machine", "expires": expires} if holder else None
    doc = {"display": f"acme#{number}", "status": status, "claim": claim}
    return ok(json.dumps({"schema": 1, "ledger": "acme", "item": doc}))


class CacheTrailer(unittest.TestCase):
    """`ikigai -c` writes how the kernel's cache treated each answer to stderr (`[uncacheable]`):
    for a person at the REPL, not part of what gonk said, so `next` and `claim` drop it."""

    def test_the_trailer_is_dropped_and_everything_else_kept(self):
        for trailer in ["[uncacheable]", "[cached]", "[computed]", "[2 cached · 1 uncacheable]"]:
            self.assertEqual(gonk.without_cache_trailer(f"{trailer}\n"), "", trailer)
        err = "error: refused\n[uncacheable]\n"
        self.assertEqual(gonk.without_cache_trailer(err), "error: refused\n")
        self.assertEqual(gonk.without_cache_trailer("[a note]\n"), "[a note]\n")

    def test_the_runner_drops_it(self):
        fake_bin = Path(self.enterContext(tempfile.TemporaryDirectory())) / "ikigai"
        fake_bin.write_text("#!/bin/sh\necho ' 1. acme#1  open'\necho '[uncacheable]' >&2\n")
        fake_bin.chmod(0o755)
        answer = gonk.Ikigai(gonk.Target(socket="/nowhere"), binary=str(fake_bin)).run("x")
        self.assertEqual((answer.code, answer.out, answer.err), (0, " 1. acme#1  open\n", ""))


class Names(unittest.TestCase):
    def test_ledger_names_follow_the_ledgers_own_rules(self):
        for good in ["acme", "a", "team-1", "x_y", "a" * 64]:
            self.assertIsNone(gonk.ledger_problem(good), good)
        for bad in ["", "Acme", "a:b", "-a", "a-", "items", "next", "a" * 65, "a b"]:
            self.assertIsNotNone(gonk.ledger_problem(bad), bad)

    def test_holder_names_refuse_the_filter_keywords_and_spaces(self):
        self.assertIsNone(gonk.holder_problem("codex-laptop-1"))
        for bad in ["none", "any", "two words", "", "-lead"]:
            self.assertIsNotNone(gonk.holder_problem(bad), bad)


class Targets(unittest.TestCase):
    def test_a_socket_and_a_quic_door_are_one_alias_mount(self):
        sock = gonk.Target(socket="/s/gonk.sock")
        self.assertEqual(sock.mount_args(), ["--mount", "urn:gk:=/s/gonk.sock"])
        quic = gonk.Target(quic="quic://box:1062", cert_dir="/c")
        self.assertEqual(
            quic.mount_args(), ["--mount", "urn:gk:=quic://box:1062", "--cert-dir", "/c"]
        )

    def test_mcp_uses_the_ledgers_own_names(self):
        quic = gonk.Target(quic="quic://box:1062", cert_dir="/c")
        self.assertEqual(
            quic.mcp_command(),
            "ikigai mcp --override urn:iki:ledger:=quic://box:1062 --cert-dir /c",
        )

    def test_a_transport_without_its_details_is_refused(self):
        with self.assertRaises(gonk.ConfigError):
            gonk.target_from({"transport": "socket"}, "x")
        with self.assertRaises(gonk.ConfigError):
            gonk.target_from({"transport": "quic", "quic": "quic://h:1"}, "x")
        with self.assertRaises(gonk.ConfigError):
            gonk.target_from({"transport": "http"}, "x")
        self.assertIsNone(gonk.target_from({}, "x"))


class Fingerprints(unittest.TestCase):
    def test_the_fingerprint_is_the_sha256_of_the_certificates_der(self):
        der = b"\x30\x82 not really a certificate, but bytes are bytes"
        body = base64.encodebytes(der).decode()
        pem = f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n"
        self.assertEqual(gonk.cert_fingerprint(pem), hashlib.sha256(der).hexdigest())

    def test_a_client_is_named_the_way_gonk_mints_it_now(self):
        # gonk mints urn:iki:host:gonk:client:<fp> (ikigai-gonk PR 126, ledger 1183); the old
        # spelling is an alias it never mints again, so this tool never prints it as the name.
        self.assertEqual(gonk.client_iri("ab12"), "urn:iki:host:gonk:client:ab12")
        self.assertEqual(gonk.legacy_client_iri("ab12"), "urn:iki:gonk:client:ab12")

    def test_a_file_with_no_certificate_is_refused(self):
        with self.assertRaises(gonk.ConfigError):
            gonk.cert_fingerprint("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----")

    # The authoritative worker grant, exactly as gonk computes it: `src/grants.rs`,
    # `grants_for(ledger, Authority::Worker)`, whose doctest is the contract. Renew and release
    # ride on the claim token (they are not capabilities of their own), and a worker NEEDS the
    # ledger graph's store WRITE token, because every ledger write goes to the store under the
    # caller's capability — without it a hand-enrolled worker cannot claim or comment.
    WORKER_TOKENS = [
        "urn:cap:ledger:read:acme",
        "urn:cap:store:read:graph:urn:iki:ledger:graph:acme",
        "urn:cap:ledger:claim:acme",
        "urn:cap:ledger:comment:acme",
        "urn:cap:store:write:graph:urn:iki:ledger:graph:acme",
    ]

    def test_the_worker_tokens_are_exactly_gonks_worker_grant(self):
        # EQUALITY, in gonk's order: a missing store WRITE token (the old bug) fails here.
        self.assertEqual(gonk.worker_tokens("acme"), self.WORKER_TOKENS)

    def test_the_operator_lines_print_the_worker_and_the_writer_grants(self):
        lines = "\n".join(gonk.enrollment_lines("ff00", "build-box", "acme"))
        # the --worker form for an unattended machine first: the grant init exists to print
        self.assertIn("ikigai-gonk client add build-box --cert client.crt --worker acme", lines)
        # =write stays as the option for a person, beside it
        self.assertIn(
            "ikigai-gonk client add build-box --cert client.crt --ledger acme=write", lines
        )
        self.assertIn('"ff00": {"grant": "build-box"}', lines)
        # the by-hand grants.json entry is the worker grant: exactly gonk's tokens, in its order
        for token in self.WORKER_TOKENS:
            self.assertIn(f'"{token}"', lines)
        self.assertNotIn("urn:cap:ledger:renew", lines)
        self.assertNotIn("urn:cap:ledger:release", lines)
        self.assertNotIn("urn:cap:ledger:close", lines)
        self.assertNotIn("urn:cap:ledger:write:acme", lines)
        self.assertNotIn("1143", lines)

    def test_the_write_grants_tokens_are_still_the_four_writer_tokens(self):
        # =write is still the option for a person, and its by-hand tokens are unchanged.
        self.assertEqual(
            gonk.ledger_write_tokens("acme"),
            [
                "urn:cap:ledger:read:acme",
                "urn:cap:store:read:graph:urn:iki:ledger:graph:acme",
                "urn:cap:ledger:write:acme",
                "urn:cap:store:write:graph:urn:iki:ledger:graph:acme",
            ],
        )


class Layout(unittest.TestCase):
    def test_the_starters_own_layout_is_complete(self):
        repo = ROOT
        ok_, bad = gonk.layout_problems(repo, gonk.read_toml(repo / ".gonk" / "layout.toml"))
        self.assertEqual(bad, [])
        self.assertEqual(len(ok_), 4)

    def test_a_missing_directory_is_named(self):
        repo = scratch_repo(self)
        (repo / "openspec" / "changes").rmdir()
        _, bad = gonk.layout_problems(repo, gonk.read_toml(repo / ".gonk" / "layout.toml"))
        self.assertEqual(bad, ["[[spec]] openspec: openspec/ has no changes/"])

    def test_an_artifact_a_spec_names_must_have_a_directory(self):
        repo = scratch_repo(self)
        change = repo / "openspec" / "changes" / "add-loans"
        change.mkdir()
        (change / "proposal.md").write_text("## Impact\n- Artifacts: `service`, `cli`\n")
        (repo / "artifacts" / "service").mkdir()
        layout = gonk.read_toml(repo / ".gonk" / "layout.toml")
        self.assertEqual(
            gonk.named_artifacts(repo, layout),
            [
                ("openspec/changes/add-loans/proposal.md", "service"),
                ("openspec/changes/add-loans/proposal.md", "cli"),
            ],
        )
        self.assertEqual(
            gonk.artifact_problems(repo, layout),
            [
                "openspec/changes/add-loans/proposal.md names artifact `cli`, "
                "and artifacts/cli/ does not exist"
            ],
        )


class Init(unittest.TestCase):
    def test_an_empty_starter_says_exactly_what_is_missing(self):
        repo = scratch_repo(self)
        code, out, _ = run(repo, ["init"])
        self.assertEqual(code, 1)
        missing = [line.split()[1] for line in out.splitlines() if "missing" in line.split()[:1]]
        self.assertEqual(missing, ["ledger", "holder", "gonk"])
        self.assertIn("3 thing(s) missing.", out)

    def test_flags_are_written_and_a_rerun_converges(self):
        repo = scratch_repo(self)
        sock = repo / "g.sock"
        args = ["init", "--ledger", "acme", "--socket", str(sock), "--holder", "agent-1"]
        run(repo, args)
        run(repo, args)
        cfg = gonk.load(repo)
        self.assertEqual(
            (cfg.ledger, cfg.holder, cfg.target.socket), ("acme", "agent-1", str(sock))
        )
        gonk_toml = (repo / ".gonk" / "gonk.toml").read_text()
        self.assertEqual(gonk_toml.count('ledger = "acme"'), 1, gonk_toml)

    def test_a_tilde_in_a_socket_path_is_expanded(self):
        repo = scratch_repo(self)
        run(repo, ["init", "--socket", "~/.ikigai/gonk.sock"])
        self.assertEqual(
            gonk.load(repo).target.socket, str(Path("~/.ikigai/gonk.sock").expanduser())
        )

    def test_a_bad_ledger_name_is_refused_before_anything_is_written(self):
        repo = scratch_repo(self)
        before = (repo / ".gonk" / "gonk.toml").read_text()
        code, _, err = run(repo, ["init", "--ledger", "items"])
        self.assertEqual(code, 2)
        self.assertIn("reserved", err)
        self.assertEqual((repo / ".gonk" / "gonk.toml").read_text(), before)

    def test_quic_needs_its_certificate_directory(self):
        repo = scratch_repo(self)
        code, _, err = run(repo, ["init", "--quic", "quic://box:1062"])
        self.assertEqual(code, 2)
        self.assertIn("--cert-dir", err)

    def test_a_reachable_gonk_without_scripts_or_nl_says_so(self):
        repo = scratch_repo(self)
        wire(repo)
        (repo / "g.sock").touch()  # the socket check only asks that the path exists
        absent = ok('<urn:ikigai:endpoint:remote> ik:title "contract unavailable" .')
        fake = FakeIkigai(
            [
                ("ledgers", ok("acme   3 open   5 total  urn:iki:ledger:graph:acme\n")),
                ("describe urn:gk:script:catalog", absent),
                ("describe urn:gk:nl:sparql", absent),
            ]
        )
        code, out, _ = run(repo, ["init"], fake)
        self.assertEqual(code, 0, out)
        self.assertIn("`acme` holds 5 item(s), 3 open", out)
        self.assertRegex(out, r"absent\s+scripts\s+gonk does not offer `urn:script:catalog`")
        self.assertRegex(out, r"absent\s+nl\s+gonk does not offer `urn:nl:sparql`")
        # Each family says what holds meanwhile in its own words (one template over both read
        # "drafting a query from a sentence (ikigai-nl) stay files").
        self.assertIn(
            "saved queries and workflow scripts (ikigai-script) stay files in this repository; "
            "nothing to do until it does",
            out,
        )
        self.assertIn(
            "yet, so a query is written by hand rather than drafted from a sentence "
            "(ikigai-nl); nothing to do until it does",
            out,
        )
        self.assertNotIn("stay files in this repository; nothing", out.split("absent   nl")[1])
        self.assertIn("ikigai mcp --override urn:iki:ledger:=", out)

    def test_a_ledger_with_nothing_filed_is_a_note_not_a_failure(self):
        repo = scratch_repo(self)
        wire(repo)
        (repo / "g.sock").touch()
        fake = FakeIkigai(
            [("ledgers", ok("default  1 open  1 total  urn:…\n")), ("describe", ok("x"))]
        )
        code, out, _ = run(repo, ["init"], fake)
        self.assertEqual(code, 0, out)
        self.assertIn("has no create", out)
        self.assertIn("holds nothing yet", out)

    def test_a_quic_machine_prints_its_identity_and_the_operators_lines(self):
        repo = scratch_repo(self)
        certs = repo / "certs"
        certs.mkdir()
        der = b"machine certificate bytes"
        pem = "-----BEGIN CERTIFICATE-----\n" + base64.encodebytes(der).decode()
        (certs / "client.crt").write_text(pem + "-----END CERTIFICATE-----\n")
        args = ["init", "--ledger", "acme", "--holder", "build-box"]
        args += ["--quic", "quic://gonk-host:1062", "--cert-dir", str(certs)]
        fake = FakeIkigai([("ledgers", ok("")), ("describe", ok("x"))])
        code, out, _ = run(repo, args, fake)
        fp = hashlib.sha256(der).hexdigest()
        # The identity row names the machine as gonk mints it, and the older spelling only as
        # what records written before the change still say.
        identity = next(line for line in out.splitlines() if " identity " in f" {line} ")
        self.assertIn(f"urn:iki:host:gonk:client:{fp} (from ", identity)
        self.assertNotIn("urn:iki:gonk:client:", identity)
        self.assertIn(f"older records name it urn:iki:gonk:client:{fp}", out)
        self.assertIn("ikigai-gonk client add build-box --cert client.crt --ledger acme=write", out)
        self.assertRegex(out, r"missing\s+server\s+no server.crt")
        self.assertEqual(code, 1)
        self.assertEqual(fake.target.mount_args()[1], "urn:gk:=quic://gonk-host:1062")


class Claims(unittest.TestCase):
    def setUp(self):
        self.repo = scratch_repo(self)
        wire(self.repo)

    def test_a_claim_sends_the_holder_as_content_and_records_the_lease(self):
        fake = FakeIkigai(
            [(":claim", ok("acme#12 claimed by agent-1 until 2026-10-10T22:00:00Z\n"))]
        )
        code, _, _ = run(self.repo, ["claim", "12"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            fake.calls, [("sink urn:gk:iki:ledger:acme:claim item=12 lease=30m", "agent-1")]
        )
        state = gonk.read_toml(self.repo / ".gonk" / "state" / "claim.toml")
        self.assertEqual(state["item"], 12)
        self.assertEqual(state["expires"], "2026-10-10T22:00:00Z")

    def test_a_takeover_names_the_holder_it_takes_from(self):
        fake = FakeIkigai([(":claim", ok("acme#12 taken over from old by agent-1 until x\n"))])
        run(self.repo, ["claim", "12", "--takeover-from", "old"], fake)
        self.assertEqual(
            fake.calls[0][0],
            "sink urn:gk:iki:ledger:acme:claim item=12 lease=30m takeover=true from=old",
        )

    def test_a_tree_holds_one_claim(self):
        fake = FakeIkigai([(":claim", ok("acme#12 claimed by agent-1\n"))])
        run(self.repo, ["claim", "12"], fake)
        code, _, err = run(self.repo, ["claim", "13"], fake)
        self.assertEqual(code, 2)
        self.assertIn("one item per working tree", err)

    def test_a_refused_claim_records_nothing(self):
        fake = FakeIkigai([(":claim", gonk.Answer(1, "", "error: conflict: held by someone"))])
        code, _, _ = run(self.repo, ["claim", "12"], fake)
        self.assertEqual(code, 1)
        self.assertFalse((self.repo / ".gonk" / "state" / "claim.toml").exists())

    def test_a_bad_lease_is_refused_locally(self):
        code, _, err = run(self.repo, ["claim", "12", "--lease", "soon"])
        self.assertEqual(code, 2)
        self.assertIn("--lease", err)

    def test_a_claim_needs_a_holder(self):
        wire(self.repo, holder=None)
        code, _, err = run(self.repo, ["claim", "12"])
        self.assertEqual(code, 2)
        self.assertIn("--holder", err)

    def test_report_comments_the_evidence_then_hands_the_claim_back(self):
        fake = FakeIkigai(
            [(":claim", ok("acme#12 claimed by agent-1\n")), (":comment", ok("commented\n"))]
        )
        run(self.repo, ["claim", "12"], fake)
        url = "https://github.com/o/r/pull/3"
        code, _, _ = run(self.repo, ["report", "12", "--evidence", url, "--note", "ok"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            fake.calls[1:],
            [
                # `handback=true` makes the hand-back the item's state (ledger 0.8.0, ledger
                # item 1199), so `next` stops offering the item; the Evidence: prefix stays
                # for the ledgers that have no such state.
                (
                    "sink urn:gk:iki:ledger:acme:comment item=12 handback=true",
                    f"Evidence: {url}\n\nok",
                ),
                ("delete urn:gk:iki:ledger:acme:claim item=12", ""),
            ],
        )
        self.assertFalse((self.repo / ".gonk" / "state" / "claim.toml").exists())

    def test_report_still_posts_the_evidence_to_a_ledger_without_handback(self):
        # A ledger older than 0.8.0 does not know `handback` and ignores it: the comment is
        # written as it stands, the Evidence: prefix being the hand-back there.
        fake = FakeIkigai(
            [(":claim", ok("acme#12 claimed by agent-1\n")), (":comment", ok("commented\n"))]
        )
        run(self.repo, ["claim", "12"], fake)
        url = "https://github.com/o/r/pull/3"
        code, _, _ = run(self.repo, ["report", "12", "--evidence", url], fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            fake.calls[1:],
            [
                (
                    "sink urn:gk:iki:ledger:acme:comment item=12 handback=true",
                    f"Evidence: {url}",
                ),
                ("delete urn:gk:iki:ledger:acme:claim item=12", ""),
            ],
        )

    def test_reject_clears_the_hand_back_with_a_reason(self):
        fake = FakeIkigai([(":comment", ok("commented\n"))])
        code, _, err = run(self.repo, ["reject", "12"], fake)
        self.assertEqual(code, 2)
        self.assertIn("--note", err)
        code, _, _ = run(self.repo, ["reject", "12", "--note", "CI is red on main"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            fake.calls,
            [
                (
                    "sink urn:gk:iki:ledger:acme:comment item=12 rejects=true",
                    "CI is red on main",
                )
            ],
        )

    def test_release_comments_the_note_then_releases_with_no_content(self):
        # Ledger item 1229: the cli never gives a batch's stdin to a DELETE, so a note piped
        # to the release was dropped. It is a comment of its own, posted first.
        fake = FakeIkigai(
            [(":claim", ok("acme#12 claimed by agent-1\n")), (":comment", ok("commented\n"))]
        )
        run(self.repo, ["claim", "12"], fake)
        code, _, _ = run(self.repo, ["release", "--note", "blocked on the schema"], fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            fake.calls[1:],
            [
                ("sink urn:gk:iki:ledger:acme:comment item=12", "blocked on the schema"),
                ("delete urn:gk:iki:ledger:acme:claim item=12", ""),
            ],
        )
        self.assertFalse((self.repo / ".gonk" / "state" / "claim.toml").exists())

    def test_a_release_whose_note_is_refused_releases_nothing(self):
        fake = FakeIkigai(
            [
                (":claim item=12 lease", ok("acme#12 claimed by agent-1\n")),
                (":comment", gonk.Answer(1, "", "error: denied")),
            ]
        )
        run(self.repo, ["claim", "12"], fake)
        code, _, _ = run(self.repo, ["release", "--note", "why"], fake)
        self.assertEqual(code, 1)
        self.assertFalse(any(c.startswith("delete") for c, _ in fake.calls))
        self.assertTrue((self.repo / ".gonk" / "state" / "claim.toml").exists())

    def test_close_needs_evidence_and_releases_this_trees_claim(self):
        code, _, err = run(self.repo, ["close", "12", "--evidence", "merged"])
        self.assertEqual(code, 2)
        self.assertIn("--evidence is a URL", err)
        fake = FakeIkigai(
            [(":claim", ok("acme#12 claimed by agent-1\n")), (":close", ok("closed\n"))]
        )
        run(self.repo, ["claim", "12"], fake)
        url = "https://github.com/o/r/pull/3"
        code, _, _ = run(self.repo, ["close", "12", "--evidence", url], fake)
        self.assertEqual(code, 0)
        self.assertEqual(
            [c for c, _ in fake.calls[1:]],
            [
                "sink urn:gk:iki:ledger:acme:close item=12 reason=done",
                "delete urn:gk:iki:ledger:acme:claim item=12",
            ],
        )

    def test_file_puts_the_title_and_body_in_the_content(self):
        fake = FakeIkigai([(":append", ok("acme#1 urn:…\n"))])
        run(self.repo, ["file", "Lend a book", "--body", "Due in 14 days", "--priority", "1"], fake)
        self.assertEqual(
            fake.calls,
            [("sink urn:gk:iki:ledger:acme:append priority=1", "Lend a book\n\nDue in 14 days")],
        )


class CheckClaim(unittest.TestCase):
    NOW = datetime(2026, 10, 10, 21, 0, tzinfo=UTC)

    def setUp(self):
        self.repo = scratch_repo(self)
        wire(self.repo)
        fake = FakeIkigai([(":claim", ok("acme#12 claimed by agent-1\n"))])
        run(self.repo, ["claim", "12"], fake)
        self.cfg = gonk.load(self.repo)

    def check(self, answer):
        return gonk.check_claim(self.cfg, FakeIkigai([("item:12", answer)])(None), self.NOW)

    def test_held_by_this_tree_with_time_left(self):
        allowed, why = self.check(item("agent-1", "2026-10-10T21:30:00Z"))
        self.assertTrue(allowed, why)

    def test_taken_over(self):
        allowed, why = self.check(item("someone-else", "2026-10-10T21:30:00Z"))
        self.assertFalse(allowed)
        self.assertIn("taken over", why)

    def test_released(self):
        allowed, why = self.check(item(None))
        self.assertFalse(allowed)
        self.assertIn("by nobody", why)

    def test_an_expired_lease_must_be_renewed(self):
        allowed, why = self.check(item("agent-1", "2026-10-10T20:59:59Z"))
        self.assertFalse(allowed)
        self.assertIn("renew", why)

    def test_closed(self):
        allowed, _ = self.check(item("agent-1", status="closed"))
        self.assertFalse(allowed)

    def test_gonk_unreachable_is_not_a_claim(self):
        allowed, why = self.check(gonk.Answer(1, "", "connect: no such file"))
        self.assertFalse(allowed)
        self.assertIn("could not read item 12", why)

    def test_a_holder_name_is_compared_exactly_even_when_it_spells_a_client(self):
        # A holder is free text. gonk aliases a client's two IRI spellings for the PRINCIPAL it
        # mints, never for a holder name: the ledger matches a renewal's holder exactly and
        # refuses one under the other spelling (`held by …, not …`). So the other spelling is
        # not this tree's claim, and saying it was would only defer the refusal to the renewal.
        state = self.repo / ".gonk" / "state" / "claim.toml"
        state.write_text('ledger = "acme"\nitem = 12\nholder = "urn:iki:host:gonk:client:ab12"\n')
        allowed, why = self.check(item("urn:iki:gonk:client:ab12", "2026-10-10T21:30:00Z"))
        self.assertFalse(allowed)
        self.assertIn("taken over", why)

    def test_no_claim_file(self):
        (self.repo / ".gonk" / "state" / "claim.toml").unlink()
        allowed, why = gonk.check_claim(self.cfg, FakeIkigai()(None), self.NOW)
        self.assertFalse(allowed)
        self.assertIn("holds no claim", why)


class LedgerOutput(unittest.TestCase):
    def test_the_ledgers_listing_parses(self):
        text = (
            "default                     729 open   1138 total  urn:iki:ledger:graph:default\n"
            "\n1 ledger(s)\n"
        )
        self.assertEqual(gonk.parse_ledgers(text), {"default": (729, 1138)})

    def test_describe_of_an_unbound_name_is_not_an_offer(self):
        self.assertFalse(gonk.offered(ok('ik:title "contract unavailable" ;')))
        self.assertTrue(gonk.offered(ok('ik:title "Run a script" ;')))
        self.assertIsNone(gonk.offered(gonk.Answer(1, "", "connect refused")))


if __name__ == "__main__":
    unittest.main()
