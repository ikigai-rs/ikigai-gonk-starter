#!/usr/bin/env python3
"""Take one named workshop task, then report a result using the layer client."""

import argparse
import json
import sys
from pathlib import Path

from shared_tasks import PREFIX, TASKS

RESULTS = "urn:layer:workshop-results:say"


def take_and_report(door, subject, result, conflict_errors):
    if not subject.startswith(PREFIX) or not all(
        c.isascii() and (c.isalnum() or c == "-") for c in subject[len(PREFIX) :]
    ):
        raise ValueError("invalid workshop task subject")
    try:
        receipt = door.delete(TASKS, tuple=subject, **{"as": "application/ld+json"})
    except conflict_errors as exc:
        print(f"Task is gone: {exc}")
        return False
    print(f"Take response (including receipt): {receipt.data}")
    turtle = (
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        f"<urn:layer:new> a ik:WorkshopResult ; ik:offer <{subject}> ; "
        f"ik:body {json.dumps(result)} .\n"
    )
    door.sink(RESULTS, turtle, **{"as": "application/json"})
    print(f"Reported result for {subject}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject", help="subject from the pending task list")
    parser.add_argument("result", help="what you did")
    parser.add_argument("--door", required=True)
    parser.add_argument("--cert-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        import ikigai
        from ikigai_layers_client.door import connect

        with connect(args.door, args.cert_dir) as door:
            return (
                0
                if take_and_report(
                    door, args.subject, args.result, (ikigai.ConflictError, ikigai.NotFoundError)
                )
                else 3
            )
    except Exception as exc:
        print(f"Take or report failed: {exc}. Check the receipt before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
