"""The command line against the real service, on a free port with a clock the test sets."""

from __future__ import annotations

import contextlib
import io
import socket
import sys
import threading
import unittest
from datetime import date
from pathlib import Path

CLI = Path(__file__).resolve().parents[1]
SERVICE = CLI.parent / "service"
sys.path.insert(0, str(CLI))
sys.path.insert(0, str(SERVICE))

import library  # noqa: E402  (the paths above)
from lending.domain import Library  # noqa: E402
from lending.http import make_server  # noqa: E402


class Cli(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 1)
        server = make_server(Library(), lambda: self.today, port=0)
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address[:2]
        self.service = f"http://{host}:{port}"

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = library.main(["--service", self.service, *argv], out=out)
        return code, out.getvalue().strip(), err.getvalue().strip()

    def shelf(self):
        for argv in [
            ("add-title", "9780141439518", "Pride and Prejudice", "Jane Austen"),
            ("add-copy", "pp-1", "9780141439518"),
            ("add-copy", "pp-2", "9780141439518"),
            ("add-member", "ada", "Ada Lovelace"),
            ("add-member", "grace", "Grace Hopper"),
        ]:
            code, _, err = self.cli(*argv)
            self.assertEqual(code, 0, err)

    def test_cataloging_answers_in_words(self):
        self.assertEqual(
            self.cli("add-title", "9780141439518", "Pride and Prejudice", "Jane Austen"),
            (0, "cataloged 9780141439518: Pride and Prejudice by Jane Austen", ""),
        )

    def test_a_loan_from_lending_to_a_late_return(self):
        self.shelf()
        self.assertEqual(self.cli("lend", "ada", "pp-1")[1], "lent pp-1 to ada, due 2026-10-22")
        self.assertEqual(
            self.cli("copy", "pp-1")[1],
            "pp-1 (9780141439518): on loan, pp-1 to ada, due 2026-10-22",
        )
        self.assertEqual(
            self.cli("renew", "pp-1")[1], "renewed pp-1 to ada, due 2026-11-12, renewed 1x"
        )
        self.today = date(2026, 11, 14)
        code, out, _ = self.cli("loans", "ada")
        self.assertEqual(out, "pp-1     due 2026-11-12  OVERDUE 2 day(s)  Pride and Prejudice")
        self.assertEqual(
            self.cli("overdue")[1], "pp-1     ada        due 2026-11-12  2 day(s) late"
        )
        self.assertEqual(
            self.cli("return", "pp-1")[1], "returned pp-1 from ada, 2 day(s) late: owes $0.50"
        )
        self.assertEqual(self.cli("overdue")[1], "nothing is overdue")
        self.assertEqual(self.cli("loans", "ada")[1], "ada has no loans")

    def test_a_refusal_is_exit_1_with_the_services_code(self):
        self.shelf()
        self.cli("lend", "ada", "pp-1")
        code, out, err = self.cli("lend", "grace", "pp-1")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("library: refused (copy-on-loan): copy pp-1 is out"), err)

    def test_no_service_is_exit_2(self):
        with socket.socket() as s:  # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = library.main(["--service", f"http://127.0.0.1:{port}", "overdue"], out=out)
        self.assertEqual(code, 2)
        self.assertIn("no answer from", err.getvalue())


if __name__ == "__main__":
    unittest.main()
