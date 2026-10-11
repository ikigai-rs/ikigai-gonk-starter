"""The spec's scenarios, run as tests: every entry of openspec/vectors.json, twice.

Once against the rules directly (`lending.domain`) and once over HTTP against a real server
on a free port, whose clock the test moves to each step's date. The vector file is the
language-neutral half: another language's target runs the same file, so no target can say
something different from the spec without a test failing.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from lending.domain import Library, Refused, apply  # noqa: E402  (the path above)
from lending.http import make_server  # noqa: E402

VECTORS = json.loads((REPO / "openspec" / "vectors.json").read_text(encoding="utf-8"))


def steps_of(scenario: dict) -> list[dict]:
    """The fixtures it names, then its own steps."""
    given = [s for name in scenario.get("given", []) for s in VECTORS["fixtures"][name]]
    return given + scenario["steps"]


class Direct:
    """Runs a step against the rules, in process."""

    def __init__(self) -> None:
        self.library = Library()

    def run(self, op: str, args: dict, today: date) -> tuple[str | None, dict | None]:
        try:
            return None, apply(self.library, op, args, today)
        except Refused as refused:
            return refused.code, None


# How each operation is asked over HTTP: (method, path, which arguments go in the body).
def http_request(op: str, args: dict) -> tuple[str, str, dict | None]:
    if op == "add_title":
        return "POST", "/titles", args
    if op == "add_copy":
        return "POST", "/copies", args
    if op == "add_member":
        return "POST", "/members", args
    if op == "copy":
        return "GET", f"/copies/{args['copy']}", None
    if op == "lend":
        return "POST", "/loans", args
    if op == "renew":
        return "POST", f"/loans/{args['copy']}/renewal", None
    if op == "return":
        return "POST", f"/loans/{args['copy']}/return", None
    if op == "loans":
        return "GET", f"/members/{args['member']}/loans", None
    if op == "overdue":
        return "GET", "/loans/overdue", None
    raise AssertionError(f"no HTTP form for {op}")


class OverHttp:
    """Runs a step against a real server, after setting its clock to the step's date."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.today = date.fromisoformat(VECTORS["today"])
        self.server = make_server(Library(), lambda: self.today, port=0)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def run(self, op: str, args: dict, today: date) -> tuple[str | None, dict | None]:
        self.today = today
        method, path, body = http_request(op, args)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return None, json.loads(response.read())
        except urllib.error.HTTPError as e:
            with e:
                return json.loads(e.read())["error"], None


class Vectors(unittest.TestCase):
    def check(self, make_runner) -> None:
        for scenario in VECTORS["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                runner = make_runner()
                today = date.fromisoformat(scenario.get("today", VECTORS["today"]))
                for n, step in enumerate(steps_of(scenario)):
                    if "on" in step:
                        today = date.fromisoformat(step["on"])
                    where = f"step {n} ({step['op']} {step['args']} on {today})"
                    refused, answer = runner.run(step["op"], step["args"], today)
                    if "refused" in step:
                        self.assertEqual(refused, step["refused"], where)
                        continue
                    self.assertIsNone(refused, where)
                    if "expect" in step:
                        self.assertEqual(answer, step["expect"], where)

    def test_every_scenario_against_the_rules(self):
        self.check(Direct)

    def test_every_scenario_over_http(self):
        self.check(lambda: OverHttp(self))


if __name__ == "__main__":
    unittest.main()
