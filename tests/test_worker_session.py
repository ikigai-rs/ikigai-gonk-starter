"""The session summary a worker records on its hand-back (tools/worker_session.py, ledger item
3023), from recorded harness output: tests/fixtures/sessions/ holds made-up `claude -p
--output-format json` / stream-json results and `codex exec --json` events, no real session.

Raw facts only: no cost, no transcript text, and a summary that cannot be made is one line on the
hand-back, never a failed run.
"""

from __future__ import annotations

import contextlib
import io
import json
import time
import unittest

from support import ROOT, gonk
from worker_support import DISPATCHER, FakeLedger, Rig, worker

worker_session = worker.worker_session

FIXTURES = ROOT / "tests" / "fixtures" / "sessions"


def read(name: str, harness: str) -> tuple[worker_session.Collector, str]:
    """Feed a fixture to a collector, as the Reader does; the collector and the log's text."""
    seen = worker_session.collector(harness)
    log = []
    for line in (FIXTURES / name).read_text().splitlines(keepends=True):
        text = seen.feed(line)
        if text:
            log.append(text)
    return seen, "".join(log)


def summarize(name: str, harness: str, argv=None, code: int = 0) -> worker_session.Session:
    seen, _ = read(name, harness)
    argv = argv or [harness]
    return worker_session.summarize(harness, seen, argv, code, 1234, "lawnboy", env={})


class TheCommand(unittest.TestCase):
    def test_claude_and_codex_are_asked_for_json(self):
        add = worker_session.with_json_output
        self.assertEqual(
            add("claude", ["claude", "-p", "hi"]), ["claude", "--output-format", "json", "-p", "hi"]
        )
        self.assertEqual(
            add("codex", ["codex", "exec", "--sandbox", "x", "hi"]),
            ["codex", "exec", "--json", "--sandbox", "x", "hi"],
        )

    def test_a_command_that_asks_already_or_is_not_the_harness_is_not_rewritten(self):
        add = worker_session.with_json_output
        for harness, argv in [
            ("claude", ["claude", "-p", "--output-format", "stream-json", "--verbose", "hi"]),
            ("claude", ["claude", "-p", "--output-format=json", "hi"]),
            ("codex", ["codex", "exec", "--json", "hi"]),
            ("claude", ["/opt/bin/run-claude", "hi"]),  # a wrapper asks for itself
            (None, ["hermes", "-z", "hi"]),
        ]:
            self.assertEqual(add(harness, argv), argv, argv)

    def test_the_harness_is_the_programs_name_or_the_configured_one(self):
        self.assertEqual(worker_session.harness_of(["/usr/local/bin/claude", "-p"]), "claude")
        self.assertEqual(worker_session.harness_of(["codex", "exec"]), "codex")
        self.assertIsNone(worker_session.harness_of(["hermes", "-z"]))
        self.assertEqual(worker_session.harness_of(["my-wrapper"], "codex"), "codex")

    def test_a_preset_runs_with_json_output(self):
        rig = Rig(self)
        path = rig.checkout / ".gonk" / "worker.toml"
        base = f'labels = ["w"]\nrouters = {json.dumps([DISPATCHER])}\n'
        repo = '\n[repos.lib]\nremote = "https://github.com/acme/lib.git"\n'
        for preset, harness in [("claude", "claude"), ("codex", "codex"), ("hermes", None)]:
            path.write_text(base + f'[agent]\npreset = "{preset}"\n' + repo)
            self.assertEqual(worker.load_worker(gonk.load(rig.checkout)).harness, harness)
        path.write_text(base + '[agent]\ncommand = ["w", "{prompt}"]\nharness = "hermes"\n' + repo)
        with self.assertRaises(gonk.ConfigError) as refused:
            worker.load_worker(gonk.load(rig.checkout))
        self.assertIn("harness", str(refused.exception))


class Claude(unittest.TestCase):
    def test_the_result_object_is_the_summary_and_its_cost_is_never_read(self):
        session = summarize("claude-result.json", "claude")
        self.assertEqual(
            session.summary,
            {
                "session": "0b5e1c2d-3f40-4a5b-8c6d-7e8f9a0b1c2d",
                "machineName": "lawnboy",
                "harness": "claude",
                "model": "claude-opus-5-5",  # the model with the most tokens in modelUsage
                "exitStatus": 0,
                "wallClockMs": 412345,  # the harness's duration_ms
                "turns": 17,
                "inputTokens": 5120,
                "cachedInputTokens": 880000,
                "cacheCreationTokens": 64000,
                "outputTokens": 21000,
            },
        )
        sent = json.dumps(session.summary)
        for word in ("cost", "12.3", "widget", "result"):
            self.assertNotIn(word, sent)
        self.assertIn("Session summary: claude session 0b5e1c2d-", session.line)

    def test_the_agents_words_still_go_to_the_log(self):
        _, log = read("claude-result.json", "claude")
        self.assertEqual(log, "Opened the pull request for the made-up widget fix; CI is green.\n")

    def test_stream_json_counts_tool_calls_and_names_the_model_from_its_init(self):
        session = summarize("claude-stream.jsonl", "claude")
        self.assertEqual(session.summary["toolCalls"], 3)
        self.assertEqual(session.summary["model"], "claude-opus-5-5")
        self.assertEqual(session.summary["session"], "5a5a5a5a-1111-2222-3333-444455556666")
        _, log = read("claude-stream.jsonl", "claude")
        self.assertEqual(log, "Done with the made-up widget.\n")  # no tool output in the log

    def test_a_non_zero_exit_is_its_exit_status_and_the_error_is_said(self):
        session = summarize("claude-error.json", "claude", code=1)
        self.assertEqual(session.summary["exitStatus"], 1)
        self.assertEqual(session.summary["model"], "claude-sonnet-5-5")
        self.assertNotIn("is_error", session.summary)  # not a key of the shape
        self.assertIn("exit 1; claude reported an error", session.line)

    def test_no_result_or_an_unparseable_one_is_one_line_why(self):
        seen = worker_session.collector("claude")
        session = worker_session.summarize("claude", seen, ["claude"], 0, 10, "m", env={})
        self.assertIsNone(session.summary)
        self.assertEqual(
            session.line,
            "No session summary: claude wrote no result object (run it with "
            "`--output-format json`).",
        )
        session = summarize("claude-garbled.txt", "claude")
        self.assertIsNone(session.summary)
        self.assertTrue(session.line.startswith("No session summary: claude's output did not "))
        self.assertEqual(len(session.line.splitlines()), 1)
        _, log = read("claude-garbled.txt", "claude")
        self.assertIn("warning: a made-up line on stderr", log)


class Codex(unittest.TestCase):
    def test_the_exec_events_are_the_summary(self):
        session = summarize("codex-exec.jsonl", "codex", ["codex", "exec", "-m", "gpt-5.5-codex"])
        self.assertEqual(
            session.summary,
            {
                "session": "0199a213-81c0-7800-8aa1-bbab2a035a53",
                "machineName": "lawnboy",
                "harness": "codex",
                "model": "gpt-5.5-codex",  # from the command: these events name none
                "exitStatus": 0,
                "wallClockMs": 1234,  # measured by the worker
                "turns": 1,
                "toolCalls": 2,
                # Codex counts its cached input inside input_tokens: the uncached rest
                "inputTokens": 24763 - 24448,
                "cachedInputTokens": 24448,
                "outputTokens": 122,
            },
        )
        _, log = read("codex-exec.jsonl", "codex")
        self.assertEqual(log, "Opened the made-up pull request.\n")
        self.assertNotIn("README", json.dumps(session.summary))

    def test_older_releases_session_events_are_read_too(self):
        session = summarize("codex-legacy.jsonl", "codex")
        s = session.summary
        self.assertEqual(s["session"], "7c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f")
        self.assertEqual(s["model"], "gpt-5.5-codex")
        # the last running total, not a sum of them
        self.assertEqual(
            (s["inputTokens"], s["cachedInputTokens"], s["outputTokens"]), (500, 1500, 80)
        )
        self.assertEqual((s["turns"], s["toolCalls"]), (1, 1))

    def test_the_model_from_codexs_own_config(self):
        home = Rig(self).tmp / "codex-home"
        home.mkdir()
        (home / "config.toml").write_text('model = "gpt-made-up"\n')
        seen, _ = read("codex-exec.jsonl", "codex")
        session = worker_session.summarize(
            "codex", seen, ["codex", "exec"], 0, 5, "m", env={"CODEX_HOME": str(home)}
        )
        self.assertEqual(session.summary["model"], "gpt-made-up")
        seen, _ = read("codex-exec.jsonl", "codex")
        session = worker_session.summarize("codex", seen, ["codex", "exec"], 0, 5, "m", env={})
        self.assertIsNone(session.summary)
        self.assertIn("ledger:SessionSummaryShape: it has no model", session.line)

    def test_a_failed_turn_is_said_and_still_summarized(self):
        session = summarize("codex-failed.jsonl", "codex", ["codex", "-m", "x"], code=1)
        self.assertEqual(session.summary["exitStatus"], 1)
        self.assertNotIn("inputTokens", session.summary)  # no usage event: not reported
        self.assertIn("codex reported an error", session.line)
        _, log = read("codex-failed.jsonl", "codex")
        self.assertIn("made-up stream disconnected", log)


class TheShape(unittest.TestCase):
    GOOD = {
        "session": "a-1",
        "machineName": "m",
        "harness": "claude",
        "model": "x",
        "exitStatus": 0,
        "wallClockMs": 0,
    }

    def test_the_closed_shape_refuses_what_it_does_not_name(self):
        self.assertIsNone(worker_session.shape_problem(self.GOOD))
        for change, says in [
            ({"cost": 1}, "`cost` is not a key"),
            ({"transcript": "hi"}, "`transcript` is not a key"),
            ({"harness": "hermes"}, "not one of claude, codex"),
            ({"turns": -1}, "turns is not a whole number of 0 or more"),
            ({"outputTokens": True}, "outputTokens"),
            ({"session": "a b"}, "session id"),
            ({"session": "x" * 129}, "session id"),
            ({"exitStatus": "0"}, "exitStatus"),
        ]:
            problem = worker_session.shape_problem({**self.GOOD, **change})
            self.assertIn(says, problem or "", change)
        for key in ("session", "machineName", "harness", "model", "exitStatus", "wallClockMs"):
            missing = {k: v for k, v in self.GOOD.items() if k != key}
            self.assertEqual(worker_session.shape_problem(missing), f"it has no {key}")

    def test_the_shape_is_the_fake_ledgers(self):
        keys = [key for key, _, _ in worker_session.SHAPE]
        self.assertEqual(keys, [*FakeLedger.SESSION_REQUIRED, *FakeLedger.SESSION_OPTIONAL])
        required = [key for key, _, need in worker_session.SHAPE if need]
        self.assertEqual(required, list(FakeLedger.SESSION_REQUIRED))


class TheHandBack(unittest.TestCase):
    """The loop, with the fake agent printing a fixture as its harness would."""

    def setUp(self):
        self.ledger = FakeLedger()
        self.rig = Rig(self)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def run_item(self, harness: str, fixture: str, *flags: str, **config):
        self.rig.harness = harness
        self.rig.agent_flags = ["--emit", str(FIXTURES / fixture), *flags]
        n = self.ledger.file("Fix the widget", ["worker:w1", "repo:lib"])
        self.rig.configure(**config)
        return n, self.rig.worker(self.ledger).run_once()

    def comments(self, n: int) -> list[str]:
        return [c["text"] for c in self.ledger.human_comments(n)]

    def session_calls(self) -> list[tuple[str, str | None]]:
        return [(c, s) for c, s in self.ledger.calls if c.endswith(":sessions")]

    def test_claude_hands_back_and_records_its_session(self):
        n, outcome = self.run_item("claude", "claude-result.json")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        [comment] = self.comments(n)
        self.assertTrue(comment.startswith("Evidence: https://github.com/acme/lib/pull/1\n"))
        self.assertIn(
            "Session summary: claude session 0b5e1c2d-3f40-4a5b-8c6d-7e8f9a0b1c2d", comment
        )
        [summary] = self.ledger.sessions[n]
        self.assertEqual(summary["harness"], "claude")
        self.assertEqual(summary["exitStatus"], 0)
        self.assertEqual(summary["outputTokens"], 21000)
        [(command, content)] = self.session_calls()
        self.assertEqual(command, f"sink urn:gk:iki:ledger:acme:item:{n}:sessions")
        self.assertNotIn("cost", content)
        self.assertNotIn("widget", content)  # no transcript text leaves the worker
        # sent after the hand-back, which it is recorded on
        order = [c for c, _ in self.ledger.calls if ":comment " in c or c.endswith(":sessions")]
        self.assertEqual(order[-1], command)
        # and the agent's words are in the item's log, as before
        [log] = (self.rig.home / "work" / "lib").glob(f"item-{n}.log")
        self.assertIn("made-up widget fix", log.read_text())

    def test_codex_hands_back_and_records_its_session(self):
        n, outcome = self.run_item("codex", "codex-exec.jsonl", "--model", "gpt-5.5-codex")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        [summary] = self.ledger.sessions[n]
        self.assertEqual(summary["session"], "0199a213-81c0-7800-8aa1-bbab2a035a53")
        self.assertEqual(summary["model"], "gpt-5.5-codex")
        self.assertGreaterEqual(summary["wallClockMs"], 0)

    def test_a_non_zero_exit_with_a_pull_request_records_its_exit_status(self):
        n, outcome = self.run_item("claude", "claude-error.json", "--exit", "1")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        [summary] = self.ledger.sessions[n]
        self.assertEqual(summary["exitStatus"], 1)
        [comment] = self.comments(n)
        self.assertIn("exit 1; claude reported an error", comment)

    def test_a_non_zero_exit_with_no_result_hands_back_without_a_summary(self):
        n, outcome = self.run_item("claude", "claude-garbled.txt", "--exit", "2")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        [comment] = self.comments(n)
        [line] = [w for w in comment.splitlines() if "session summary" in w.lower()]
        self.assertTrue(line.startswith("No session summary: claude's output did not parse"))
        self.assertEqual(self.session_calls(), [])
        self.assertNotIn(n, self.ledger.sessions)

    def test_a_budget_stopped_run_says_why_it_has_no_summary(self):
        started = time.monotonic()
        n, outcome = self.run_item("codex", "codex-stopped.jsonl", "--sleep", "60", budget='"2s"')
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        [note] = self.comments(n)
        self.assertIn("its budget of 0m02s ran out", note)
        self.assertIn(
            "No session summary: the agent was stopped before its harness reported the session.",
            note,
        )
        self.assertEqual(self.session_calls(), [])

    def test_a_refused_summary_leaves_the_hand_back_standing(self):
        self.ledger.refuse_sessions = "denied: urn:cap:ledger:session:acme is not granted"
        n, outcome = self.run_item("claude", "claude-result.json")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertEqual(self.ledger.items[n]["awaiting"], "dispatcher")
        self.assertIsNone(self.ledger.items[n]["claim"])
        self.assertEqual(len(self.session_calls()), 1)
        self.assertEqual(len(self.comments(n)), 1)  # nothing posted after the hand-back

    def test_another_agent_says_it_has_none(self):
        n, outcome = self.run_item(None, "claude-result.json")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        [comment] = self.comments(n)
        self.assertIn("No session summary: `python", comment)
        self.assertIn("is not claude or codex", comment)
        self.assertEqual(self.session_calls(), [])
        # with no harness, the agent's output goes to the log untouched, as before
        [log] = (self.rig.home / "work" / "lib").glob(f"item-{n}.log")
        self.assertIn('"total_cost_usd"', log.read_text())


if __name__ == "__main__":
    unittest.main()
