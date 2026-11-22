#!/usr/bin/env python3
"""Seed or reset a disposable workshop layer using an enrolled facilitator identity."""

import argparse
import json
import sys
import uuid
from pathlib import Path

TASKS = "urn:layer:workshop-tasks:tuples"
PREFIX = "urn:layer:workshop-tasks:offer:"
FIXTURE = (
    (
        "Clarify the reference",
        "Identify the ambiguous reference and ask for clarification.",
        "collision",
    ),
    ("Trace a dependency", "Explain which dependency must be resolved first.", "chain-plan"),
    ("Compare two options", "Summarize the tradeoffs without making the decision.", "decision"),
    ("Check release evidence", "List what a person needs before publishing.", "human"),
    ("Verify a hand-back", "Check the evidence before accepting the claim.", "false-evidence"),
    ("Inspect an expired lease", "Explain what expiration means for the claim.", "stalled"),
    ("Coordinate shared work", "Describe how two agents can avoid a collision.", "contended"),
    ("Investigate a flaky test", "Suggest a repeatable way to collect evidence.", "flaky"),
)


def build_tasks(run_id):
    """Build eight Turtle tuples with distinct subjects for a new run."""
    if not run_id.isalnum():
        raise ValueError("run ID must contain only letters and digits")
    result = []
    for index, (title, body, item) in enumerate(FIXTURE, 1):
        subject = f"{PREFIX}{run_id}-{index}"
        turtle = (
            "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
            "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
            f"<{subject}> a ik:Offer ; dcterms:title {json.dumps(title)} ; "
            f"ik:brief {json.dumps(body)} ; ik:about <urn:workshop:item:{item}> .\n"
        )
        result.append((subject, turtle))
    return result


def seed(door, run_id):
    for subject, turtle in build_tasks(run_id):
        door.sink(TASKS, turtle, **{"as": "application/json"})
        print(f"Posted {subject}")


def reset(door):
    """Take all pending tuples, leaving immutable receipts and result entries intact."""
    while True:
        answer = door.source(TASKS, limit="100", **{"as": "application/ld+json"})
        nodes = json.loads(answer.data).get("@graph", [])
        subjects = [node["@id"] for node in nodes if isinstance(node, dict) and "@id" in node]
        if not subjects:
            return
        for subject in subjects:
            if not subject.startswith(PREFIX):
                raise ValueError(f"unexpected task {subject}; reset stopped")
            door.delete(TASKS, tuple=subject, **{"as": "application/ld+json"})
            print(f"Removed pending task {subject}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "reset"))
    parser.add_argument("--door", required=True, help="layer host QUIC URL")
    parser.add_argument("--cert-dir", required=True, type=Path, help="facilitator certificate set")
    args = parser.parse_args()
    try:
        from ikigai_layers_client.door import connect

        with connect(args.door, args.cert_dir) as door:
            if args.action == "seed":
                seed(door, uuid.uuid4().hex)
            else:
                reset(door)
        return 0
    except Exception as exc:
        print(f"Workshop {args.action} stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
