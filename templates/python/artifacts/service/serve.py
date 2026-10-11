#!/usr/bin/env python3
"""Run the library-lending service.

    python3 artifacts/service/serve.py [--host 127.0.0.1] [--port 8080] [--today 2026-10-01]

The state is in memory and goes when the process does. `--today` pins the server's clock to
one date, so a demonstration or a book chapter gives the same due dates on any day it runs;
without it the server dates each request with this machine's local date.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from lending.domain import Library
from lending.http import make_server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="serve.py", description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--today", type=date.fromisoformat, help="pin the clock to this date")
    p.add_argument("--verbose", action="store_true", help="log every request to stderr")
    args = p.parse_args(argv)
    clock = (lambda: args.today) if args.today else date.today
    server = make_server(Library(), clock, args.host, args.port, args.verbose)
    host, port = server.server_address[:2]
    pinned = f", clock pinned to {args.today}" if args.today else ""
    print(f"library service on http://{host}:{port}{pinned}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
