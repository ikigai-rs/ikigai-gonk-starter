"""Support for the worker's tests: an in-memory ledger that answers the commands the worker
sends (as gonk's ledger does, for the parts the worker uses), a scratch git origin, the stub gh
and the fake agent, and a worker wired to all of them.

The in-memory ledger is checked against the real one by tests/test_worker_gonk.py, which runs
the same loop against a scratch gonk where `ikigai-gonk` and `ikigai` are installed.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from support import ROOT, gonk, scratch_repo

sys.path.insert(0, str(ROOT / "tools"))
import worker  # noqa: E402

FAKES = ROOT / "tests" / "fakes"

# The principal every test worker trusts to route work (`routers` in Rig.configure), and the one
# FakeLedger.file dispatches as unless told otherwise. Not a real credential id.
DISPATCHER = "urn:iki:gonk:passkey:dispatcher-test"


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FakeLedger:
    """One ledger, in memory, answering `ikigai -c` command strings the way gonk does for
    next, items, item:N, claim (sink and delete), comment (with its `author`), label and
    close. Thread-safe: the heartbeat calls it from its own thread.

    It models a ledger 0.8.0 or later: `comment handback=true` sets the item's `awaiting` to
    `dispatcher`, `rejects=true` clears it, and the JSON item carries the field — the state,
    not just the `Evidence:` prefix in the newest comment. `older_comment` makes it a ledger
    from before 0.8.0: it does not know either argument, writes the comment and touches no
    state, and serves no `awaiting` field (which is exactly what the worker's fallback reads).
    """

    def __init__(self, name: str = "acme", older_comment: bool = False) -> None:
        self.name = name
        self.items: dict[int, dict] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.closes: list[int] = []
        self.lock = threading.Lock()
        self.refuse_renewals = False
        self.refuse_comments = 0  # refuse this many comments, then answer them again
        self.older_comment = older_comment  # a comment resource without handback/rejects
        self.tick = 0

    # -- the test's own hands on it
    def file(
        self, title: str, labels: list[str], body: str = "", dispatched_by: str | None = DISPATCHER
    ) -> int:
        """File an item and, unless `dispatched_by` is None, dispatch it as that principal: the
        dispatch comment `gonk.py dispatch` writes, naming every label it was filed with."""
        number = len(self.items) + 1
        self.items[number] = {
            "number": number,
            "display": f"{self.name}#{number}",
            "iri": f"urn:iki:ledger:{self.name}:item:{number}",
            "title": title,
            "body": body,
            "status": "open",
            "deferred": False,
            "labels": sorted(labels),
            "claim": None,
            "comments": [],
            "awaiting": None,
            "links": [],
        }
        if dispatched_by is not None:
            self.dispatch(number, dispatched_by)
        return number

    def dispatch(self, number: int, author: str = DISPATCHER, labels: list[str] | None = None):
        item = self.items[number]
        text = gonk.dispatch_text(labels or item["labels"], item["title"], item["body"])
        self.add_comment(number, text, author=author)

    def block(self, blocker: int, blocked: int) -> None:
        """`blocker` blocks `blocked`: the outbound link on the blocker, as the ledger keeps it."""
        target = self.items[blocked]
        self.items[blocker]["links"].append(
            {"type": "blocks", "target": {"number": blocked, "iri": target["iri"]}}
        )

    def open_blockers(self, number: int) -> list[int]:
        return [
            n
            for n, it in sorted(self.items.items())
            if it["status"] == "open"
            and any(
                link["type"] == "blocks" and link["target"]["number"] == number
                for link in it["links"]
            )
        ]

    def human_comments(self, number: int) -> list[dict]:
        """The item's comments less its dispatches: what the worker and others wrote."""
        return [c for c in self.items[number]["comments"] if gonk.parse_dispatch(c["text"]) is None]

    def force_claim(self, number: int, holder: str, expires: datetime | None = None) -> None:
        """What a takeover, or a dispatcher's release and re-claim, leaves behind."""
        with self.lock:
            exp = expires or datetime.now(UTC) + timedelta(minutes=30)
            self.items[number]["claim"] = {"holder": holder, "kind": "machine", "expires": iso(exp)}

    def add_comment(self, number: int, text: str, author: str = "fake") -> None:
        self.tick += 1
        stamp = iso(datetime.now(UTC) + timedelta(microseconds=self.tick * 1000))
        self.items[number]["comments"].append({"author": author, "time": stamp, "text": text})

    def item_json(self, number: int) -> dict:
        """The item as the ledger serves it: without `awaiting` on a pre-0.8.0 ledger."""
        item = self.items[number]
        if not self.older_comment:
            return dict(item)
        return {k: v for k, v in item.items() if k != "awaiting"}

    # -- the factory gonk.Ikigai is replaced by
    def __call__(self, target):
        return self

    def locate(self):
        return "/fake/bin/ikigai", "ikigai 0.0.0"

    def run(self, command: str, stdin: str | None = None) -> gonk.Answer:
        with self.lock:
            self.calls.append((command, stdin))
            return self.answer(command, stdin or "")

    def answer(self, command: str, stdin: str) -> gonk.Answer:
        verb, resource, *words = command.split()
        args = dict(w.split("=", 1) for w in words)
        prefix = f"urn:gk:iki:ledger:{self.name}:"
        if not resource.startswith(prefix):
            return gonk.Answer(1, "", f"no endpoint resolved for {resource}")
        name = resource[len(prefix) :]
        now = datetime.now(UTC)

        def has(item, labels, without):
            want = [w for w in labels.split(",") if w]
            avoid = [w for w in without.split(",") if w]
            return all(w in item["labels"] for w in want) and not any(
                w in item["labels"] for w in avoid
            )

        def served(item: dict) -> dict:
            """The item as the ledger serves it: a pre-0.8.0 ledger has no `awaiting` field."""
            if not self.older_comment:
                return item
            return {k: v for k, v in item.items() if k != "awaiting"}

        if verb == "source" and name == "next":
            ready, excluded = [], []
            for n in sorted(self.items):
                item = self.items[n]
                if item["status"] != "open" or not has(
                    item, args.get("labels", ""), args.get("without", "")
                ):
                    continue
                if item["claim"]:
                    excluded.append(
                        {"why": "claimed", "holder": item["claim"]["holder"], "item": served(item)}
                    )
                elif not self.older_comment and item["awaiting"] == "dispatcher":
                    # A ledger 0.8.0 excludes a handed-back item: it waits for its dispatcher.
                    excluded.append(
                        {
                            "why": "awaiting-dispatcher",
                            "reason": "handed back, awaiting the dispatcher",
                            "item": served(item),
                        }
                    )
                elif self.open_blockers(n):
                    # Blocked is a fact about the whole ledger, whatever the filter (0.2.1).
                    by = self.open_blockers(n)
                    excluded.append(
                        {
                            "why": "blocked",
                            "reason": "blocked by " + ", ".join(f"#{b}" for b in by),
                            "blocked_by": by,
                            "item": served(item),
                        }
                    )
                else:
                    ready.append(item)
            limit = int(args.get("limit", 3))
            ranking = [
                {"rank": i + 1, "item": served(it)}
                for i, it in enumerate(ready if limit == 0 else ready[:limit])
            ]
            doc = {"schema": 1, "ledger": self.name, "ready": len(ready), "ranking": ranking}
            return gonk.Answer(0, json.dumps({**doc, "excluded": excluded}), "")
        if verb == "source" and name == "items":
            rows = [
                served(it)
                for _, it in sorted(self.items.items())
                if it["status"] == "open" and has(it, args.get("labels", ""), "")
            ]
            return gonk.Answer(0, json.dumps({"schema": 1, "items": rows, "total": len(rows)}), "")
        if verb == "source" and name.startswith("item:"):
            item = self.items.get(int(name[5:]))
            if item is None:
                return gonk.Answer(1, "", f"no item {name[5:]}")
            return gonk.Answer(0, json.dumps({"schema": 1, "item": served(item)}), "")

        if verb == "sink" and name.startswith("item:"):
            item = self.items.get(int(name[5:]))
            if item is None:
                return gonk.Answer(1, "", "no such item")
            item["priority"] = int(args["priority"])
            return gonk.Answer(0, "updated priority\n", "")

        item = self.items.get(int(args.get("item", 0)))
        if item is None:
            return gonk.Answer(1, "", "no such item")
        display = item["display"]
        if verb == "sink" and name == "claim":
            holder = stdin.strip()
            held = item["claim"]
            if held and held["holder"] != holder:
                if gonk.parse_instant(held["expires"]) <= now:
                    return gonk.Answer(
                        1, "", f"{display}: lease ran out; takeover=true from={held['holder']}"
                    )
                return gonk.Answer(
                    1, "", f"conflict: {display} is already claimed by {held['holder']}"
                )
            if self.refuse_renewals and held:
                return gonk.Answer(1, "", f"conflict: {display} is already claimed by someone")
            lease = worker.seconds(args.get("lease", "30m"), "lease")
            expires = iso(now + timedelta(seconds=lease))
            item["claim"] = {"holder": holder, "kind": "machine", "expires": expires}
            return gonk.Answer(0, f"claimed by {holder} until {expires}\n", "")
        if verb == "delete" and name == "claim":
            # No note, whatever is piped: the cli gives a batch's stdin only to a content-less
            # SINK, never to a DELETE, so gonk's ledger never sees one (ledger item 1229). An
            # earlier fake recorded it as a comment, and hid exactly that defect.
            held = item["claim"]
            if "from" in args:
                if held is None:
                    return gonk.Answer(1, "", f"conflict: {display} is not claimed")
                if held["holder"] != args["from"]:
                    return gonk.Answer(
                        1,
                        "",
                        f"conflict: {display} is held by {held['holder']}, not {args['from']}",
                    )
            item["claim"] = None
            return gonk.Answer(0, f"{display} released\n", "")
        if verb == "sink" and name == "comment":
            if args.get("handback") and args.get("rejects"):
                return gonk.Answer(
                    1, "", "invalid argument `rejects`: `handback=true` and `rejects=true` together"
                )
            if self.refuse_comments:
                self.refuse_comments -= 1
                return gonk.Answer(1, "", f"error: the store refused the comment on {display}")
            self.add_comment(item["number"], stdin.strip(), author=args.get("author", "fake"))
            # The hand-back is the item's state (ledger 0.8.0, ledger item 1199), set or
            # cleared in the same write as the comment. A ledger older than 0.8.0 does not
            # know the argument: it writes the comment and touches no state (the `served`
            # view then has no `awaiting` field at all, and the prefix is the hand-back).
            if not self.older_comment:
                if args.get("handback"):
                    item["awaiting"] = "dispatcher"
                elif args.get("rejects"):
                    item["awaiting"] = None
            return gonk.Answer(0, f"commented on {display}\n", "")
        if verb == "sink" and name == "label":
            label = stdin.strip()
            if label not in item["labels"]:
                item["labels"] = sorted([*item["labels"], label])
            return gonk.Answer(0, f"labeled {display} {label}\n", "")
        if verb == "sink" and name == "close":
            self.closes.append(item["number"])
            item["status"] = "closed"
            return gonk.Answer(0, f"closed {display}\n", "")
        return gonk.Answer(1, "", f"the fake ledger does not answer {command!r}")


def git(*argv: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def scratch_origin(tmp: Path) -> Path:
    """A bare repository with one commit on main: the remote a worker clones."""
    origin = tmp / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp / "seed"
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("a library\n")
    git("add", "README.md", cwd=seed)
    git("commit", "-q", "-m", "first", cwd=seed)
    git("push", "-q", str(origin), "main", cwd=seed)
    return origin


class Rig:
    """A worker's checkout, its home, a scratch origin, the stub gh and the fake agent."""

    def __init__(self, test, ledger_name: str = "acme", target: str | None = None) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gonk-worker-")).resolve()
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.origin = scratch_origin(self.tmp)
        self.checkout = scratch_repo(test)
        self.record = self.tmp / "record"
        self.gh_state = self.tmp / "gh.json"
        self.gh_state.write_text(json.dumps({"slug": "acme/lib"}))
        (self.checkout / ".gonk" / "gonk.toml").write_text(f'ledger = "{ledger_name}"\n')
        sock = target or str(self.tmp / "g.sock")
        (self.checkout / ".gonk" / "local.toml").write_text(
            f'transport = "socket"\nsocket = "{sock}"\nholder = "w1"\n'
        )
        self.agent_flags: list[str] = []

    @property
    def gh_argv(self) -> list[str]:
        return [sys.executable, str(FAKES / "gh.py"), "--state", str(self.gh_state)]

    def configure(self, file: str = "worker.toml", **overrides) -> Path:
        """Write `.gonk/<file>`: worker.toml, or one loop's worker-<agent>.toml."""
        settings = {
            "labels": '["worker:w1"]',
            "work_root": '"~/work"',
            "lease": '"30m"',
            "renew_every": '"1s"',
            "poll": '"1s"',
            "budget": '"60s"',
            "ci_wait": '"0s"',
            "grace": '"2s"',
            "routers": json.dumps([DISPATCHER]),
        }
        settings.update({k: v for k, v in overrides.items() if v is not None})
        command = [
            sys.executable,
            str(FAKES / "fake_agent.py"),
            "--record",
            str(self.record),
            "--gh",
            shlex.join(self.gh_argv),
            "--branch",
            "{branch}",
            "--item",
            "{item}",
            "--report-file",
            "{report_file}",
            *self.agent_flags,
            "--",
            "{prompt}",
        ]
        lines = [f"{k} = {v}" for k, v in settings.items()]
        lines.append(f"gh = {json.dumps(self.gh_argv)}")
        lines += ["", "[agent]", f"command = {json.dumps(command)}"]
        lines += ["", "[repos.lib]", f'remote = "{self.origin}"', 'github = "acme/lib"']
        lines += ["", "[repos.other]", f'remote = "{self.origin}"', 'github = "acme/other"']
        path = self.checkout / ".gonk" / file
        path.write_text("\n".join(lines) + "\n")
        return path

    def worker(self, ikigai, config: Path | None = None) -> worker.Worker:
        cfg = gonk.load(self.checkout)
        wc = worker.load_worker(cfg, config)
        return worker.Worker(cfg, wc, ikigai, home=self.home)

    def gh(self) -> dict:
        return json.loads(self.gh_state.read_text())

    def prompt(self, number: int) -> str:
        return (self.record / f"prompt-{number}.md").read_text()

    def branches(self) -> list[str]:
        out = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=self.origin)
        return out.split()
