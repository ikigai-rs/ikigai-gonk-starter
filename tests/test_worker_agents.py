"""One worker loop per agent, from one checkout (worker/README.md, step 8).

Each loop has its own configuration, `.gonk/worker-<agent>.toml`, with its own labels, agent
and, optionally, holder name; its own record of the items it handed back and its own stop file;
and it shares with every other loop of the checkout the per-repository locks and the stop file
for all of them. Hermetic against the in-memory ledger, except the last class, which runs the
holder override against a scratch gonk over QUIC where `ikigai-gonk` and `ikigai` are installed.
"""

from __future__ import annotations

import contextlib
import io
import json
import plistlib
import threading
import time
import tomllib
import unittest
from pathlib import Path

from support import ROOT, gonk
from test_integration import GONK, IKIGAI, ScratchGonk
from worker_support import DISPATCHER, FakeLedger, Rig, worker

CODEX = '["worker:w1", "agent:codex"]'
CLAUDE = '["worker:w1", "agent:claude"]'


def quietly(test) -> None:
    quiet = contextlib.redirect_stdout(io.StringIO())
    quiet.__enter__()
    test.addCleanup(quiet.__exit__, None, None, None)


class ALoopsConfiguration(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.base = self.rig.checkout / ".gonk" / "state" / "worker"

    def load(self, path: Path) -> worker.WorkerConfig:
        return worker.load_worker(gonk.load(self.rig.checkout), path)

    def test_its_loop_labels_holder_and_state(self):
        path = self.rig.configure("worker-codex.toml", labels=CODEX, holder='"w1-codex"')
        wc = self.load(path)
        self.assertEqual(wc.loop, "codex")
        self.assertEqual(wc.labels, ["worker:w1", "agent:codex"])
        self.assertEqual(wc.holder, "w1-codex")
        self.assertEqual(wc.state_dir, self.base)  # the locks: shared
        self.assertEqual(wc.loop_dir, self.base / "codex")  # its hand-backs: its own
        self.assertEqual(wc.stop_file, self.base / "codex" / "stop")
        self.assertEqual(wc.stop_files(), [self.base / "codex" / "stop", self.base / "stop"])

    def test_worker_toml_is_the_one_loop_as_before(self):
        wc = self.load(self.rig.configure())
        self.assertIsNone(wc.loop)
        self.assertIsNone(wc.holder)
        self.assertEqual(wc.loop_dir, self.base)
        self.assertEqual(wc.stop_files(), [self.base / "stop"])

    def test_a_holder_that_is_not_one_is_refused(self):
        for bad in ['"any"', '"two words"', "3"]:
            path = self.rig.configure("worker-codex.toml", labels=CODEX, holder=bad)
            with self.assertRaises(gonk.ConfigError, msg=bad):
                self.load(path)

    def test_a_file_name_that_is_no_loop_name_is_refused(self):
        path = self.rig.configure("worker-two words.toml", labels=CODEX)
        with self.assertRaises(gonk.ConfigError):
            self.load(path)

    def test_the_machine_counts_as_configured_with_only_per_agent_loops(self):
        self.rig.configure("worker-codex.toml", labels=CODEX)
        self.assertTrue(gonk.worker_configured(self.rig.checkout))


class LoopsSideBySide(unittest.TestCase):
    """Two loops of one checkout: what they share and what each keeps."""

    def setUp(self):
        self.ledger = FakeLedger()
        self.rig = Rig(self)
        self.codex_toml = self.rig.configure("worker-codex.toml", labels=CODEX)
        self.claude_toml = self.rig.configure("worker-claude.toml", labels=CLAUDE)
        quietly(self)

    def loop(self, path: Path) -> worker.Worker:
        return self.rig.worker(self.ledger, path)

    def test_the_repository_lock_is_shared(self):
        n = self.ledger.file("lib work", ["worker:w1", "agent:codex", "repo:lib"])
        lock = worker.RepoLock(self.loop(self.claude_toml).wc.state_dir, "lib")
        self.assertTrue(lock.acquire())  # claude's loop is working in lib
        self.addCleanup(lock.release)
        outcome = self.loop(self.codex_toml).run_once()
        self.assertEqual(outcome.code, worker.NOTHING, outcome.summary)
        self.assertIsNone(self.ledger.items[n]["claim"])

    def test_each_loop_remembers_only_its_own_hand_backs(self):
        n = self.ledger.file("lib work", ["worker:w1", "agent:codex", "repo:lib"])
        self.assertEqual(self.loop(self.codex_toml).run_once().code, worker.HANDED_BACK)
        # The dispatcher rejects the hand-back and routes the item to another agent.
        item = self.ledger.items[n]
        item["awaiting"] = None
        item["labels"] = sorted(["worker:w1", "agent:claude", "repo:lib"])
        self.ledger.dispatch(n, labels=[*item["labels"], "agent:codex"])
        take, skip, _ = self.loop(self.claude_toml).candidates()
        self.assertEqual([i["number"] for i, _ in take], [n], skip)
        # The claude loop took it without the codex loop's memory in the way; that memory is
        # the codex loop's own, and yields only to a dispatch newer than the hand-back.
        codex = self.loop(self.codex_toml)
        self.assertIsNone(worker.Attempts(self.loop(self.claude_toml).wc.loop_dir).seen("acme", n))
        self.assertIsNotNone(worker.Attempts(codex.wc.loop_dir).seen("acme", n))

    def test_a_loops_stop_file_stops_it_alone(self):
        codex, claude = self.loop(self.codex_toml), self.loop(self.claude_toml)
        codex.wc.stop_file.parent.mkdir(parents=True)
        codex.wc.stop_file.touch()
        self.assertNotIn(codex.wc.stop_file, claude.wc.stop_files())
        self.assertEqual(codex.loop(), 0)
        self.assertEqual(self.ledger.calls, [])  # stopped before it asked for anything

        thread = threading.Thread(target=claude.loop)
        thread.start()
        while not any("next" in c for c, _ in self.ledger.calls):
            time.sleep(0.02)
        claude.shutdown.set()
        thread.join()

    def test_the_shared_stop_file_stops_every_loop(self):
        codex, claude = self.loop(self.codex_toml), self.loop(self.claude_toml)
        codex.wc.global_stop.parent.mkdir(parents=True, exist_ok=True)
        codex.wc.global_stop.touch()
        self.assertEqual(codex.loop(), 0)
        self.assertEqual(claude.loop(), 0)
        self.assertEqual(self.ledger.calls, [])


class TheHolderOverride(unittest.TestCase):
    def test_every_claim_renewal_release_and_note_uses_the_loops_holder(self):
        ledger = FakeLedger()
        rig = Rig(self)
        quietly(self)
        n = ledger.file("lib work", ["worker:w1", "agent:codex", "repo:lib"])
        rig.agent_flags = ["--sleep", "2.5"]
        path = rig.configure("worker-codex.toml", labels=CODEX, holder='"w1-codex"', lease='"10s"')
        outcome = rig.worker(ledger, path).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertGreaterEqual(outcome.renewals, 1)
        claims = [
            stdin
            for command, stdin in ledger.calls
            if command.startswith("sink urn:gk:iki:ledger:acme:claim ")
        ]
        self.assertGreaterEqual(len(claims), 2)  # the claim and at least one renewal
        self.assertEqual(set(claims), {"w1-codex"})
        releases = [c for c, _ in ledger.calls if c.startswith("delete")]
        self.assertEqual(releases, [f"delete urn:gk:iki:ledger:acme:claim item={n} from=w1-codex"])
        [comment] = ledger.human_comments(n)
        self.assertIn("gonk worker `w1-codex` handed this back", comment["text"])

    def test_local_toml_need_not_name_a_holder_when_the_loop_does(self):
        rig = Rig(self)
        local = rig.checkout / ".gonk" / "local.toml"
        local.write_text(local.read_text().replace('holder = "w1"\n', ""))
        path = rig.configure("worker-codex.toml", labels=CODEX, holder='"w1-codex"')
        self.assertEqual(rig.worker(FakeLedger(), path).cfg.holder, "w1-codex")
        with self.assertRaises(gonk.ConfigError):  # and without either, it is refused
            rig.worker(FakeLedger(), rig.configure("worker-claude.toml", labels=CLAUDE))


class TheCommandLine(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.path = self.rig.configure("worker-codex.toml", labels=CODEX)

    def test_config_after_the_command_as_the_templated_unit_passes_it(self):
        for argv in (
            ["plan", "--config", str(self.path)],
            ["--config", str(self.path), "plan"],
        ):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = worker.main(
                    ["--repo", str(self.rig.checkout), *argv], make_ikigai=FakeLedger()
                )
            self.assertEqual(code, worker.NOTHING, argv)
            self.assertIn("worker:w1,agent:codex", out.getvalue())
        args = worker.parser().parse_args(["run", "--config", "x.toml"])
        self.assertEqual(args.config, "x.toml")
        self.assertIsNone(worker.parser().parse_args(["run"]).config)


class InitAgents(unittest.TestCase):
    AGENTS = ["claude", "codex", "hermes"]

    def setUp(self):
        self.rig = Rig(self)
        self.gonk_dir = self.rig.checkout / ".gonk"

    def init(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        argv = ["--repo", str(self.rig.checkout), "init-agents", "--name", "w1"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = worker.main([*argv, "--agents", ",".join(self.AGENTS), *extra])
        return code, out.getvalue()

    def table(self, agent: str) -> dict:
        return tomllib.loads((self.gonk_dir / f"worker-{agent}.toml").read_text())

    def test_from_the_sample_one_configuration_per_agent(self):
        code, out = self.init()
        self.assertEqual(code, 0, out)
        commands = {}
        for agent in self.AGENTS:
            t = self.table(agent)
            self.assertEqual(t["labels"], ["worker:w1", f"agent:{agent}"])
            self.assertEqual(t["holder"], f"w1-{agent}")
            self.assertNotIn("repos", t, "the sample's example repository is nobody's")
            commands[agent] = t["agent"]
            self.assertEqual(t["budget"], "45m")  # the sample's settings come along
            self.assertEqual(t["repos_from"], {"github": "ikigai-rs"})
            # Admission is already configured; the operator must still name its routers.
            # Explicit repositories may still be added as overrides.
            path = self.gonk_dir / f"worker-{agent}.toml"
            with path.open("a") as f:
                f.write('\n[repos.lib]\nremote = "https://github.com/acme/lib.git"\n')
            with self.assertRaises(gonk.ConfigError):
                worker.load_worker(gonk.load(self.rig.checkout), path)
            text = path.read_text()
            [line] = [w for w in text.splitlines() if w.startswith("routers = ")]
            path.write_text(text.replace(line, f"routers = {json.dumps([DISPATCHER])}"))
            wc = worker.load_worker(gonk.load(self.rig.checkout), path)
            self.assertEqual(wc.routers, [DISPATCHER])
            self.assertEqual((wc.loop, wc.holder), (agent, f"w1-{agent}"))
        self.assertIn("bypassPermissions", commands["claude"]["command"])
        self.assertIn("danger-full-access", commands["codex"]["command"])
        self.assertEqual(commands["hermes"], {"preset": "hermes"})

    def test_from_the_machines_worker_toml_its_settings_and_repositories_kept(self):
        self.rig.configure(budget='"2h"')  # worker.toml, with lib and other
        code, out = self.init()
        self.assertEqual(code, 0, out)
        t = self.table("codex")
        self.assertEqual(t["budget"], "2h")
        self.assertEqual(sorted(t["repos"]), ["lib", "other"])
        self.assertEqual(t["labels"], ["worker:w1", "agent:codex"])
        self.assertEqual(t["agent"]["command"][:2], ["codex", "exec"])
        self.assertEqual(t["routers"], [DISPATCHER])  # who may route work comes along too
        self.assertIn(".gonk/worker.toml", (self.gonk_dir / "worker-codex.toml").read_text())

    def test_a_second_run_never_overwrites(self):
        self.assertEqual(self.init()[0], 0)
        path = self.gonk_dir / "worker-codex.toml"
        path.write_text(path.read_text() + "# edited by hand\n")
        edited = path.read_bytes()
        code, out = self.init()
        self.assertEqual(code, 0, out)
        self.assertEqual(path.read_bytes(), edited)
        self.assertIn("kept     .gonk/worker-codex.toml", out)

    def test_an_agent_it_cannot_write_is_refused_before_anything_is_written(self):
        self.AGENTS = ["codex", "nonesuch"]
        code, out = self.init()
        self.assertEqual(code, worker.CONFIG, out)
        self.assertEqual(list(self.gonk_dir.glob("worker-*.toml")), [])


class Units(unittest.TestCase):
    UNITS = ROOT / "worker" / "units"

    def test_the_templated_systemd_unit_runs_the_instances_configuration(self):
        text = (self.UNITS / "gonk-worker@.service").read_text()
        [start] = [line for line in text.splitlines() if line.startswith("ExecStart=")]
        self.assertIn("tools/worker.py run --config %h/gonk-worker/.gonk/worker-%i.toml", start)
        [path] = [line for line in text.splitlines() if line.startswith("Environment=PATH=")]
        self.assertTrue(path.startswith("Environment=PATH=%h/.cargo/bin:"), path)
        self.assertIn("WantedBy=default.target", text)

    def test_a_launchd_daemon_per_agent(self):
        template = (self.UNITS / "dev.ikigai-rs.gonk-worker.plist").read_bytes()
        plists = {a: plistlib.loads(worker.agent_plist(template, a)) for a in ("codex", "hermes")}
        codex = plists["codex"]
        self.assertEqual(codex["Label"], "dev.ikigai-rs.gonk-worker.codex")
        self.assertEqual(codex["UserName"], "agent")
        argv = codex["ProgramArguments"]
        at = argv.index("run")
        self.assertEqual(
            argv[at + 1 : at + 3], ["--config", "/Users/agent/gonk-worker/.gonk/worker-codex.toml"]
        )
        self.assertEqual(codex["StandardOutPath"], "/tmp/ikigai-gonk-worker-codex.log")
        self.assertNotEqual(codex["StandardOutPath"], plists["hermes"]["StandardOutPath"])

    def test_plist_writes_one_file_per_agent(self):
        rig = Rig(self)
        out = rig.tmp / "plists"
        with contextlib.redirect_stdout(io.StringIO()):
            code = worker.main(["plist", "--agents", "claude,codex", "--out", str(out)])
        self.assertEqual(code, 0)
        names = sorted(p.name for p in out.iterdir())
        want = [f"dev.ikigai-rs.gonk-worker.{a}.plist" for a in ("claude", "codex")]
        self.assertEqual(names, want)


@unittest.skipUnless(
    GONK and IKIGAI,
    "needs `ikigai-gonk` and `ikigai` on PATH to start a scratch gonk (CI has neither)",
)
class TheHolderOverrideAgainstARealGonk(unittest.TestCase):
    """One machine identity (a certificate enrolled under the WORKER grant), two holder names:
    gonk takes a claim under the loop's holder from that certificate, renews it, records the
    machine as its principal, and releases it on the hand-back."""

    def test_a_loops_holder_from_the_machines_certificate(self):
        g = ScratchGonk(quic=True)
        self.addCleanup(g.stop)
        principal = g.add_client("w1", "acme", worker=True)
        g.start()
        rig = Rig(self, ledger_name="acme", target=str(g.socket))
        quietly(self)
        socket = gonk.Ikigai(gonk.Target(socket=str(g.socket)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):  # filed as the owner, over the socket
            code = gonk.main(
                ["--repo", str(rig.checkout), "file", "lib work", "--labels",
                 "worker:w1,agent:codex,repo:lib"]
            )  # fmt: skip
        self.assertEqual(code, 0, out.getvalue())
        n = int(out.getvalue().split()[0].split("#")[1])
        # The worker reaches gonk over QUIC with the machine's certificate; local.toml's holder
        # is the machine's, `w1`, and the loop's own is `w1-codex`.
        (rig.checkout / ".gonk" / "local.toml").write_text(
            f'transport = "quic"\nquic = "quic://127.0.0.1:{g.quic_port}"\n'
            f'cert_dir = "{g.bundle("w1")}"\nholder = "w1"\n'
        )

        # Over QUIC a dispatch may not borrow another principal's name: gonk's door refuses an
        # `author` shaped like a principal that is not the client's own, which is what makes a
        # dispatch's author worth checking.
        def dispatch(*extra: str) -> tuple[int, str]:
            out = io.StringIO()
            argv = [
                "--repo",
                str(rig.checkout),
                "dispatch",
                str(n),
                "--labels",
                "worker:w1,agent:codex",
            ]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                return gonk.main([*argv, *extra]), out.getvalue()

        code, said = dispatch("--as", "urn:iki:gonk:passkey:somebody-else")
        self.assertNotEqual(code, 0, said)
        self.assertIn("only the door names one", said)
        # With no `--as` the door names the machine itself, and this loop trusts that machine.
        code, said = dispatch()
        self.assertEqual(code, 0, said)
        item, err = gonk.item_json(gonk.load(rig.checkout), socket, n)
        self.assertIsNone(err)
        [sent] = [c for c in item["comments"] if gonk.parse_dispatch(c["text"])]
        self.assertTrue(gonk.same_principal(sent["author"], principal), sent)
        rig.agent_flags = ["--sleep", "2.5"]
        path = rig.configure(
            "worker-codex.toml",
            labels=CODEX,
            holder='"w1-codex"',
            lease='"10s"',
            routers=json.dumps([principal]),
        )
        seen = {}

        def watch():
            while not (rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            item, _ = gonk.item_json(gonk.load(rig.checkout), socket, n)
            seen.update(item["claim"] or {})

        thread = threading.Thread(target=watch)
        thread.start()
        cfg = gonk.load(rig.checkout)
        wc = worker.load_worker(cfg, path)
        outcome = worker.Worker(cfg, wc, gonk.Ikigai(cfg.target), home=rig.home).run_once()
        thread.join()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertGreaterEqual(outcome.renewals, 1)
        self.assertEqual(seen.get("holder"), "w1-codex", seen)
        self.assertEqual(seen.get("kind"), "machine", seen)
        if "principal" in seen:  # the JSON face names it on a ledger that records one
            self.assertEqual(seen["principal"], principal)
        item, err = gonk.item_json(gonk.load(rig.checkout), socket, n)
        self.assertIsNone(err)
        self.assertIsNone(item["claim"])  # released, by the compare-and-set on `w1-codex`
        texts = [c["text"] for c in item["comments"]]
        self.assertTrue(texts[-1].startswith("Evidence: "), texts)
        self.assertIn("gonk worker `w1-codex` handed this back", texts[-1])
        self.assertEqual(item["awaiting"], "dispatcher")


if __name__ == "__main__":
    unittest.main()
