#!/usr/bin/env python3
"""The worker loop: take a ledger item, run an agent on it in a fresh worktree, hand it back.

    python3 tools/worker.py run --once      one item, then exit (what a person and the tests use)
    python3 tools/worker.py run             a loop: poll, take, run, hand back, until the stop file
    python3 tools/worker.py plan            what it would take now, and why it skips the rest
    python3 tools/worker.py forget 12       let this worker take item 12 again

Standard library only (Python 3.11 or later), beside tools/gonk.py, whose transport and
configuration it shares: `.gonk/gonk.toml` names the ledger, `.gonk/local.toml` says how this
machine reaches gonk (a socket, or QUIC with this machine's identity) and the holder name, and
`.gonk/worker.toml` (git-ignored, this machine's) says which items this worker takes, which
repositories it may work in, and which agent it runs. See worker/README.md.

What it does with one item, in order:

1. Pull `next`, narrowed to the configured labels, and take the first ranked item that is
   unclaimed, carries exactly one `repo:<name>` label naming a configured repository, is not
   awaiting its dispatcher (`awaiting_dispatcher`), was not handed back by this worker
   before, and whose repository has no ACTIVE claim held by anyone (one agent per repository)
   and no item running on this machine.
2. Claim it with a lease, then renew the lease while it works (the heartbeat). A renewal that
   finds the item held by someone else, released, or closed, or that the ledger refuses, means
   the claim is lost: the agent is stopped and the loss is reported, and nothing is released.
3. A fresh worktree branched from `origin/<base>` under the work root (default `~/work`, which
   must be under this account's own home), `<work root>/<repo>/item-<N>`.
4. The agent, from the configured command template, with the item as its brief behind a fixed
   preamble; stopped when the wall-clock budget runs out.
5. The hand-back: the pull request(s) the run opened, by the worktree's branch, and their CI
   (`gh pr checks`, or the Actions runs for the head's FULL sha when a fine-grained token may
   not read checks), posted on the item as an `Evidence:` comment that also says
   `handback=true`; then the claim is released. `handback=true` makes the hand-back a STATE
   the ledger knows (0.8.0, ledger item 1199), so `next` stops offering the item to the next
   worker; the `Evidence:` prefix stays, because that is what a ledger older than 0.8.0 —
   which does not know the argument and ignores it — reads, and what the brief filters on.
   The worker NEVER closes and NEVER merges: the dispatcher does both.

Each decision above that enforces a rule (a skip, a lost claim, a budget, a hand-back without
evidence, a merge while the agent ran, a refused configuration) is a sensor of the harness and
is recorded in this checkout's firing log (tools/harness.py), best effort: recording never
changes what the worker does. A skip is recorded once per item per process, not at every poll.

The one-agent-per-repository rule here is BEST-EFFORT until the ledger enforces it (ledger
item 1140 on ikigai's own ledger): two workers can read "free" at the same moment. Each
re-reads after claiming and backs off if it sees another claim, which narrows the window and
does not close it.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gonk  # noqa: E402  (beside this file; the path above makes it importable)

# --------------------------------------------------------------------------- durations

DURATION = re.compile(r"^(\d+)([smhd])$")
XSD_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def seconds(text: str, what: str) -> int:
    """`90s`, `30m`, `2h`, `1d` or an xsd:duration (`PT30M`), in seconds."""
    m = DURATION.match(text)
    if m:
        return int(m.group(1)) * UNIT[m.group(2)]
    m = XSD_DURATION.match(text)
    if m and text not in ("P", "PT"):
        d, h, mi, s = (int(g or 0) for g in m.groups())
        return d * 86400 + h * 3600 + mi * 60 + s
    raise gonk.ConfigError(f"{what} = {text!r}: `90s`, `30m`, `2h`, `1d` or an xsd:duration")


def spoken(total: float) -> str:
    total = int(total)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


# --------------------------------------------------------------------------- config

# The agents a worker can run, as command templates. Each is the documented non-interactive
# form of that agent's command line. Hermes and Codex have each run an item end to end on a
# Linux worker: hermes as its preset, Codex as `codex exec --sandbox danger-full-access
# {prompt}`, since it commits, pushes and opens the pull request, which its default sandbox
# refuses (the agent's own Unix account is the boundary). Claude Code is untested, so try it
# with `run --once` on an item of no consequence first. The placeholders are listed in TOKENS.
# An unattended agent also needs its own permission settings (Claude Code's allowlist or
# permission mode, Codex's sandbox and approval policy); the worker does not choose them for
# you.
PRESETS: dict[str, list[str]] = {
    "claude": ["claude", "-p", "{prompt}"],
    "codex": ["codex", "exec", "{prompt}"],
    "hermes": ["hermes", "-z", "{prompt}"],
    "hermes-profile": ["hermes", "--profile", "{profile}", "-z", "{prompt}"],
}

TOKENS = (
    "{prompt}",  # the whole brief: the preamble, then the item
    "{prompt_file}",  # the same text, in a file
    "{workdir}",  # the item's worktree (also the agent's working directory)
    "{branch}",  # the branch the worktree is on
    "{item}",  # the item's number
    "{repo}",  # the repository's name, as in its `repo:` label
    "{report_file}",  # where the agent writes evidence when it opens no pull request
    "{profile}",  # [agent] profile, for an agent with profiles (hermes)
)

REPO_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
GITHUB_SLUG = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
GITHUB_REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")
LABEL_SHAPE = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass
class Repo:
    name: str
    remote: str  # what `git clone` takes
    github: str  # owner/name, for gh
    base: str = "main"


@dataclass
class WorkerConfig:
    labels: list[str]
    without: list[str]
    work_root: str
    lease: str
    renew_every: int
    poll: int
    budget: int
    ci_wait: int
    ci_poll: int
    grace: int
    keep: bool
    gh: list[str]
    command: list[str]
    profile: str | None
    repos: dict[str, Repo]
    stop_file: Path
    state_dir: Path


WORKER_TOML = Path(".gonk") / "worker.toml"


def load_worker(cfg: gonk.Config, path: Path | None = None) -> WorkerConfig:
    path = path or cfg.repo / WORKER_TOML
    if not path.is_file():
        raise gonk.ConfigError(
            f"no {path}: a worker needs one (which items it takes, which repositories it works "
            "in, which agent it runs). Copy worker/worker.sample.toml there and edit it"
        )
    t = gonk.read_toml(path)
    where = str(path)

    def words(key: str) -> list[str]:
        value = t.get(key, [])
        if isinstance(value, str):
            value = [v for v in value.split(",") if v]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise gonk.ConfigError(f"{where}: {key} is a list of label words")
        for v in value:
            if not LABEL_SHAPE.match(v):
                raise gonk.ConfigError(f"{where}: {key} has {v!r}, which is not a label word")
        return value

    labels, without = words("labels"), words("without")
    if not labels:
        raise gonk.refuse(
            "worker-needs-labels",
            cfg.repo,
            f"{where}: labels is empty. Name at least one (`worker:<name>`, `repo:<name>`): a "
            "worker that takes every ready item in a ledger takes work nobody meant for it",
        )
    lease = str(t.get("lease", gonk.DEFAULT_LEASE))
    if not gonk.LEASE_SHAPE.match(lease):
        raise gonk.ConfigError(f"{where}: lease = {lease!r}: `30m`, `2h` or `PT30M`")
    lease_s = seconds(lease, "lease")
    renew = seconds(str(t["renew_every"]), "renew_every") if "renew_every" in t else lease_s // 3
    if renew < 1 or renew >= lease_s:
        raise gonk.ConfigError(
            f"{where}: renew_every must be at least 1s and shorter than the lease ({lease}), "
            "or the lease runs out between renewals"
        )

    agent = t.get("agent", {})
    if "command" in agent and "preset" in agent:
        raise gonk.ConfigError(f"{where}: [agent] has a command OR a preset, not both")
    if "preset" in agent:
        if agent["preset"] not in PRESETS:
            raise gonk.ConfigError(
                f"{where}: [agent] preset = {agent['preset']!r}; one of {', '.join(PRESETS)}"
            )
        command = list(PRESETS[agent["preset"]])
    else:
        command = agent.get("command")
        if isinstance(command, str):
            command = shlex.split(command)
    if not command or not all(isinstance(c, str) for c in command):
        raise gonk.ConfigError(
            f'{where}: [agent] needs `preset = "claude"` (or codex, hermes, hermes-profile) '
            'or `command = ["…", "{prompt}"]`'
        )
    if not any("{prompt}" in c or "{prompt_file}" in c for c in command):
        raise gonk.ConfigError(
            f"{where}: the agent's command names neither {{prompt}} nor {{prompt_file}}, so "
            "the agent would never see the item"
        )
    profile = agent.get("profile")
    if any("{profile}" in c for c in command) and not profile:
        raise gonk.ConfigError(
            f"{where}: the agent's command names {{profile}}: set [agent] profile"
        )

    gh = t.get("gh", "gh")
    gh = shlex.split(gh) if isinstance(gh, str) else list(gh)

    repos = {}
    for name, r in (t.get("repos") or {}).items():
        if not REPO_SHAPE.match(name) or name in (".", ".."):
            raise gonk.ConfigError(f"{where}: [repos.{name}] is not a repository name")
        remote = r.get("remote")
        if not remote:
            raise gonk.ConfigError(f'{where}: [repos.{name}] needs remote = "<git URL>"')
        slug = r.get("github")
        if not slug:
            m = GITHUB_REMOTE.search(remote)
            if not m:
                raise gonk.ConfigError(
                    f'{where}: [repos.{name}] needs github = "owner/name": its remote is not a '
                    "github.com URL to read it from"
                )
            slug = m.group(1)
        if not GITHUB_SLUG.match(slug):
            raise gonk.ConfigError(f"{where}: [repos.{name}] github = {slug!r} is not owner/name")
        repos[name] = Repo(name, remote, slug, r.get("base", "main"))
    if not repos:
        raise gonk.ConfigError(
            f"{where}: no [repos.<name>]: name each repository this worker may work in, as its "
            "items' `repo:<name>` label names it"
        )

    state_dir = cfg.repo / ".gonk" / "state" / "worker"
    stop = t.get("stop_file")
    return WorkerConfig(
        labels=labels,
        without=without,
        work_root=str(t.get("work_root", "~/work")),
        lease=lease,
        renew_every=renew,
        poll=seconds(str(t.get("poll", "60s")), "poll"),
        budget=seconds(str(t.get("budget", "45m")), "budget"),
        ci_wait=seconds(str(t.get("ci_wait", "10m")), "ci_wait"),
        ci_poll=seconds(str(t.get("ci_poll", "20s")), "ci_poll"),
        grace=seconds(str(t.get("grace", "10s")), "grace"),
        keep=bool(t.get("keep", False)),
        gh=gh,
        command=command,
        profile=profile,
        repos=repos,
        stop_file=Path(gonk.expand(stop, cfg.repo)) if stop else state_dir / "stop",
        state_dir=state_dir,
    )


def work_root(text: str, home: Path, repo: Path | None = None) -> Path:
    """The work root, refused unless it is strictly under this account's own home: the worker
    runs as an account of its own, and never works in a person's checkouts. A refusal is
    recorded in `repo`'s firing log when one is given."""
    home = home.resolve()
    if text == "~" or text.startswith("~/"):
        root = home / text[2:]
    elif text.startswith("~"):
        raise gonk.refuse(
            "worker-work-root-in-home",
            repo,
            f"work_root = {text!r}: another account's home is never a work root",
        )
    else:
        root = Path(text)
    if not root.is_absolute():
        raise gonk.refuse(
            "worker-work-root-in-home",
            repo,
            f"work_root = {text!r} is relative; write it as ~/<dir>",
        )
    root = root.resolve()
    if root == home or home not in root.parents:
        raise gonk.refuse(
            "worker-work-root-in-home",
            repo,
            f"work_root {root} is not under this account's home ({home}): a worker works only "
            "in its own home, never in a person's checkouts or a shared directory",
        )
    return root


# --------------------------------------------------------------------------- the ledger


def log(message: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} worker: {message}", flush=True)


class Ledger:
    """The ledger resources the worker uses, over gonk.py's transport."""

    def __init__(self, cfg: gonk.Config, ikigai) -> None:
        self.cfg = cfg
        self.ikigai = ikigai

    def _json(self, command: str) -> tuple[dict | None, str | None]:
        answer = self.ikigai.run(command)
        if not answer.ok:
            return None, answer.text() or f"exit {answer.code}"
        try:
            return json.loads(answer.out), None
        except ValueError as e:
            return None, f"the JSON face did not parse: {e}"

    def next(self, labels: list[str], without: list[str], limit: int = 25):
        words = [f"source {self.cfg.resource('next')}", f"labels={','.join(labels)}"]
        if without:
            words.append(f"without={','.join(without)}")
        words += [f"limit={limit}", "as=application/json"]
        return self._json(" ".join(words))

    def items(self, label: str):
        command = f"source {self.cfg.resource('items')} labels={label} limit=0 as=application/json"
        doc, err = self._json(command)
        return (doc.get("items", []) if doc else None), err

    def item(self, number: int):
        doc, err = self._json(f"source {self.cfg.resource(f'item:{number}')} as=application/json")
        return (doc.get("item") if doc else None), err

    def claim(self, number: int, holder: str, lease: str) -> gonk.Answer:
        command = f"sink {self.cfg.resource('claim')} item={number} lease={lease}"
        return self.ikigai.run(command, stdin=holder)

    def release(self, number: int, holder: str) -> gonk.Answer:
        """Release the claim only if `holder` still holds it: `from=` makes the DELETE a
        compare-and-set, refused with nothing released when the claim changed hands. It
        carries no note. The cli gives a batch's stdin only to a content-less SINK, never to a
        DELETE, so a note piped here never reached the item (ledger item 1229): post a note
        with `comment` first."""
        command = f"delete {self.cfg.resource('claim')} item={number} from={holder}"
        return self.ikigai.run(command, stdin="")

    def comment(self, number: int, text: str, **flags: str) -> gonk.Answer:
        """A comment on an item, with any flags the ledger's `comment` resource takes.

        `handback=true` makes the hand-back a STATE of the item (ledger 0.8.0, ledger item
        1199): `next` stops offering it to the next worker, and only the dispatcher's close or
        comment (or the worker's own `rejects=true`) takes it off. A ledger older than 0.8.0
        does not know the argument and ignores it, writing the comment — the `Evidence:` prefix
        in the text is the hand-back there, which is why it stays.
        """
        words = [f"sink {self.cfg.resource('comment')} item={number}"]
        words += [f"{name}={value}" for name, value in flags.items()]
        return self.ikigai.run(" ".join(words), stdin=text)


def active_claim(item: dict, now: datetime) -> str | None:
    """The holder of a claim whose lease still runs (or that has no lease), else None. An
    expired lease is still a claim to the ledger; to the one-agent-per-repository rule it is a
    holder that stopped renewing, so it does not fence the repository."""
    claim = item.get("claim") or {}
    holder = claim.get("holder")
    if not holder:
        return None
    expires = claim.get("expires")
    if expires and gonk.parse_instant(expires) <= now:
        return None
    return holder


def awaiting_dispatcher(item: dict) -> bool:
    """The item waits for its dispatcher to check the evidence and close it.

    The ledger's own state first: an item the worker handed back with `handback=true` carries
    `awaiting: dispatcher` in its JSON (ledger 0.8.0, ledger item 1199), which no later
    comment of the worker's clears and only the dispatcher can. A ledger older than 0.8.0 has
    no such field — the key is absent, not null — so the prefix in the newest comment is the
    fallback: an `Evidence:` hand-back, and any later comment (the dispatcher saying why the
    evidence did not hold) makes the item ready again. `awaiting` is `null` on a 0.8.0 ledger
    whose state was cleared, which the prefix must NOT override: the dispatcher has spoken.
    """
    if "awaiting" in item:
        return item.get("awaiting") == "dispatcher"
    comments = sorted(item.get("comments") or [], key=lambda c: c.get("time") or "")
    return bool(comments) and (comments[-1].get("text") or "").startswith("Evidence:")


def repo_labels(item: dict) -> list[str]:
    return [label[5:] for label in item.get("labels") or [] if label.startswith("repo:")]


# --------------------------------------------------------------------------- local state


class Attempts:
    """The items this worker has handed back, so it never takes one twice. `forget` clears one.
    A file, not memory, so a restarted worker remembers."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "attempts.json"

    def read(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as e:
            raise gonk.ConfigError(f"{self.path}: {e}") from e

    def seen(self, ledger: str, number: int) -> dict | None:
        return self.read().get(f"{ledger}#{number}")

    def record(self, ledger: str, number: int, outcome: str) -> None:
        table = self.read()
        table[f"{ledger}#{number}"] = {
            "outcome": outcome,
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def forget(self, ledger: str, number: int) -> bool:
        table = self.read()
        if table.pop(f"{ledger}#{number}", None) is None:
            return False
        self.path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True


class RepoLock:
    """One item per repository on this machine: an exclusive lock on a file per repository,
    released when the process ends however it ends."""

    def __init__(self, state_dir: Path, repo: str) -> None:
        self.path = state_dir / "locks" / f"{repo}.lock"
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


# --------------------------------------------------------------------------- the heartbeat


class Heartbeat(threading.Thread):
    """Renews the claim every `every` seconds while the item is worked. Each beat first reads
    the item, because a claim's DELETE releases whoever holds it and a CLAIM by the same holder
    of a free item succeeds: a renewal alone would silently re-take an item the dispatcher had
    released. A definite answer that the claim is gone (another holder, no holder, closed, or
    the renewal refused) sets `lost`. A transport failure is retried at the next beat."""

    def __init__(self, ledger: Ledger, number: int, holder: str, lease: str, every: int):
        super().__init__(daemon=True)
        self.ledger, self.number, self.holder = ledger, number, holder
        self.lease, self.every = lease, every
        self.halt = threading.Event()
        self.lost = threading.Event()
        self.why = ""
        self.renewals = 0
        self.failures = 0

    def run(self) -> None:
        while not self.halt.wait(self.every):
            if not self.beat():
                self.lost.set()
                return

    def beat(self) -> bool:
        item, err = self.ledger.item(self.number)
        if item is None:
            self.failures += 1
            log(f"heartbeat: could not read item {self.number} ({err}); trying again next beat")
            return True
        held = (item.get("claim") or {}).get("holder")
        display = item.get("display", self.number)
        if item.get("status") != "open":
            self.why = f"item {display} is {item.get('status')}"
            return False
        if held != self.holder:
            who = f"held by `{held}`" if held else "released (held by nobody)"
            self.why = f"item {display} is {who}, not `{self.holder}`"
            return False
        answer = self.ledger.claim(self.number, self.holder, self.lease)
        if answer.ok:
            self.renewals += 1
            return True
        text = answer.text()
        if "claimed by" in text or "takeover" in text or "conflict" in text.lower():
            self.why = f"the renewal was refused: {text}"
            return False
        self.failures += 1
        log(f"heartbeat: renewal of item {display} did not answer ({text}); trying again")
        return True

    def stop(self) -> None:
        self.halt.set()
        if self.is_alive():
            self.join()


# --------------------------------------------------------------------------- git and gh


def git(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True)


class StepFailed(Exception):
    """A step after the claim failed; the item is released with this as the note."""


def must(done: subprocess.CompletedProcess, what: str) -> str:
    if done.returncode != 0:
        raise StepFailed(f"{what} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


@dataclass
class Workspace:
    path: Path
    source: Path
    branch: str
    log: Path
    prompt: Path
    report: Path


def make_workspace(root: Path, repo: Repo, number: int) -> Workspace:
    """A fresh worktree of `origin/<base>` at `<root>/<repo>/item-<N>`, from one clone per
    repository (`.source`, fetched before each item). A directory left by an earlier run is
    never reused or removed: the next free `item-<N>.<k>` is taken instead."""
    home = root / repo.name
    home.mkdir(parents=True, exist_ok=True)
    source = home / ".source"
    if not (source / ".git").exists():
        must(git("clone", "--no-checkout", "--quiet", repo.remote, str(source)), "git clone")
    else:
        url = git("remote", "get-url", "origin", cwd=source).stdout.strip()
        if url != repo.remote:
            raise StepFailed(
                f"{source} is a clone of {url}, and the configuration says {repo.remote}: "
                "move it aside, and the next item clones afresh"
            )
    must(git("fetch", "--prune", "--quiet", "origin", cwd=source), "git fetch")
    name, k = f"item-{number}", 1
    while (home / name).exists():
        k += 1
        name = f"item-{number}.{k}"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    branch = f"worker/{name}-{stamp}"  # the directory's name: two runs in one second differ
    path = home / name
    must(
        git(
            "worktree", "add", "--quiet", "-b", branch, str(path), f"origin/{repo.base}", cwd=source
        ),
        "git worktree add",
    )
    return Workspace(
        path=path,
        source=source,
        branch=branch,
        log=home / f"{name}.log",
        prompt=home / f"{name}.prompt.md",
        report=home / f"{name}.report",
    )


def remove_workspace(ws: Workspace) -> None:
    git("worktree", "remove", "--force", str(ws.path), cwd=ws.source)
    git("branch", "-D", ws.branch, cwd=ws.source)
    for leftover in (ws.prompt, ws.report):
        leftover.unlink(missing_ok=True)


@dataclass
class PullRequest:
    number: int
    url: str
    state: str
    sha: str
    ci: str = "unknown"  # green, red, pending, none, unknown
    ci_source: str = ""
    ci_detail: str = ""
    merged_at: str = ""  # GitHub's mergedAt, ISO 8601; empty when not merged
    merged_by: str = ""  # the merger's login, which cannot tell an agent from its dispatcher
    # when both use one GitHub identity: when the merge happened can (ledger item 1232)


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GREEN = {"success", "skipped", "neutral"}


class GitHub:
    def __init__(self, gh: list[str], slug: str) -> None:
        self.gh, self.slug = gh, slug

    def run(self, *argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [*self.gh, *argv], cwd=cwd, capture_output=True, text=True, timeout=120
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(argv, 127, "", f"`{self.gh[0]}` is not on PATH")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", "gh did not answer within 120s")

    def pull_requests(self, branch: str) -> tuple[list[PullRequest], str | None]:
        done = self.run(
            "pr", "list", "-R", self.slug, "--head", branch, "--state", "all",
            "--json", "number,url,state,headRefOid,mergedAt,mergedBy",
        )  # fmt: skip
        if done.returncode != 0:
            return [], (done.stderr or done.stdout).strip() or f"gh exit {done.returncode}"
        try:
            rows = json.loads(done.stdout or "[]")
        except ValueError as e:
            return [], f"gh pr list did not answer JSON: {e}"
        prs = [
            PullRequest(
                r["number"],
                r["url"],
                r["state"],
                r.get("headRefOid", ""),
                merged_at=r.get("mergedAt") or "",
                merged_by=(r.get("mergedBy") or {}).get("login") or "",
            )
            for r in rows
        ]
        return sorted(prs, key=lambda p: p.number), None

    def read_ci(self, pr: PullRequest) -> None:
        """The PR's CI, from its checks, or from the Actions runs for its head when the token
        may not read checks (a fine-grained token: GitHub offers no Checks permission for one).
        Says which source answered."""
        done = self.run("pr", "checks", str(pr.number), "-R", self.slug, "--json", "name,bucket")
        rows = None
        try:
            rows = json.loads(done.stdout) if done.stdout.strip() else None
        except ValueError:
            rows = None
        if isinstance(rows, list):
            buckets = [r.get("bucket") for r in rows]
            pr.ci_source = "gh pr checks"
            pr.ci_detail = ", ".join(f"{r.get('name')}: {r.get('bucket')}" for r in rows)
            if not buckets:
                pr.ci = "none"
            elif any(b == "pending" for b in buckets):
                pr.ci = "pending"
            elif all(b in ("pass", "skipping") for b in buckets):
                pr.ci = "green"
            else:
                pr.ci = "red"
            return
        refused = (done.stderr or done.stdout).strip().splitlines()
        why = refused[0] if refused else f"exit {done.returncode}"
        if not FULL_SHA.match(pr.sha):
            # `gh run list --commit` with a short sha answers [] silently, which reads as "no
            # runs"; refuse to ask rather than report a false "none".
            pr.ci, pr.ci_source = "unknown", "nothing"
            pr.ci_detail = (
                f"gh pr checks was refused ({why}) and the head sha {pr.sha!r} is not full"
            )
            return
        done = self.run(
            "run", "list", "-R", self.slug, "--commit", pr.sha,
            "--json", "name,status,conclusion,headSha",
        )  # fmt: skip
        try:
            runs = json.loads(done.stdout) if done.returncode == 0 else None
        except ValueError:
            runs = None
        if not isinstance(runs, list):
            pr.ci, pr.ci_source = "unknown", "nothing"
            pr.ci_detail = f"gh pr checks was refused ({why}) and gh run list failed too"
            return
        pr.ci_source = f"GitHub Actions runs for {pr.sha} (gh pr checks was refused: {why})"
        pr.ci_detail = ", ".join(
            f"{r.get('name')}: {r.get('conclusion') or r.get('status')}" for r in runs
        )
        if not runs:
            pr.ci = "none"
        elif any(r.get("status") != "completed" for r in runs):
            pr.ci = "pending"
        elif all(r.get("conclusion") in GREEN for r in runs):
            pr.ci = "green"
        else:
            pr.ci = "red"


# --------------------------------------------------------------------------- the brief

PREAMBLE = """\
You are an unattended worker. A gonk ledger item was claimed for you by the worker loop,
which renews its lease while you run and hands it back when you exit. These rules are fixed;
the item follows them. If this repository has an AGENTS.md (or CLAUDE.md), read it first:
its rules hold beside these.

1. You hold {display} through the worker. Do not claim, release, report or close it, and do
   not file new items: the worker and the dispatcher do those.
2. Reproduce before you fix. The item is a claim, not a task: a bug report, a review finding
   or a measurement someone else made may be wrong. Reproduce it first, preferably as a test
   that fails because of the defect. If it does not hold, change nothing and open no pull
   request: write the URL of your evidence on the FIRST line of {report_file}, and what you
   found on the lines after. Closing with no change is a good outcome.
3. Work only in this directory, {workdir}, on the branch {branch}, made for you from
   origin/{base}. Never read or write any other checkout.
4. Every change through a pull request with green CI: commit, push this branch
   (`git push -u origin {branch}`), open ONE pull request against {base} with `gh pr create`,
   and see its CI to green, fixing what fails. NEVER merge it, never enable auto-merge, and
   never close the item: the dispatcher does both after checking your evidence.
5. The pull request is your evidence. Say in its description what you reproduced, what you
   changed and how CI proves it. If you could not finish, say so there plainly.
6. You have {budget} of wall-clock time; at the end the worker stops you.

The item, {display} on the ledger `{ledger}`:
"""


# The worker's own hand-back comments, which the brief does not carry back to it
HAND_BACK_PREFIXES = ("Evidence:", "gonk worker")

# Bounds on the comments the brief carries: enough for a real correction, not a flood
MAX_COMMENTS_IN_BRIEF = 20
MAX_COMMENT_LINES = 40
MAX_COMMENT_TEXT = 2000
MAX_BRIEF_COMMENTS = 8000


def brief_comments(item: dict) -> str:
    """The item's comments, oldest first, as a marked block for the brief.

    The worker's own hand-back comments (`Evidence:` and `gonk worker`) are left out:
    they are what the worker said back, not what the dispatcher or others said. The
    block is bounded: a flood of comments must not push the item itself out of the
    prompt.
    """
    comments = sorted(item.get("comments") or [], key=lambda c: c.get("time") or "")
    kept, total = [], 0
    for comment in comments:
        text = (comment.get("text") or "").strip()
        if not text or text.startswith(HAND_BACK_PREFIXES):
            continue
        lines = text.splitlines()
        if len(lines) > MAX_COMMENT_LINES:
            text = "\n".join(lines[:MAX_COMMENT_LINES]) + "\n[...]"
        if len(text) > MAX_COMMENT_TEXT:
            text = text[:MAX_COMMENT_TEXT] + "[...]"
        if total + len(text) > MAX_BRIEF_COMMENTS:
            kept.append("[... further comments truncated ...]")
            break
        kept.append(text)
        total += len(text)
        if len(kept) >= MAX_COMMENTS_IN_BRIEF:
            kept.append("[... further comments truncated ...]")
            break
    if not kept:
        return ""
    return (
        "\n\nLater notes from the dispatcher and others (oldest first, "
        + f"{len(kept)} of {len(comments)} comments):\n\n"
        + "\n\n---\n\n".join(kept)
        + "\n"
    )


def brief(item: dict, ledger: str, ws: Workspace, repo: Repo, budget: int) -> str:
    values = {
        "{display}": str(item.get("display") or f"#{item.get('number')}"),
        "{report_file}": str(ws.report),
        "{workdir}": str(ws.path),
        "{branch}": ws.branch,
        "{base}": repo.base,
        "{budget}": spoken(budget),
        "{ledger}": ledger,
    }
    text = PREAMBLE
    for token, value in values.items():
        text = text.replace(token, value)
    title = (item.get("title") or "").strip()
    body = (item.get("body") or "").strip()
    comments = brief_comments(item)
    if body:
        return f"{text}\n# {title}\n\n{body}\n{comments}"
    return f"{text}\n# {title}\n\n{comments}" if comments else f"{text}\n# {title}\n"


def agent_argv(command: list[str], values: dict[str, str]) -> list[str]:
    out = []
    for word in command:
        for token, value in values.items():
            word = word.replace(token, value)
        out.append(word)
    return out


def stop_process(proc: subprocess.Popen, grace: int) -> None:
    """SIGTERM to the agent's whole process group, then SIGKILL after the grace period. The
    agent was started in a session of its own, so its children go with it, and the worker
    (in another group) is not signaled."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 30)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


# --------------------------------------------------------------------------- the worker

# Exit codes of `run --once`, and the outcome of one item.
HANDED_BACK, ERROR, CONFIG, NOTHING, NOT_HANDED_BACK = 0, 1, 2, 3, 4


@dataclass
class Outcome:
    code: int
    summary: str
    number: int | None = None
    prs: list[PullRequest] = field(default_factory=list)
    renewals: int = 0


class Worker:
    def __init__(self, cfg: gonk.Config, wc: WorkerConfig, ikigai, home: Path | None = None):
        gonk.need(cfg, "ledger", "target", "holder")
        self.cfg, self.wc = cfg, wc
        self.ledger = Ledger(cfg, ikigai)
        self.root = work_root(wc.work_root, home or Path.home(), cfg.repo)
        self.attempts = Attempts(wc.state_dir)
        self.shutdown = threading.Event()
        # The skips recorded as firings so far, (control, item): once each per process, since
        # the loop asks again at every poll and a skip that has not changed is not news.
        self.skips_fired: set[tuple[str, int]] = set()
        self.skipped: list[tuple[str, int]] = []

    # ----------------------------------------------------------------- choosing

    def candidates(self) -> tuple[list[tuple[dict, Repo]], list[tuple[str, str]], str | None]:
        """(takeable items in rank order with their repository, (item, why) for the rest, the
        error when gonk did not answer). Nothing is claimed."""
        doc, err = self.ledger.next(self.wc.labels, self.wc.without)
        if doc is None:
            return [], [], err
        now = gonk.iso_now()
        take, skip = [], []
        busy: dict[str, str | None] = {}
        self.skipped = []
        for ranked in doc.get("ranking", []):
            item = ranked["item"]
            display = item.get("display", f"#{item.get('number')}")
            reason = self.skip_reason(item, now, busy)
            if reason:
                skip.append((display, reason[1]))
                self.skipped.append((reason[0], item.get("number")))
            else:
                take.append((item, self.wc.repos[repo_labels(item)[0]]))
        return take, skip, None

    def why_not(self, item: dict, now: datetime, busy: dict) -> str | None:
        reason = self.skip_reason(item, now, busy)
        return reason[1] if reason else None

    def skip_reason(self, item: dict, now: datetime, busy: dict) -> tuple[str, str] | None:
        """(the harness sensor that skips it, why), or None when the item is takeable."""
        if item.get("claim"):
            return gonk.sensor("worker-skips-claimed"), (
                f"claimed by `{item['claim'].get('holder')}`"
            )
        repos = repo_labels(item)
        if len(repos) != 1:
            named = ", ".join(f"repo:{r}" for r in repos) or "none"
            return gonk.sensor("worker-one-repository-label"), (
                f"a worker works in exactly one repository, and its repo: labels are {named}"
            )
        repo = repos[0]
        if repo not in self.wc.repos:
            return gonk.sensor("worker-one-repository-label"), (
                f"repo:{repo} is not one of this worker's repositories"
            )
        if awaiting_dispatcher(item):
            return gonk.sensor("worker-skips-awaiting-dispatcher"), (
                "it is awaiting its dispatcher (handed back, awaiting review)"
            )
        seen = self.attempts.seen(self.cfg.ledger, item["number"])
        if seen:
            return gonk.sensor("worker-skips-handed-back"), (
                f"this worker handed it back already ({seen['outcome']}, {seen['at']}); "
                f"`worker.py forget {item['number']}` to allow it again"
            )
        if repo not in busy:
            busy[repo] = self.repo_holder(repo, now, exclude=None)
        if busy[repo]:
            return gonk.sensor("worker-one-agent-per-repository"), (
                f"repo:{repo} is busy: {busy[repo]} (one agent per repository)"
            )
        return None

    def fire_skip(self, control: str, number: int | None) -> None:
        """Record a skip as a firing of its sensor, once per item per process."""
        if (control, number) in self.skips_fired:
            return
        self.skips_fired.add((control, number))
        gonk.fire(control, "refusal", self.cfg.repo)

    def repo_holder(self, repo: str, now: datetime, exclude: int | None) -> str | None:
        """Who holds an ACTIVE claim on an open item of this repository, if anyone."""
        items, err = self.ledger.items(f"repo:{repo}")
        if items is None:
            return f"its claims could not be read ({err})"
        for other in items:
            if other.get("number") == exclude:
                continue
            holder = active_claim(other, now)
            if holder:
                return f"{other.get('display')} is claimed by `{holder}`"
        return None

    # ----------------------------------------------------------------- one item

    def run_once(self) -> Outcome:
        take, skip, err = self.candidates()
        if err:
            return Outcome(ERROR, f"gonk did not answer next: {err}")
        for display, why in skip:
            log(f"skip {display}: {why}")
        for control, number in self.skipped:
            self.fire_skip(control, number)
        for item, repo in take:
            lock = RepoLock(self.wc.state_dir, repo.name)
            if not lock.acquire():
                log(f"skip {item.get('display')}: repo:{repo.name} has an item running here")
                self.fire_skip(gonk.sensor("worker-one-agent-per-repository"), item["number"])
                continue
            try:
                return self.work(item, repo)
            finally:
                lock.release()
        return Outcome(NOTHING, "nothing to take")

    def work(self, item: dict, repo: Repo) -> Outcome:
        number, display, holder = item["number"], item.get("display"), self.cfg.holder
        answer = self.ledger.claim(number, holder, self.wc.lease)
        if not answer.ok:
            return Outcome(NOTHING, f"could not claim {display}: {answer.text()}", number)
        log(f"claimed {display} for `{holder}` on a {self.wc.lease} lease (repo:{repo.name})")
        other = self.repo_holder(repo.name, gonk.iso_now(), exclude=number)
        if other:
            self.fire_skip(gonk.sensor("worker-one-agent-per-repository"), number)
            self.ledger.release(number, holder)
            return Outcome(NOTHING, f"backed off {display}: repo:{repo.name} is busy: {other}")
        beat = Heartbeat(self.ledger, number, holder, self.wc.lease, self.wc.renew_every)
        beat.start()
        try:
            outcome = self.work_claimed(item, repo, beat)
        except Exception as e:  # noqa: BLE001  (any failure after the claim hands the item back)
            beat.stop()
            what = str(e) if isinstance(e, StepFailed) else f"{type(e).__name__}: {e}"
            note = f"gonk worker `{holder}` failed on {display} and hands it back: {what}"
            self.release_if_ours(number, note)
            outcome = Outcome(ERROR, note, number)
        finally:
            beat.stop()
        outcome.number, outcome.renewals = number, beat.renewals
        self.attempts.record(self.cfg.ledger, number, outcome_word(outcome, beat))
        log(outcome.summary)
        return outcome

    def work_claimed(self, item: dict, repo: Repo, beat: Heartbeat) -> Outcome:
        number, display, holder = item["number"], item.get("display"), self.cfg.holder
        ws = make_workspace(self.root, repo, number)
        text = brief(item, self.cfg.ledger, ws, repo, self.wc.budget)
        ws.prompt.write_text(text, encoding="utf-8")
        argv = agent_argv(
            self.wc.command,
            {
                "{prompt_file}": str(ws.prompt),
                "{prompt}": text,
                "{workdir}": str(ws.path),
                "{branch}": ws.branch,
                "{item}": str(number),
                "{repo}": repo.name,
                "{report_file}": str(ws.report),
                "{profile}": self.wc.profile or "",
            },
        )
        log(f"{display}: running `{argv[0]}` in {ws.path} on {ws.branch} (log {ws.log})")
        started = time.monotonic()
        with ws.log.open("w", encoding="utf-8") as out:
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=ws.path,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as e:
                raise StepFailed(f"the agent `{argv[0]}` did not start: {e}") from e
            ended, code = self.watch(proc, started, beat)
        exited = datetime.now(UTC)
        took = spoken(time.monotonic() - started)
        where = f"the worktree is kept at {ws.path} (branch {ws.branch}, log {ws.log})"
        gh = GitHub(self.wc.gh, repo.github)

        if ended == "lost":
            # Not ours any more: say so on the item, release NOTHING (it is someone else's,
            # or nobody's by the dispatcher's choice), keep the work.
            gonk.fire("worker-claim-lost", "refusal", self.cfg.repo)
            note = (
                f"gonk worker `{holder}` lost its claim on {display} after {took}: {beat.why}. "
                f"It stopped the agent; {where}."
            )
            self.ledger.comment(number, note)
            return Outcome(NOT_HANDED_BACK, note)

        prs, pr_err = gh.pull_requests(ws.branch)
        if ended == "agent":
            self.wait_for_ci(gh, prs, beat)
        else:
            for pr in prs:
                gh.read_ci(pr)
        if beat.lost.is_set():
            gonk.fire("worker-claim-lost", "refusal", self.cfg.repo)
            note = (
                f"gonk worker `{holder}` lost its claim on {display} while reading CI: "
                f"{beat.why}. {where}."
            )
            self.ledger.comment(number, note)
            return Outcome(NOT_HANDED_BACK, note, prs=prs)

        lines = []
        for pr in prs:
            lines.append(f"- {pr.url} ({pr.state.lower()}): CI {pr.ci}")
            lines.append(f"  from {pr.ci_source}" + (f": {pr.ci_detail}" if pr.ci_detail else ""))
        if pr_err:
            lines.append(f"- the pull requests could not be listed: {pr_err}")
        for pr in prs:
            if pr.state.upper() == "MERGED":
                lines.append(merge_line(pr, exited))
                if lines[-1].startswith("- WARNING"):
                    gonk.fire("worker-merged-before-exit", "warning", self.cfg.repo)
        agent = f"`{argv[0]}` exited {code} after {took}" if code is not None else ""

        if ended == "overrun":
            gonk.fire("worker-budget-overrun", "refusal", self.cfg.repo)
        if ended in ("overrun", "stopped"):
            why = (
                f"its budget of {spoken(self.wc.budget)} ran out"
                if ended == "overrun"
                else "the worker was stopped"
            )
            note = "\n".join(
                [
                    f"gonk worker `{holder}` stopped the agent on {display} after {took}: {why}. "
                    f"It hands the item back without evidence; {where}.",
                    *lines,
                ]
            )
            self.release_if_ours(number, note)
            return Outcome(NOT_HANDED_BACK, note.splitlines()[0], prs=prs)

        evidence = prs[0].url if prs else self.reported_evidence(ws)
        if not evidence:
            gonk.fire("worker-no-evidence", "refusal", self.cfg.repo)
            note = (
                f"gonk worker `{holder}` handed {display} back without evidence: {agent}, "
                f"opened no pull request on {ws.branch} and wrote no evidence to its report "
                f"file. {where}."
            )
            if pr_err:
                note += f" (The pull requests could not be listed: {pr_err})"
            self.release_if_ours(number, note)
            return Outcome(NOT_HANDED_BACK, note, prs=prs)

        removed = not self.wc.keep
        body = [
            f"Evidence: {evidence}",
            "",
            f"gonk worker `{holder}` handed this back: {agent}. It never closes and never "
            "merges; the dispatcher does both, after checking this evidence.",
        ]
        if prs:
            body += ["", *lines]
        else:
            body += ["", "No pull request: the agent reported the evidence above instead."]
            report = ws.report.read_text(encoding="utf-8").strip().splitlines()[1:]
            body += [line for line in report if line.strip()][:40]
        body += [
            "",
            f"Branch {ws.branch}; "
            + (f"worktree removed; log {ws.log}" if removed else where)
            + ".",
        ]
        posted = self.hand_back(number, "\n".join(body))
        if not posted.ok:
            note = (
                f"gonk worker `{holder}` could not post its evidence on {display} "
                f"({posted.text()}): it was {evidence}. {where}."
            )
            self.release_if_ours(number, note)
            return Outcome(NOT_HANDED_BACK, note, prs=prs)
        released = self.release_if_ours(number, "")
        if removed:
            remove_workspace(ws)
        state = "released" if released else "NOT released (it was no longer this worker's)"
        return Outcome(
            HANDED_BACK, f"handed {display} back with {evidence}; claim {state}", prs=prs
        )

    def watch(self, proc: subprocess.Popen, started: float, beat: Heartbeat):
        """Wait for the agent, stopping it on a lost claim, an overrun or a shutdown."""
        deadline = started + self.wc.budget
        while True:
            try:
                return "agent", proc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            for ended, why in (
                ("lost", beat.lost.is_set()),
                ("overrun", time.monotonic() >= deadline),
                ("stopped", self.shutdown.is_set()),
            ):
                if why:
                    stop_process(proc, self.wc.grace)
                    return ended, proc.returncode

    def wait_for_ci(self, gh: GitHub, prs: list[PullRequest], beat: Heartbeat) -> None:
        deadline = time.monotonic() + self.wc.ci_wait
        while True:
            for pr in prs:
                gh.read_ci(pr)
            settled = all(pr.ci in ("green", "red") for pr in prs)
            if settled or time.monotonic() >= deadline or beat.lost.is_set():
                return
            if self.shutdown.wait(min(self.wc.ci_poll, max(0.0, deadline - time.monotonic()))):
                return

    def reported_evidence(self, ws: Workspace) -> str | None:
        try:
            first = ws.report.read_text(encoding="utf-8").strip().splitlines()
        except FileNotFoundError:
            return None
        url = first[0].strip() if first else ""
        return url if gonk.EVIDENCE_SHAPE.match(url) else None

    def hand_back(self, number: int, text: str) -> gonk.Answer:
        """Post the hand-back comment, marked with `handback=true`.

        `handback=true` (ledger 0.8.0, ledger item 1199) is what tells `next` the item waits
        for its dispatcher rather than being free for the next worker. A ledger older than 0.8.0
        ignores the argument it does not know and writes the comment anyway: the `Evidence:`
        prefix in `text` is the hand-back there, and the worker's own `awaiting_dispatcher`
        reads it back.
        """
        return self.ledger.comment(number, text, handback="true")

    def release_if_ours(self, number: int, note: str) -> bool:
        """Post the note, then release only a claim that is still this worker's. The note is
        always a comment of its own, posted first, whoever holds the claim: one handed to the
        release never reached the item (ledger item 1229). The release is a compare-and-set on
        this worker's holder, since a claim's DELETE without `from=` releases whoever holds it."""
        if note:
            posted = self.ledger.comment(number, note)
            if not posted.ok:
                log(f"could not post the hand-back note on item {number}: {posted.text()}")
        item, _ = self.ledger.item(number)
        held = ((item or {}).get("claim") or {}).get("holder")
        if held != self.cfg.holder:
            return False
        return self.ledger.release(number, self.cfg.holder).ok

    # ----------------------------------------------------------------- the loop

    def loop(self) -> int:
        log(
            f"started: ledger `{self.cfg.ledger}` over {self.cfg.target.describe()}, holder "
            f"`{self.cfg.holder}`, labels {','.join(self.wc.labels)}, work root {self.root}; "
            f"stop with: touch {self.wc.stop_file}"
        )
        while not self.shutdown.is_set():
            if self.wc.stop_file.exists():
                log(f"stopping: {self.wc.stop_file} exists (remove it to run again)")
                return 0
            outcome = self.run_once()
            if outcome.code in (NOTHING, ERROR):
                if outcome.code == ERROR:
                    log(outcome.summary)
                self.shutdown.wait(self.wc.poll)
        log("stopped by a signal")
        return 0


def merge_line(pr: PullRequest, exited: datetime) -> str:
    """Who merged a pull request the run opened, as far as the worker can tell. The login cannot
    tell the agent from its dispatcher when both use one GitHub identity, so the time decides: a
    merge after the agent exited is not the agent's. One at or before the exit, or at a time
    GitHub did not give, is called out, though the dispatcher may have merged while the agent
    still ran (ledger item 1232 was that case: merged 30 s before the agent exited)."""
    by = f" by {pr.merged_by}" if pr.merged_by else ""
    try:
        at = gonk.parse_instant(pr.merged_at) if pr.merged_at else None
    except ValueError:
        at = None
    if at is not None and at > exited:
        return f"- {pr.url} was merged{by} at {pr.merged_at}, after the agent exited"
    when = f" at {pr.merged_at}" if pr.merged_at else ""
    return (
        f"- WARNING: merged{when}{by} before the agent exited: by the agent, against its "
        f"rules, unless someone else merged it while it ran: {pr.url}. Only the repository's "
        "branch protection can stop an agent merging; the worker cannot."
    )


def outcome_word(outcome: Outcome, beat: Heartbeat) -> str:
    if outcome.code == HANDED_BACK:
        return "handed back with evidence"
    if beat.lost.is_set():
        return "claim lost"
    return outcome.summary.split(":")[0][:120]


# --------------------------------------------------------------------------- commands


def build(args, make_ikigai, home: Path | None) -> Worker:
    repo = Path(args.repo).resolve() if args.repo else gonk.find_repo(Path.cwd())
    cfg = gonk.load(repo)
    wc = load_worker(cfg, Path(args.config).resolve() if args.config else None)
    if getattr(args, "labels", None):
        wc.labels = [w for w in args.labels.split(",") if w]
    if getattr(args, "keep", False):
        wc.keep = True
    gonk.need(cfg, "ledger", "target", "holder")
    return Worker(cfg, wc, make_ikigai(cfg.target), home=home)


def cmd_run(args, make_ikigai, home) -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            repo = Path(args.repo).resolve() if args.repo else gonk.find_repo(Path.cwd())
        except gonk.ConfigError:
            repo = None
        raise gonk.refuse(
            "worker-refuses-root",
            repo,
            "refusing to run as root: a worker runs as an account of its own",
        )
    worker = build(args, make_ikigai, home)
    if args.once:
        outcome = worker.run_once()
        if outcome.code == NOTHING:
            log(outcome.summary)
        return outcome.code

    def on_signal(signum, _frame):
        log(f"signal {signum}: stopping the current item, then exiting")
        worker.shutdown.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    return worker.loop()


def cmd_plan(args, make_ikigai, home) -> int:
    worker = build(args, make_ikigai, home)
    take, skip, err = worker.candidates()
    if err:
        print(f"gonk did not answer next: {err}", file=sys.stderr)
        return ERROR
    for item, repo in take:
        print(f"take  {item.get('display')}  repo:{repo.name}  {item.get('title')}")
    for display, why in skip:
        print(f"skip  {display}  {why}")
    if not take and not skip:
        print(f"nothing ready with labels {','.join(worker.wc.labels)}")
    return 0 if take else NOTHING


def cmd_forget(args, make_ikigai, home) -> int:
    worker = build(args, make_ikigai, home)
    if worker.attempts.forget(worker.cfg.ledger, args.item):
        print(f"forgot {worker.cfg.ledger}#{args.item}: this worker may take it again")
        return 0
    print(f"{worker.cfg.ledger}#{args.item} was not in {worker.attempts.path}")
    return 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="worker.py", description=__doc__.splitlines()[0])
    p.add_argument(
        "--repo", help="the worker's checkout (default: found from the working directory)"
    )
    p.add_argument("--config", help="the worker configuration (default: .gonk/worker.toml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("run", help="take items and work them: a loop, or --once")
    s.add_argument("--once", action="store_true", help="one item, then exit (3: nothing to take)")
    s.add_argument("--labels", help="override the configured labels (comma-separated)")
    s.add_argument("--keep", action="store_true", help="keep each worktree after its hand-back")
    s.set_defaults(run=cmd_run)

    s = sub.add_parser("plan", help="what it would take now, and why it skips the rest")
    s.add_argument("--labels", help="override the configured labels (comma-separated)")
    s.set_defaults(run=cmd_plan)

    s = sub.add_parser("forget", help="let this worker take an item it handed back before")
    s.add_argument("item", type=int)
    s.set_defaults(run=cmd_forget)
    return p


def main(argv: list[str] | None = None, make_ikigai=gonk.Ikigai, home: Path | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.run(args, make_ikigai, home)
    except gonk.ConfigError as e:
        print(f"worker.py: {e}", file=sys.stderr)
        return CONFIG


if __name__ == "__main__":
    sys.exit(main())
