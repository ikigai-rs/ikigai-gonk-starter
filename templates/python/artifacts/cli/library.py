#!/usr/bin/env python3
"""The library's command line: a client of the service's HTTP JSON API.

    python3 artifacts/cli/library.py [--service URL] <command> …

    add-title ISBN TITLE AUTHOR    catalog a title
    add-copy COPY ISBN             add a copy of it
    add-member MEMBER NAME         register a member
    copy COPY                      a copy, and its loan if it is out
    lend MEMBER COPY               lend a copy
    renew COPY                     renew its loan
    return COPY                    return it
    loans MEMBER                   a member's loans
    overdue                        every overdue loan

The service is http://127.0.0.1:8080 unless `--service` names another. The client never
sends a date: the service's clock dates every request. Exit status: 0 done, 1 refused by
the service (its code and message on stderr), 2 the service could not be reached or the
command line is wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SERVICE = "http://127.0.0.1:8080"
TIMEOUT_S = 10


class Unreachable(Exception):
    pass


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"error": f"http-{e.code}", "message": e.reason}
        except (urllib.error.URLError, OSError) as e:
            raise Unreachable(f"no answer from {self.base}: {e}") from e


def seg(value: str) -> str:
    """One path segment, so an id with a `/` or a space stays one segment."""
    return urllib.parse.quote(value, safe="")


def dollars(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def show_loan(loan: dict) -> str:
    renewals = f", renewed {loan['renewals']}x" if loan["renewals"] else ""
    return f"{loan['copy']} to {loan['member']}, due {loan['due']}{renewals}"


def run(args, client: Client, out) -> int:
    c = args.command
    if c == "add-title":
        status, body = client.call(
            "POST", "/titles", {"isbn": args.isbn, "title": args.title, "author": args.author}
        )
        ok = f"cataloged {body.get('isbn')}: {body.get('title')} by {body.get('author')}"
    elif c == "add-copy":
        status, body = client.call("POST", "/copies", {"copy": args.copy, "isbn": args.isbn})
        ok = f"added copy {body.get('copy')} of {body.get('isbn')}"
    elif c == "add-member":
        status, body = client.call("POST", "/members", {"member": args.member, "name": args.name})
        ok = f"registered {body.get('member')}: {body.get('name')}"
    elif c == "copy":
        status, body = client.call("GET", f"/copies/{seg(args.copy)}")
        loan = body.get("loan")
        ok = f"{body.get('copy')} ({body.get('isbn')}): " + (
            f"on loan, {show_loan(loan)}" if loan else "available"
        )
    elif c == "lend":
        status, body = client.call("POST", "/loans", {"member": args.member, "copy": args.copy})
        ok = f"lent {show_loan(body)}" if "due" in body else ""
    elif c == "renew":
        status, body = client.call("POST", f"/loans/{seg(args.copy)}/renewal")
        ok = f"renewed {show_loan(body)}" if "due" in body else ""
    elif c == "return":
        status, body = client.call("POST", f"/loans/{seg(args.copy)}/return")
        late = body.get("days_late", 0)
        owes = f", {late} day(s) late: owes {dollars(body.get('fee_cents', 0))}" if late else ""
        ok = f"returned {body.get('copy')} from {body.get('member')}{owes}"
    elif c == "loans":
        status, body = client.call("GET", f"/members/{seg(args.member)}/loans")
        rows = [
            f"{r['copy']:<8} due {r['due']}"
            + (f"  OVERDUE {r['days_late']} day(s)" if r["overdue"] else "")
            + f"  {r['title']}"
            for r in body.get("loans", [])
        ]
        ok = "\n".join(rows) if rows else f"{args.member} has no loans"
    elif c == "overdue":
        status, body = client.call("GET", "/loans/overdue")
        rows = [
            f"{r['copy']:<8} {r['member']:<10} due {r['due']}  {r['days_late']} day(s) late"
            for r in body.get("overdue", [])
        ]
        ok = "\n".join(rows) if rows else "nothing is overdue"
    else:  # argparse refuses anything else first
        raise AssertionError(c)
    if status >= 400:
        print(f"library: refused ({body.get('error')}): {body.get('message')}", file=sys.stderr)
        return 1
    print(ok, file=out)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="library.py", description=__doc__.splitlines()[0])
    p.add_argument("--service", default=DEFAULT_SERVICE, help=f"default {DEFAULT_SERVICE}")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("add-title", help="catalog a title")
    s.add_argument("isbn")
    s.add_argument("title")
    s.add_argument("author")
    s = sub.add_parser("add-copy", help="add a copy of a cataloged title")
    s.add_argument("copy")
    s.add_argument("isbn")
    s = sub.add_parser("add-member", help="register a member")
    s.add_argument("member")
    s.add_argument("name")
    s = sub.add_parser("copy", help="a copy, and its loan if it is out")
    s.add_argument("copy")
    s = sub.add_parser("lend", help="lend a copy to a member")
    s.add_argument("member")
    s.add_argument("copy")
    for name, about in [("renew", "renew a loan"), ("return", "return a copy")]:
        s = sub.add_parser(name, help=about)
        s.add_argument("copy")
    s = sub.add_parser("loans", help="a member's loans")
    s.add_argument("member")
    sub.add_parser("overdue", help="every overdue loan")
    return p


def main(argv: list[str] | None = None, out=None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args, Client(args.service), out or sys.stdout)
    except Unreachable as e:
        print(f"library: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
