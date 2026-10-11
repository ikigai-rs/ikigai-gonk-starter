"""What the HTTP API adds to the rules: status codes, malformed requests, and the clock."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lending.domain import Library  # noqa: E402  (the path above)
from lending.http import make_server  # noqa: E402


class Api(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 1)
        self.server = make_server(Library(), lambda: self.today, port=0)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_a_creation_is_201_and_a_read_is_200(self):
        status, _ = self.call("POST", "/titles", {"isbn": "1", "title": "T", "author": "A"})
        self.assertEqual(status, 201)
        self.call("POST", "/copies", {"copy": "c", "isbn": "1"})
        status, body = self.call("GET", "/copies/c")
        self.assertEqual((status, body["status"]), (200, "available"))

    def test_unknown_is_404_and_a_rule_is_409(self):
        status, body = self.call("GET", "/copies/nope")
        self.assertEqual((status, body["error"]), (404, "unknown-copy"))
        self.call("POST", "/titles", {"isbn": "1", "title": "T", "author": "A"})
        status, body = self.call("POST", "/titles", {"isbn": "1", "title": "T", "author": "A"})
        self.assertEqual((status, body["error"]), (409, "duplicate-title"))

    def test_a_malformed_request_is_400_and_names_what_is_wrong(self):
        status, body = self.call("POST", "/titles", raw=b"{not json")
        self.assertEqual((status, body["error"]), (400, "bad-request"))
        status, body = self.call("POST", "/titles", {"isbn": "1"})
        self.assertEqual(status, 400)
        self.assertIn("title, author", body["message"])
        status, body = self.call("POST", "/members", {"member": "m", "name": "N", "age": "9"})
        self.assertEqual(status, 400)
        self.assertIn("takes no age", body["message"])

    def test_an_unknown_route_is_404(self):
        status, body = self.call("POST", "/loans/overdue")
        self.assertEqual((status, body["error"]), (404, "no-route"))
        status, body = self.call("GET", "/shelves")
        self.assertEqual((status, body["error"]), (404, "no-route"))

    def test_the_date_is_the_servers_clock_not_the_clients(self):
        self.call("POST", "/titles", {"isbn": "1", "title": "T", "author": "A"})
        self.call("POST", "/copies", {"copy": "c", "isbn": "1"})
        self.call("POST", "/members", {"member": "m", "name": "N"})
        self.today = date(2026, 12, 25)
        status, body = self.call("POST", "/loans", {"member": "m", "copy": "c"})
        self.assertEqual((status, body["lent"], body["due"]), (201, "2026-12-25", "2027-01-15"))


if __name__ == "__main__":
    unittest.main()
