#!/usr/bin/env python3
"""Seed the workshop fixture into an empty ledger. Requires a facilitator's grant."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import gonk

FIXTURE = Path(__file__).resolve().parent.parent / "workshop" / "ledger.json"


def seed(cfg, ikigai, fixture, repo_name=None):
    """Refuse nonempty ledgers; stop at the first failure, leaving a disposable ledger."""

    def run(command, content=None):
        answer = ikigai.run(command, stdin=content)
        if not answer.ok:
            raise RuntimeError(f"{command}: {answer.text() or f'exit {answer.code}'}")
        return answer.out

    try:
        if repo_name is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo_name):
            raise RuntimeError("invalid repository name for --repo-name")
        data = json.loads(run(f"source {cfg.resource('items')} limit=0 as=application/json"))
        if data["items"]:
            raise RuntimeError("ledger is not empty; use a new ledger for another run")
        refs = {}
        for index, item in enumerate(fixture["items"], 1):
            labels = item["labels"] + ([f"repo:{repo_name}"] if repo_name else [])
            command = (
                f"sink {cfg.resource('append')} priority={item['priority']} "
                f"labels={','.join(labels)} key=urn:workshop:item:{item['id']}"
            )
            answer = run(command, item["title"] + "\n\n" + item["body"])
            match = gonk.APPENDED.search(answer)
            if not match or match.group(3) or int(match.group(1).rsplit("#", 1)[1]) != index:
                raise RuntimeError(f"unexpected append response for {item['id']}: {answer}")
            refs[item["id"]] = (index, match.group(2))
        for source, target in fixture["links"]:
            run(f"sink {cfg.resource('link')} item={refs[source][1]} type=blocks", refs[target][1])
        for index, comment in enumerate(fixture["comments"], 1):
            flag = " handback=true" if comment.get("handback") else ""
            run(
                f"sink {cfg.resource('comment')} item={refs[comment['item']][0]}"
                f" key=urn:workshop:comment:{index}{flag}",
                comment["text"],
            )
        done = refs[fixture["closed"]][0]
        run(
            f"sink {cfg.resource('close')} item={done} reason=done "
            'evidence="https://github.com/ikigai-rs/ikigai-gonk-starter/pull/1"'
        )
        stalled = refs[fixture["expired"]][0]
        run(f"sink {cfg.resource('claim')} item={stalled} lease=1s", "workshop-stalled")
        time.sleep(2)
        print(f"workshop seed: filed {len(refs)} items; the stalled lease has elapsed")
        return 0
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(
            f"workshop seed: {exc}. If the run wrote anything, use a new ledger; "
            "do not retry this one.",
            file=sys.stderr,
        )
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", default=".", help="repository configured for the disposable ledger"
    )
    parser.add_argument(
        "--repo-name", help="worker repository name to label each item for dispatch"
    )
    args = parser.parse_args()
    try:
        cfg = gonk.load(Path(args.repo).resolve())
        gonk.need(cfg, "ledger", "target")
        return seed(cfg, gonk.Ikigai(cfg.target), json.loads(FIXTURE.read_text()), args.repo_name)
    except (gonk.ConfigError, OSError, ValueError) as exc:
        print(f"workshop seed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
