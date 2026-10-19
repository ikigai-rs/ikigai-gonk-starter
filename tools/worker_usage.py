"""An agent's usage against its plan's weekly limit, read between items and reported.

Standard library only, beside tools/worker.py. The worker loop calls `UsageWatch.check()` before it
takes an item (so between items), and at most once an hour (`every`). It runs the usage command
the worker's configuration names for its agent (`[usage] command`), which prints ONE JSON object
on standard output:

    {"plan": "Max 5x weekly", "used": 4200, "limit": 5000, "percent": 84,
     "resets_at": "2026-10-20T09:00:00Z"}

`plan` and `resets_at` are optional; so is one of `percent` and (`used`, `limit`), which are
worked out from the other. No agent CLI documents a non-interactive usage output this
worker can rely on, so there is no default command and nothing is scraped from a UI: with no
`[usage] command`, or a command that fails or prints something else, the reading is `unknown`,
and an `unknown` reading never stops the loop (the worker cannot tell it from a free week).

The report is one line, which is also one comment on the standing per-worker status item
(`[usage] status_item`), for the hub's hourly summary to read:

    Usage: agent=claude plan="Max 5x weekly" used=4200 limit=5000 percent=84
    resets=2026-10-20T09:00:00Z read=2026-10-17T10:00:00Z   (one line)

Only those fields: whatever else the command printed (a token, an account id) is dropped, and a
value that is not a number, or a plan or time that is not plain words, is dropped too.

Above `threshold` percent (default 85) the loop takes no NEW item for that agent until a later
reading is at or under it, or the reset time has passed, and says so.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_THRESHOLD = 85.0
DEFAULT_EVERY = 3600
PREFIX = "Usage:"

PLAN = re.compile(r"^[A-Za-z0-9 ._+/-]{1,60}$")
TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")


@dataclass
class Reading:
    agent: str
    read_at: str
    plan: str | None = None
    used: float | None = None
    limit: float | None = None
    percent: float | None = None
    resets_at: str | None = None

    @property
    def known(self) -> bool:
        return self.percent is not None

    def line(self) -> str:
        def num(v: float) -> str:
            return str(int(v)) if float(v).is_integer() else f"{v:.1f}"

        parts = [f"agent={self.agent}"]
        if not self.known:
            return f"{PREFIX} agent={self.agent} unknown read={self.read_at}"
        if self.plan:
            parts.append(f'plan="{self.plan}"')
        if self.used is not None:
            parts.append(f"used={num(self.used)}")
        if self.limit is not None:
            parts.append(f"limit={num(self.limit)}")
        parts.append(f"percent={num(self.percent)}")
        if self.resets_at:
            parts.append(f"resets={self.resets_at}")
        parts.append(f"read={self.read_at}")
        return f"{PREFIX} " + " ".join(parts)


def number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 and value == value and value != float("inf") else None


def parse(agent: str, text: str, read_at: str) -> Reading:
    """The reading in a usage command's output; `unknown` when there is none to trust."""
    try:
        doc = json.loads(text)
    except ValueError:
        return Reading(agent, read_at)
    if not isinstance(doc, dict):
        return Reading(agent, read_at)
    used, limit, percent = (
        number(doc.get("used")),
        number(doc.get("limit")),
        number(doc.get("percent")),
    )
    if percent is None and used is not None and limit:
        percent = round(100 * used / limit, 1)
    if percent is None:
        return Reading(agent, read_at)
    plan, resets = doc.get("plan"), doc.get("resets_at")
    return Reading(
        agent,
        read_at,
        plan=plan if isinstance(plan, str) and PLAN.match(plan) else None,
        used=used,
        limit=limit,
        percent=percent,
        resets_at=resets if isinstance(resets, str) and TIME.match(resets) else None,
    )


def past(stamp: str | None, now: datetime) -> bool:
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) <= now
    except ValueError:
        return False


class UsageWatch:
    """Reads the usage at most every `every` seconds and answers whether to hold new items."""

    def __init__(self, agent, command, threshold, every, status_item, ledger, log, run=None):
        self.agent, self.command, self.threshold = agent, command, threshold
        self.every, self.status_item, self.ledger, self.log = every, status_item, ledger, log
        self.run = run or self.run_command
        self.last: float | None = None
        self.reading: Reading | None = None
        self.holding = False

    @staticmethod
    def run_command(argv: list[str]) -> str | None:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def check(self) -> str | None:
        """Read and report when an hour has passed; the reason to take no new item, or None."""
        if not self.command:
            return None
        if self.last is None or time.monotonic() - self.last >= self.every:
            self.last = time.monotonic()
            self.read()
        return self.hold_reason()

    def read(self) -> None:
        now = datetime.now(UTC)
        read_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        out = self.run(self.command)
        self.reading = (
            parse(self.agent, out, read_at) if out is not None else Reading(self.agent, read_at)
        )
        line = self.reading.line()
        self.log(line)
        if self.status_item is not None:
            posted = self.ledger.comment(self.status_item, line)
            if not posted.ok:
                self.log(f"could not post the usage on item {self.status_item}: {posted.text()}")

    def hold_reason(self) -> str | None:
        r = self.reading
        over = (
            r is not None
            and r.known
            and r.percent > self.threshold
            and not past(r.resets_at, datetime.now(UTC))
        )
        if over:
            why = (
                f"{self.agent} is at {r.percent:g}% of its weekly limit, over {self.threshold:g}%: "
                "taking no new item for it"
            )
            if not self.holding:
                self.log(why)
            self.holding = True
            return why
        if self.holding:
            self.log(f"{self.agent} is back under {self.threshold:g}%: taking items again")
        self.holding = False
        return None
