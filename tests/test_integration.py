"""Against a REAL gonk: a scratch one this test starts, on a scratch port, and stops by PID.

Skipped unless `ikigai-gonk` and `ikigai` are both on PATH. CI has neither (gonk builds
RocksDB from source and is not on crates.io), so there it skips and says why; on a machine
with both installed it runs as part of `just gates`.

The scratch gonk keeps everything in a temporary directory: its own config home, data home
and socket, an ephemeral port that is never 1060 or 4433, and no backups. It never touches a
gonk already running on the machine.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import ROOT, gonk, load_hook, scratch_repo

GONK = shutil.which("ikigai-gonk")
IKIGAI = shutil.which("ikigai")
LIVE_PORTS = {1060, 4433}


def free_port() -> int:
    """A port free on both TCP (gonk's HTTP door) and UDP (its QUIC door)."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
        if port in LIVE_PORTS:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.bind(("0.0.0.0", port))
        except OSError:
            continue
        return port
    raise RuntimeError("no free port")


class ScratchGonk:
    """One gonk in a temporary directory. A Unix socket path must fit in 104 bytes, so the
    directory is a short one under /tmp rather than the system's long temporary path."""

    def __init__(self, quic: bool):
        self.dir = Path(tempfile.mkdtemp(prefix="gks", dir="/tmp"))
        self.port = free_port()
        self.quic = quic
        self.config_home = self.dir / "cfg"
        self.socket = self.dir / "g.sock"
        self.proc = None

    def add_client(self, name: str, ledger: str) -> str:
        """Enroll a QUIC client; answer the principal gonk printed for it."""
        out = subprocess.run(
            [GONK, "client", "add", name, "--ledger", f"{ledger}=write", "--port", str(self.port)]
            + ["--config-home", str(self.config_home)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return re.search(r"principal\s+(\S+)", out).group(1)

    def bundle(self, name: str) -> Path:
        return self.config_home / "gonk" / "quic" / "clients" / name

    def start(self) -> None:
        argv = [GONK, "--config-home", str(self.config_home), "--data-home", str(self.dir / "d")]
        argv += ["--port", str(self.port), "--socket", str(self.socket), "--no-backup"]
        if not self.quic:
            argv.append("--no-quic")
        self.log = (self.dir / "gonk.log").open("w")
        self.proc = subprocess.Popen(argv, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        probe = gonk.Ikigai(gonk.Target(socket=str(self.socket)))
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"scratch gonk exited: {(self.dir / 'gonk.log').read_text()}")
            if self.socket.exists() and probe.run("source urn:gk:iki:ledger:ledgers").ok:
                return
            time.sleep(0.2)
        raise RuntimeError("scratch gonk did not answer within 60s")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()  # by PID: this process, and only this one
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if getattr(self, "log", None):
            self.log.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def graph_select(target: gonk.Target, query: Path, ledger: str, **params: str) -> list[str]:
    """Run a saved query through gonk's store as ikigai-script would, answering its rows.

    gonk does not mount `urn:script:` yet, so this does by hand what a published query's
    `:result` will: `LEDGER` replaced by the ledger, the `# @param` block dropped, each
    parameter bound to a literal (ikigai-script binds terms in the algebra; for a string in a
    FILTER the two agree), and the graphs it names given to the store's graph-select door."""
    lines = [ln for ln in query.read_text().splitlines() if not ln.lstrip().startswith("#")]
    text = "\n".join(lines).replace("LEDGER", ledger)
    for name, value in params.items():
        text = text.replace(f"?{name}", "'" + value + "'")
    quoted = text.replace("\\", "\\\\").replace('"', '\\"')
    graph = f"urn:iki:ledger:graph:{ledger}"
    command = (
        f"source urn:gk:iki:store:graph-select graph={graph} "
        f'as=text/tab-separated-values query="{quoted}"'
    )
    answer = gonk.Ikigai(target).run(command)
    assert answer.ok, answer.text()
    rows = [ln for ln in answer.out.splitlines() if ln and not ln.startswith("[")]
    return rows[1:]  # without the header


def tool(repo: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = gonk.main(["--repo", str(repo), *argv])
    return code, out.getvalue()


@unittest.skipUnless(
    GONK and IKIGAI,
    "needs `ikigai-gonk` and `ikigai` on PATH to start a scratch gonk (CI has neither: gonk "
    "is installed from git and builds RocksDB; run `just gates` where both are installed)",
)
class AgainstARealGonk(unittest.TestCase):
    def test_the_whole_loop_over_the_socket(self):
        g = ScratchGonk(quic=False)
        self.addCleanup(g.stop)
        g.start()
        one, two = scratch_repo(self), scratch_repo(self)
        sock = str(g.socket)

        code, out = tool(one, "init", "--ledger", "acme", "--socket", sock, "--holder", "one")
        self.assertEqual(code, 0, out)
        self.assertIn("holds nothing yet", out)
        self.assertRegex(out, r"absent\s+scripts")
        code, out = tool(two, "init", "--ledger", "acme", "--socket", sock, "--holder", "two")
        self.assertEqual(code, 0, out)

        code, out = tool(one, "file", "Lend a book", "--priority", "1")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("acme#1 "), out)
        code, out = tool(one, "init")
        self.assertIn("`acme` holds 1 item(s), 1 open", out)

        # Tree one claims on a one-second lease; tree two is refused while it runs.
        code, out = tool(one, "claim", "1", "--lease", "1s")
        self.assertEqual(code, 0, out)
        self.assertIn("claimed by one until", out)
        hook = load_hook()
        edit = {"tool_name": "Edit", "tool_input": {"file_path": str(one / "artifacts" / "x")}}
        self.assertEqual(hook.decide(edit), (0, ""))
        code, out = tool(two, "claim", "1")
        self.assertEqual(code, 1, out)
        self.assertIn("already claimed by one", out)

        # The lease runs out: not free, but no longer good for writing in tree one.
        time.sleep(1.5)
        code, out = tool(one, "status")
        self.assertEqual(code, 1, out)
        self.assertIn("ran out", out)
        self.assertEqual(hook.decide(edit)[0], 2)
        code, out = tool(two, "claim", "1")
        self.assertEqual(code, 1, out)
        self.assertIn("takeover=true from=one", out)

        # Tree two takes it over; tree one is told its claim is gone.
        code, out = tool(two, "claim", "1", "--takeover-from", "one")
        self.assertEqual(code, 0, out)
        self.assertIn("taken over from one by two", out)
        code, out = tool(one, "status")
        self.assertIn("held by `two`", out)

        # The worker reports; the dispatcher closes.
        url = "https://github.com/example/library/pull/1"
        code, out = tool(two, "report", "1", "--evidence", url)
        self.assertEqual(code, 0, out)
        self.assertIn("released", out)
        (one / ".gonk" / "state" / "claim.toml").unlink()
        code, out = tool(one, "close", "1", "--evidence", url)
        self.assertEqual(code, 0, out)
        self.assertIn("closed acme#1 (done)", out)
        code, out = tool(one, "next")
        self.assertIn("nothing is ready", out)

    def test_a_machine_identity_over_quic(self):
        g = ScratchGonk(quic=True)
        self.addCleanup(g.stop)
        principal = g.add_client("worker", "acme")
        g.start()
        repo = scratch_repo(self)
        args = ["init", "--ledger", "acme", "--holder", "worker"]
        args += ["--quic", f"quic://127.0.0.1:{g.port}", "--cert-dir", str(g.bundle("worker"))]
        code, out = tool(repo, *args)
        self.assertEqual(code, 0, out)
        # The identity init computes is the one gonk enrolled, named as gonk mints it now. A
        # gonk from before ikigai-gonk PR 126 prints the older spelling of the same machine, so
        # the two are compared by fingerprint (both spellings name one client to gonk).
        [identity] = re.findall(r"identity\s+(\S+) \(from ", out)
        fingerprint = re.fullmatch(r"urn:iki:(?:host:)?gonk:client:([0-9a-f]+)", principal)
        self.assertIsNotNone(fingerprint, principal)
        self.assertEqual(identity, gonk.client_iri(fingerprint.group(1)))
        self.assertIn("ikigai-gonk client add worker --cert client.crt --ledger acme=write", out)

        code, out = tool(repo, "file", "Run unattended")
        self.assertEqual(code, 0, out)
        code, out = tool(repo, "claim", "1")
        self.assertEqual(code, 0, out)
        cfg = gonk.load(repo)
        item, err = gonk.item_json(cfg, gonk.Ikigai(cfg.target), 1)
        self.assertIsNone(err)
        self.assertEqual(item["claim"]["holder"], "worker")
        self.assertEqual(item["claim"]["kind"], "machine")
        self.assertEqual(item["author"], principal)

    def test_the_library_sample_end_to_end(self):
        """Generate the sample, point it at a scratch gonk, seed it twice, ask next, claim,
        ask the saved queries, and run the generated repository's own tests."""
        g = ScratchGonk(quic=False)
        self.addCleanup(g.stop)
        g.start()
        tmp = Path(tempfile.mkdtemp(prefix="gonk-sample-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = tmp / "library"
        made = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "new.py"), str(repo), "--target", "python"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)

        args = ["init", "--ledger", "library", "--socket", str(g.socket), "--holder", "me"]
        code, out = tool(repo, *args)
        self.assertEqual(code, 0, out)
        self.assertIn("8 open task(s) in 2 change(s) in flight", out)
        self.assertIn("4 saved here are ready but unpublished", out)

        code, out = tool(repo, "seed")
        self.assertEqual(code, 0, out)
        self.assertIn("seed: 8 filed, 0 already filed", out)
        self.assertIn("6 order link(s) added", out)  # 5 in add-holds, 1 in cap-late-fees
        self.assertIn("ordered     library#1 blocks library#2 (add-holds 1.1 before 1.2)", out)
        code, out = tool(repo, "seed")
        self.assertEqual(code, 0, out)
        self.assertIn("seed: 0 filed, 8 already filed", out)
        self.assertIn("0 order link(s) added", out)

        cfg = gonk.load(repo)
        item, err = gonk.item_json(cfg, gonk.Ikigai(cfg.target), 1)
        self.assertIsNone(err)
        self.assertEqual(item["key"], "urn:openspec:change:add-holds:task:1.1")
        self.assertEqual(item["labels"], ["artifact:cli", "artifact:service", "change:add-holds"])
        self.assertIn(
            "urn:openspec:spec:holds:requirement:a-member-may-hold-a-title-with-no-copy-on-the-shelf",
            item["about"],
        )

        # next, in task order: the first open task of each change is ready, and each later
        # task waits on the one before it. Without the links all 8 were ready, newest first,
        # so cap-late-fees 1.2 ranked above 1.1.
        code, out = tool(repo, "next")
        self.assertEqual(code, 0, out)
        self.assertIn("ready: 2   excluded: 6", out)
        ranked = re.findall(r"^ ?\d+\. (library#\d+) ", out, re.MULTILINE)
        self.assertEqual(sorted(ranked), ["library#1", "library#7"], out)
        for later, earlier in [(2, 1), (3, 2), (4, 3), (5, 4), (6, 5), (8, 7)]:
            self.assertIn(f"not library#{later}: blocked by library#{earlier}", out)
        self.assertNotIn("[uncacheable]", out)  # the CLI's cache trailer is not gonk's answer
        code, out = tool(repo, "claim", "7")
        self.assertEqual(code, 0, out)
        code, out = tool(repo, "status")
        self.assertEqual(code, 0, out)
        self.assertIn("held by `me`", out)

        # The saved queries, against the ledger seed filed.
        queries = repo / ".gonk" / "queries"
        rows = graph_select(
            cfg.target, queries / "open-items-for-artifact.rq", "library", artifact="cli"
        )
        self.assertEqual(len(rows), 6, rows)  # add-holds touches the cli; cap-late-fees does not
        rows = graph_select(
            cfg.target, queries / "open-items-for-artifact.rq", "library", artifact="service"
        )
        self.assertEqual(len(rows), 8, rows)
        self.assertTrue(any(r.endswith('"me"') for r in rows), rows)  # item 7's holder
        rows = graph_select(cfg.target, queries / "open-work-by-change.rq", "library")
        self.assertEqual([r.split("\t")[0] for r in rows], ['"add-holds"', '"cap-late-fees"'])

        # Closing a change's first task makes its second ready, and only its second (item 7,
        # cap-late-fees 1.1, is claimed above, so it is not offered either).
        code, out = tool(repo, "close", "1", "--evidence", "https://example.com/pull/1")
        self.assertEqual(code, 0, out)
        code, out = tool(repo, "next")
        self.assertEqual(code, 0, out)
        ranked = re.findall(r"^ ?\d+\. (library#\d+) ", out, re.MULTILINE)
        self.assertEqual(ranked, ["library#2"], out)
        self.assertIn("not library#3: blocked by library#2", out)
        self.assertIn("not library#7: claimed by me", out)

        done = subprocess.run(
            [sys.executable, "scripts/test.py"], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
