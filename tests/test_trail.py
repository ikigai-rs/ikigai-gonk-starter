"""The session trail's writer (tools/trail.py, ledger item 3965): one `log:ToolCall` line per tool
call, in ikigai-log 0.14.0's format, from the Claude Code hook and from the worker loop.

tests/fixtures/trail/ikigai-log-0.14.0.json holds lines ikigai-log itself rendered for the same
calls (checked byte for byte against the published crate), so the format is the crate's, not a
guess at it. The rest: a default line carries no argument text, an opted-in line does, and one
session's lines read back in order.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from support import ROOT, FakeIkigai, load_hook, scratch_repo
from worker_support import worker

sys.path.insert(0, str(ROOT / "tools"))
import trail  # noqa: E402

worker_session = worker.worker_session

GOLDEN = ROOT / "tests" / "fixtures" / "trail" / "ikigai-log-0.14.0.json"
SESSIONS = ROOT / "tests" / "fixtures" / "sessions"
HOOK = ROOT / "hooks" / "claude" / "tool-trail.py"
SECRET = "echo made-up-secret-42"
SESSION_ID = "5a5a5a5a-1111-2222-3333-444455556666"
SESSION = trail.CLAUDE_SESSION_NS + SESSION_ID


def hook_repo(test) -> Path:
    """A scratch repository carrying this starter's tools/trail.py, as a generated one does."""
    repo = scratch_repo(test)
    (repo / "tools").mkdir(exist_ok=True)
    shutil.copy(ROOT / "tools" / "trail.py", repo / "tools" / "trail.py")
    return repo


def run_hook(repo: Path, event: dict) -> subprocess.CompletedProcess:
    event = {"cwd": str(repo), "session_id": SESSION_ID, **event}
    return subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True
    )


def bash_call(repo: Path, call_id: str, command: str = SECRET, end="PostToolUse") -> None:
    tool = {"tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": call_id}
    for name in ("PreToolUse", end):
        extra = {"tool_response": {"stdout": "made-up output"}} if name == "PostToolUse" else {}
        if name == "PostToolUseFailure":
            extra = {"error": "made-up failure"}
        done = run_hook(repo, {"hook_event_name": name, **tool, **extra})
        assert done.returncode == 0 and not done.stdout, done


class TheLine(unittest.TestCase):
    def test_each_line_is_the_one_ikigai_log_renders(self):
        cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
        self.assertGreater(len(cases), 5)
        for seq, c in enumerate(cases, 1):
            call = trail.Call(
                c["started_ms"] / 1000,
                c["session"],
                c["tool"],
                c["args"],
                c["outcome"],
                c["dur"],
                c["result"],
            )
            with self.subTest(seq=seq, tool=c["tool"]):
                self.assertEqual(trail.entry_line(call, seq, c["text"]), c["line"])

    def test_the_golden_text_reads_back_as_written(self):
        """Quotes, backslashes, newlines, controls, bidi marks and a cut all round-trip."""
        cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
        for c in cases:
            if not c["text"]:
                continue
            fields = dict(trail.parse_fields(c["line"].split(" ", 3)[3]))
            args, _ = trail.cut_text(c["args"])
            self.assertEqual(fields["args-text"], args)
            self.assertEqual(fields["args"], trail.digest(c["args"]))

    def test_a_long_text_is_cut_and_the_cut_is_named(self):
        call = trail.Call(0, SESSION, "Write", "é" * 3000, "ok", 1)
        line = trail.entry_line(call, 1, text=True)
        self.assertIn("cut=args-text", line)
        self.assertIn(trail.CUT_MARKER, line)
        self.assertIn(f"args={trail.digest('é' * 3000)}", line)  # the whole text's digest

    def test_an_outcome_session_or_tool_ikigai_log_refuses_is_refused(self):
        for call in [
            trail.Call(0, SESSION, "Bash", "", "maybe"),
            trail.Call(0, "not a session", "Bash", "", "ok"),
            trail.Call(0, SESSION, "", "", "ok"),
        ]:
            with self.assertRaises(ValueError):
                trail.entry_line(call, 1)


class TheHook(unittest.TestCase):
    def setUp(self):
        self.repo = hook_repo(self)
        self.log = self.repo / trail.TREE_TRAIL

    def test_a_default_line_carries_no_argument_text(self):
        bash_call(self.repo, "toolu_1")
        text = self.log.read_text(encoding="utf-8")
        self.assertNotIn("made-up-secret", text)
        self.assertNotIn("made-up output", text)
        self.assertNotIn("args-text=", text)
        _, lines = trail.read(self.log)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line.tool, "urn:log:tool:name:Bash")
        self.assertEqual(line.fields["session"], SESSION)
        self.assertEqual(line.fields["outcome"], "ok")
        self.assertRegex(line.fields["dur"], r"^\d+$")
        self.assertEqual(
            line.fields["args"], trail.digest(trail.canonical_json({"command": SECRET}))
        )
        self.assertRegex(line.fields["result"], r"^sha256:[0-9a-f]{64}$")
        self.assertFalse(any((self.repo / trail.TREE_PENDING).iterdir()), "the start is taken")

    def test_an_opted_in_line_carries_the_text(self):
        (self.repo / ".gonk" / "local.toml").write_text("[trail]\ntext = true\n")
        bash_call(self.repo, "toolu_1")
        _, lines = trail.read(self.log)
        self.assertEqual(json.loads(lines[0].fields["args-text"]), {"command": SECRET})
        self.assertIn("made-up output", lines[0].fields["result-text"])

    def test_only_true_opts_in(self):
        (self.repo / ".gonk" / "local.toml").write_text('[trail]\ntext = "yes"\n')
        bash_call(self.repo, "toolu_1")
        self.assertNotIn("made-up-secret", self.log.read_text(encoding="utf-8"))

    def test_one_sessions_lines_read_back_in_order(self):
        bash_call(self.repo, "toolu_1", "ls")
        run_hook(  # another session's call, between this session's
            self.repo,
            {
                "hook_event_name": "PostToolUse",
                "session_id": "another-session",
                "tool_name": "Read",
                "tool_input": {"file_path": "x"},
            },
        )
        bash_call(self.repo, "toolu_2", "git status", end="PostToolUseFailure")
        bash_call(self.repo, "toolu_3", "git diff")
        mine = trail.session_trail(self.log, SESSION)
        self.assertEqual([ln.seq for ln in mine], [1, 3, 4])
        self.assertEqual([ln.fields["outcome"] for ln in mine], ["ok", "failed", "ok"])
        digests = [
            trail.digest(trail.canonical_json({"command": c}))
            for c in ("ls", "git status", "git diff")
        ]
        self.assertEqual([ln.fields["args"] for ln in mine], digests)
        other = trail.session_trail(self.log, trail.CLAUDE_SESSION_NS + "another-session")
        self.assertEqual([(ln.seq, "dur" in ln.fields) for ln in other], [(2, False)])

    def test_a_refusal_by_the_claim_hook_is_a_refused_line(self):
        hook = load_hook()
        event = {
            "cwd": str(self.repo),
            "session_id": SESSION_ID,
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo / "README.md"), "new_string": SECRET},
        }
        code, _ = hook.decide(event, FakeIkigai())
        self.assertEqual(code, 2)
        hook.trail_refused(event)
        _, lines = trail.read(self.log)
        self.assertEqual(
            [(ln.tool, ln.fields["outcome"]) for ln in lines],
            [
                ("urn:log:tool:name:Edit", "refused"),
            ],
        )
        self.assertNotIn("made-up-secret", self.log.read_text(encoding="utf-8"))

    def test_the_hook_never_stands_in_the_way(self):
        for stdin in ["not json", "[]", json.dumps({"hook_event_name": "PostToolUse"})]:
            done = subprocess.run(
                [sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True
            )
            self.assertEqual((done.returncode, done.stdout), (0, ""))
        (self.repo / ".gonk" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".gonk" / "state" / "trail").write_text("a file where the directory goes")
        bash_call(self.repo, "toolu_1")  # the line cannot land; the hook still exits 0


class TheSegment(unittest.TestCase):
    def setUp(self):
        self.repo = hook_repo(self)
        self.trail = trail.Trail(self.repo / "t.log", "urn:log:tools:tree-", "urn:iki:tools:tree:")

    def test_its_header_is_ikigai_logs(self):
        self.trail.append(trail.Call(0, SESSION, "Bash", "{}", "ok", 1))
        lines = (self.repo / "t.log").read_text().splitlines()
        self.assertEqual(lines[0], "# ikigai-log v1")
        self.assertEqual(lines[1], "@prefix log: <https://ikigai-rs.dev/ns/log#> .")
        self.assertRegex(lines[2], r"^@name     urn:log:tools:tree-[0-9a-f]{16}$")
        self.assertEqual(lines[6], "@prev     genesis")
        self.assertEqual(lines[7], "")

    def test_seq_is_read_from_the_line_not_from_text_inside_it(self):
        self.trail.text = True
        self.trail.append(trail.Call(0, SESSION, "Bash", "x seq=99 y", "ok", 1))
        self.assertEqual(self.trail.append(trail.Call(0, SESSION, "Bash", "{}", "ok", 1)), 2)

    def test_record_is_best_effort(self):
        (self.repo / "t.log").write_text("not a segment\n")
        self.assertIsNone(self.trail.record(trail.Call(0, SESSION, "Bash", "{}", "ok")))
        self.assertEqual((self.repo / "t.log").read_text(), "not a segment\n")


class TheWorker(unittest.TestCase):
    def calls(self, harness: str, fixture: str, text: bool = False):
        repo = hook_repo(self)
        log = repo / "tools.log"
        writer = trail.Trail(
            log, worker.WORKER_TRAIL_NAME_NS, worker.WORKER_TRAIL_INSTANCE_NS, text
        )
        calls = trail.Calls(writer, worker_session.SESSION_NS[harness])
        seen = worker_session.collector(harness, calls)
        for line in (SESSIONS / fixture).read_text().splitlines(keepends=True):
            seen.feed(line)
        calls.close()
        return log, trail.read(log)[1]

    def test_claude_stream_events_are_one_line_per_call(self):
        log, lines = self.calls("claude", "claude-stream.jsonl")
        session = trail.CLAUDE_SESSION_NS + SESSION_ID
        self.assertEqual(
            [(ln.tool, ln.fields["outcome"], ln.fields["session"], ln.seq) for ln in lines],
            [
                ("urn:log:tool:name:Bash", "ok", session, 1),
                ("urn:log:tool:name:Edit", "failed", session, 2),  # no result came back
                ("urn:log:tool:name:Bash", "failed", session, 3),
            ],
        )
        self.assertIn("dur", lines[0].fields)
        self.assertNotIn("args-text=", log.read_text())
        self.assertEqual(lines[0].fields["args"], trail.digest('{"command":"ls"}'))

    def test_codex_items_and_older_events_are_one_line_per_call(self):
        _, lines = self.calls("codex", "codex-exec.jsonl", text=True)
        self.assertEqual(
            [(ln.tool, ln.fields["outcome"]) for ln in lines],
            [
                ("urn:log:tool:name:command_execution", "ok"),
                ("urn:log:tool:name:file_change", "ok"),
            ],
        )
        self.assertEqual(json.loads(lines[0].fields["args-text"]), {"command": "bash -lc ls"})
        self.assertNotIn("dur", lines[1].fields)  # seen only completed
        _, lines = self.calls("codex", "codex-legacy.jsonl")
        self.assertEqual([ln.tool for ln in lines], ["urn:log:tool:name:exec_command"])

    def test_claude_is_asked_for_its_stream(self):
        self.assertEqual(
            worker_session.with_json_output("claude", ["claude", "-p", "hi"]),
            ["claude", "--output-format", "stream-json", "--verbose", "-p", "hi"],
        )

    def test_the_text_opt_in_is_the_operators_and_off_by_default(self):
        self.assertFalse(worker.load_trail({}, "w.toml"))
        self.assertTrue(worker.load_trail({"text": True}, "w.toml"))
        for bad in [{"text": "yes"}, {"other": 1}, "text"]:
            with self.assertRaises(worker.gonk.ConfigError):
                worker.load_trail(bad, "w.toml")


if __name__ == "__main__":
    unittest.main()
