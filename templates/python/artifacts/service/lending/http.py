"""The library's HTTP JSON API: a routing table over `lending.domain`, and nothing else.

    POST /titles                 {"isbn", "title", "author"}   catalog a title
    POST /copies                 {"copy", "isbn"}              add a copy of it
    POST /members                {"member", "name"}            register a member
    GET  /copies/{copy}                                        a copy, and its loan if out
    POST /loans                  {"member", "copy"}            lend a copy
    POST /loans/{copy}/renewal                                 renew its loan
    POST /loans/{copy}/return                                  return it
    GET  /members/{member}/loans                               a member's loans
    GET  /loans/overdue                                        every overdue loan

A refusal is `{"error": <code>, "message": <text>}` with 404 for something unknown, 400 for
a malformed request and 409 for a rule. The DATE of every request is the server's clock,
never the client's: the clock is an argument of `make_server`, so a test hands it a fixed or
settable one and `serve.py --today` pins it for a demonstration.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .domain import Library, Refused, apply

# (method, path pattern, operation, whether the operation's arguments come from the body)
ROUTES = [
    ("POST", r"/titles", "add_title", True),
    ("POST", r"/copies", "add_copy", True),
    ("POST", r"/members", "add_member", True),
    ("GET", r"/copies/(?P<copy>[^/]+)", "copy", False),
    ("POST", r"/loans", "lend", True),
    ("GET", r"/loans/overdue", "overdue", False),
    ("POST", r"/loans/(?P<copy>[^/]+)/renewal", "renew", False),
    ("POST", r"/loans/(?P<copy>[^/]+)/return", "return", False),
    ("GET", r"/members/(?P<member>[^/]+)/loans", "loans", False),
]
CREATES = {"add_title", "add_copy", "add_member", "lend"}
NOT_FOUND = {"unknown-title", "unknown-copy", "unknown-member", "no-route"}
MAX_BODY = 64 * 1024


def status_for(code: str) -> int:
    if code in NOT_FOUND:
        return 404
    if code == "bad-request":
        return 400
    return 409


class Handler(BaseHTTPRequestHandler):
    server: LendingServer
    # Headers and body go out as two writes; with Nagle's algorithm on, the second waits for
    # the client's delayed ACK, which costs ~40 ms a request on loopback.
    disable_nagle_algorithm = True

    def do_GET(self) -> None:  # noqa: N802 (the name http.server calls)
        self.dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        try:
            for verb, pattern, op, from_body in ROUTES:
                m = re.fullmatch(pattern, path)
                if verb != method or not m:
                    continue
                args = dict(m.groupdict())
                if from_body:
                    args.update(self.body())
                today = self.server.clock()
                with self.server.lock:
                    result = apply(self.server.library, op, args, today)
                return self.reply(201 if op in CREATES else 200, result)
            raise Refused("no-route", f"no route {method} {path}")
        except Refused as refused:
            self.reply(
                status_for(refused.code), {"error": refused.code, "message": refused.message}
            )

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise Refused("bad-request", f"a body is at most {MAX_BODY} bytes")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as e:
            raise Refused("bad-request", f"the body is not JSON: {e}") from e
        if not isinstance(value, dict):
            raise Refused("bad-request", "the body is a JSON object")
        return value

    def reply(self, status: int, value: dict) -> None:
        data = json.dumps(value, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (http.server's name)
        if self.server.verbose:
            super().log_message(format, *args)


class LendingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, library: Library, clock: Callable[[], date], verbose: bool):
        super().__init__(address, Handler)
        self.library = library
        self.clock = clock
        self.verbose = verbose
        self.lock = threading.Lock()


def make_server(
    library: Library,
    clock: Callable[[], date],
    host: str = "127.0.0.1",
    port: int = 8080,
    verbose: bool = False,
) -> LendingServer:
    """A server for `library`, dating every request with `clock()`. Port 0 picks a free one."""
    return LendingServer((host, port), library, clock, verbose)
