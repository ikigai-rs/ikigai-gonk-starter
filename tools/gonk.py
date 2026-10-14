#!/usr/bin/env python3
"""This repository's way to reach gonk: init, seed, file, next, claim, release, report, reject,
close, harness.

Standard library only (Python 3.11 or later, for tomllib). Every request goes through the
`ikigai` command line with an ALIAS mount, `--mount urn:gk:=<target>`, so a socket on this
machine and a QUIC door on another are one code path: only the target differs.

Configuration is files and flags, never environment variables:

  .gonk/gonk.toml     committed: which ledger this repository's work lives in
  .gonk/layout.toml   committed: where specs, artifacts, queries and scripts live
  .gonk/process.toml  committed: how the team works, the process profiles tools/new.py
                      generated it with; `init` and `status` report them
  .gonk/local.toml    per working tree, git-ignored: how this machine reaches gonk, and
                      the holder name this working tree claims under
  .gonk/state/        per working tree, git-ignored: the claim this tree holds, and the
                      harness firing log (tools/harness.py)
  .gonk/harness/      committed: this process's harness graph and its vocabulary

`init` writes the first and third from its flags and reports what is still missing.

Every refusal here that enforces a rule of AGENTS.md is a SENSOR of the harness: it names its
control (`refuse("evidence-required", …)`) and records one line in this working tree's firing
log, best effort (tools/harness.py). A refusal of a malformed value (a lease, a label, a ledger
name) is input checking, not a rule, and records nothing.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

try:
    import harness  # beside this file: the harness graph and the firing log
except ImportError:  # a tree from before tools/harness.py: nothing recorded, nothing else changes
    harness = None

# The alias every request is sent under: `urn:gk:iki:ledger:…` here is `urn:iki:ledger:…`
# on gonk. An alias rather than `--override urn:iki:ledger:=…` so that no name this tool
# sends can resolve against a binding of the LOCAL kernel by accident.
PREFIX = "urn:gk:"

# ikigai-ledger's own rules for a ledger name (src/ledger.rs, `Ledger::parse`), repeated
# here so that `init` refuses a name before anything is filed under it.
LEDGER_RESERVED = frozenset(
    "append claim close comment counter defer doctor graph item items label ledgers "
    "lifecycle link next policy purge reopen selection tombstone".split()
)
LEDGER_MAX = 64
LEDGER_SHAPE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")

# A holder is the name a claim is held under. `none` and `any` are the ledger's filter
# keywords, so it refuses them; whitespace would not survive the engine grammar.
HOLDER_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:+-]{0,127}$")
HOLDER_RESERVED = frozenset({"none", "any"})

# `30m`, `2h`, `1d`, `90s`, or an xsd:duration of days, hours, minutes and seconds.
LEASE_SHAPE = re.compile(r"^(?:\d+[smhd]|P(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?)$")
DEFAULT_LEASE = "30m"

ARTIFACT_SHAPE = LEDGER_SHAPE
EVIDENCE_SHAPE = re.compile(r"^https?://\S+$")
# ikigai-ledger's close reasons (src/vocabulary.rs, CLOSE_REASONS).
CLOSE_REASONS = ("done", "wontfix", "duplicate", "superseded", "audit-no-change")
TIMEOUT_S = 30


class ConfigError(Exception):
    """A configuration file is malformed, or a flag or value is refused."""


def fire(control: str, kind: str, repo: Path | None, **context) -> None:
    """Record one firing of a harness sensor in `repo`'s firing log. Never raises, never
    prints to standard output: the decision is the caller's, whether or not the line lands."""
    if harness is not None:
        harness.fire(control, kind, repo, **context)


def refuse(control: str, repo: Path | None, message: str, args=None) -> ConfigError:
    """A refusal that enforces a rule: recorded as a firing of `control`, then answered as the
    ConfigError to raise (`raise refuse(…)`), so the refusal reads the same either way."""
    fire(control, "refusal", repo, command=command_line(args))
    return ConfigError(message)


def command_line(args) -> str | None:
    """The command as typed, for the firing's digest (never logged as text)."""
    argv = getattr(args, "argv", None)
    return " ".join(["gonk.py", *argv]) if argv is not None else None


# --------------------------------------------------------------------------- config


def find_repo(start: Path) -> Path:
    """The nearest directory at or above `start` holding `.gonk/layout.toml`."""
    here = start.resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".gonk" / "layout.toml").is_file():
            return candidate
    raise ConfigError(
        f"no .gonk/layout.toml at or above {here}: run this inside a repository made from "
        "ikigai-gonk-starter, or pass --repo"
    )


def read_toml(path: Path) -> dict:
    """The table in `path`, or an empty one when the file does not exist."""
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


def toml_string(value: str) -> str:
    """A TOML basic string. JSON's escapes are a subset of TOML's for these characters."""
    return json.dumps(value, ensure_ascii=False)


def write_toml(path: Path, header: str, values: dict[str, str]) -> None:
    lines = [header.rstrip("\n"), ""] if header else []
    lines += [f"{k} = {toml_string(v)}" for k, v in values.items() if v is not None]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ledger_problem(name: str) -> str | None:
    """Why `name` cannot be a ledger, or None."""
    if not name:
        return "a ledger name is empty"
    if len(name) > LEDGER_MAX:
        return f"`{name}` is {len(name)} characters; a ledger name is at most {LEDGER_MAX}"
    if not LEDGER_SHAPE.match(name):
        return (
            f"`{name}` is not a ledger name: lowercase letters, digits, `-` and `_`, starting "
            "and ending with a letter or a digit (the name becomes an IRI segment and a "
            "capability token)"
        )
    if name in LEDGER_RESERVED:
        words = ", ".join(sorted(LEDGER_RESERVED))
        return f"`{name}` is reserved by the ledger's short forms: {words}"
    return None


def holder_problem(name: str) -> str | None:
    if name in HOLDER_RESERVED:
        return f"`{name}` is reserved: `holder=none` and `holder=any` are the ledger's filters"
    if not HOLDER_SHAPE.match(name):
        return (
            f"`{name}` is not a holder name: a letter or digit, then up to 127 of letters, "
            "digits and `._@:+-` (no spaces)"
        )
    return None


@dataclass
class Target:
    """How to reach gonk: its owner-only socket, or its QUIC door with a client bundle."""

    socket: str | None = None
    quic: str | None = None
    cert_dir: str | None = None

    def describe(self) -> str:
        if self.socket:
            return f"socket {self.socket}"
        return f"quic {self.quic} (certificates in {self.cert_dir})"

    def mount_args(self, prefix: str = PREFIX) -> list[str]:
        if self.socket:
            return ["--mount", f"{prefix}={self.socket}"]
        return ["--mount", f"{prefix}={self.quic}", "--cert-dir", str(self.cert_dir)]

    def mcp_command(self) -> str:
        """The command an MCP client is configured with: the ledger's own names, over
        `--override`, so every `urn:iki:ledger:*` goes to gonk and nowhere else."""
        if self.socket:
            return f"ikigai mcp --override urn:iki:ledger:={self.socket}"
        return f"ikigai mcp --override urn:iki:ledger:={self.quic} --cert-dir {self.cert_dir}"


@dataclass
class Config:
    repo: Path
    ledger: str | None
    target: Target | None
    holder: str | None
    layout: dict
    enrollment: list[str] = field(default_factory=list)

    @property
    def gonk_toml(self) -> Path:
        return self.repo / ".gonk" / "gonk.toml"

    @property
    def local_toml(self) -> Path:
        return self.repo / ".gonk" / "local.toml"

    @property
    def claim_file(self) -> Path:
        return self.repo / ".gonk" / "state" / "claim.toml"

    def resource(self, name: str) -> str:
        """The aliased IRI of one of this ledger's resources."""
        return f"{PREFIX}iki:ledger:{self.ledger}:{name}"


def expand(path: str, base: Path) -> str:
    """`~` and relative paths made absolute: `ikigai` reads a mount target as written."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = base / p
    return str(p.resolve())


# What gonk names a QUIC client, by every host's convention `urn:iki:host:{name}:client:<fp>`
# (ikigai-gonk PR 126, ledger item 1183). The older spelling is a permanent alias: gonk never
# mints it again and never rewrites it where it is stored (item authors, its access log), and
# treats both spellings of one fingerprint as one machine. This tool compares no principals (a
# claim is compared by its holder NAME, free text the ledger matches exactly), so the alias is
# only ever printed, as what older records say.
CLIENT_IRI_PREFIX = "urn:iki:host:gonk:client:"
LEGACY_CLIENT_IRI_PREFIX = "urn:iki:gonk:client:"
PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----\s*(.+?)\s*-----END CERTIFICATE-----", re.DOTALL)


def cert_fingerprint(pem: str) -> str:
    """The SHA-256 of a PEM's first certificate (its DER bytes), lowercase hex: the
    fingerprint gonk enrolls and names a QUIC client by (ikigai-quic `fingerprint_of_pem`)."""
    m = PEM_CERT.search(pem)
    if not m:
        raise ConfigError("not a PEM certificate: no BEGIN CERTIFICATE block")
    try:
        der = base64.b64decode("".join(m.group(1).split()), validate=True)
    except ValueError as e:
        raise ConfigError(f"the certificate's base64 does not decode: {e}") from e
    return hashlib.sha256(der).hexdigest()


def client_iri(fingerprint: str) -> str:
    """The name gonk gives the QUIC client with this certificate fingerprint."""
    return CLIENT_IRI_PREFIX + fingerprint


def legacy_client_iri(fingerprint: str) -> str:
    """The same client's older spelling, which records written before the change still carry."""
    return LEGACY_CLIENT_IRI_PREFIX + fingerprint


def worker_tokens(ledger: str) -> list[str]:
    """`ikigai-gonk client add <name> --worker <ledger>`: the per-verb worker grant of one
    ledger, EXACTLY as gonk computes it — `src/grants.rs`, `grants_for(ledger, Authority::Worker)`,
    whose doctest is the contract. In gonk's order: read, the ledger graph's store read, claim,
    comment, the ledger graph's store WRITE. Renew and release ride on the claim token, so they
    are not capabilities of their own; the store write is there because every ledger write goes
    to the store under the caller's capability, and without it a hand-enrolled worker could
    neither claim nor comment."""
    graph = f"urn:iki:ledger:graph:{ledger}"
    return [
        f"urn:cap:ledger:read:{ledger}",
        f"urn:cap:store:read:graph:{graph}",
        f"urn:cap:ledger:claim:{ledger}",
        f"urn:cap:ledger:comment:{ledger}",
        f"urn:cap:store:write:graph:{graph}",
    ]


def enrollment_lines(fingerprint: str, grant: str, ledger: str) -> list[str]:
    """What the gonk operator adds for this machine, by command or by hand. The `--worker` form
    is what an unattended machine is enrolled with; `--ledger <ledger>=write` stays the option
    for a person's machine, and the by-hand `grants.json` entry is the worker grant."""
    worker = json.dumps({grant: worker_tokens(ledger)}, indent=2).splitlines()
    return [
        "on the gonk machine, with this machine's client.crt copied there:",
        f"  ikigai-gonk client add {grant} --cert client.crt --worker {ledger}",
        "  (an unattended machine: the worker grant reads, claims, renews, comments and",
        "  releases, and cannot close, relabel or file)",
        f"  ikigai-gonk client add {grant} --cert client.crt --ledger {ledger}=write",
        "  (a person's machine: the writer grant also files, closes and relabels)",
        "or by hand, in the config home's gonk/ directory:",
        f'  clients.json  {{"clients": {{"{fingerprint}": {{"grant": "{grant}"}}}}}}',
        "  grants.json   " + worker[0],
        *("                " + line for line in worker[1:]),
        "then restart gonk: it reads the certificates it trusts at startup. Copy the server.crt",
        "from the bundle `client add` wrote over the one in this machine's certificate directory:",
        "the one `ikigai cert generate` wrote is a server identity of its own, not gonk's.",
    ]


def ledger_write_tokens(ledger: str) -> list[str]:
    """`ikigai-gonk grants <ledger> write`: the four-token writer of one ledger."""
    graph = f"urn:iki:ledger:graph:{ledger}"
    return [
        f"urn:cap:ledger:read:{ledger}",
        f"urn:cap:store:read:graph:{graph}",
        f"urn:cap:ledger:write:{ledger}",
        f"urn:cap:store:write:graph:{graph}",
    ]


def target_from(table: dict, where: str) -> Target | None:
    transport = table.get("transport")
    if transport is None:
        return None
    if transport == "socket":
        sock = table.get("socket")
        if not sock:
            raise ConfigError(f'{where}: transport = "socket" needs `socket = "<path>"`')
        return Target(socket=sock)
    if transport == "quic":
        url, certs = table.get("quic"), table.get("cert_dir")
        if not url or not certs:
            raise ConfigError(
                f'{where}: transport = "quic" needs `quic = "quic://host:port"` and '
                '`cert_dir = "<the client bundle>"`'
            )
        if not url.startswith("quic://"):
            raise ConfigError(f"{where}: quic = {url!r} is not a quic://host:port URL")
        return Target(quic=url, cert_dir=certs)
    raise ConfigError(f'{where}: transport = {transport!r}; it is "socket" or "quic"')


def load(repo: Path) -> Config:
    gonk = read_toml(repo / ".gonk" / "gonk.toml")
    local = read_toml(repo / ".gonk" / "local.toml")
    layout = read_toml(repo / ".gonk" / "layout.toml")
    ledger = gonk.get("ledger")
    if ledger is not None:
        problem = ledger_problem(ledger)
        if problem:
            raise ConfigError(f"{repo / '.gonk' / 'gonk.toml'}: {problem}")
    holder = local.get("holder")
    if holder is not None:
        problem = holder_problem(holder)
        if problem:
            raise ConfigError(f"{repo / '.gonk' / 'local.toml'}: {problem}")
    return Config(
        repo=repo,
        ledger=ledger,
        target=target_from(local, str(repo / ".gonk" / "local.toml")),
        holder=holder,
        layout=layout,
    )


PROCESS_TOML = Path(".gonk") / "process.toml"


def read_process(repo: Path) -> dict | None:
    """`.gonk/process.toml`, the process profiles this repository was generated with, or None
    when it has none (generated before profiles existed). Read, never guessed: what a
    repository's process is, this file says and nothing else does."""
    path = repo / PROCESS_TOML
    if not path.is_file():
        return None
    process = read_toml(path)
    if process.get("version") != 1:
        raise ConfigError(f"{path}: `version = 1` is the only process file this tool reads")
    names = process.get("process")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ConfigError(f"{path}: `process` is a list of profile names")
    return process


def review_root(repo: Path, process: dict) -> str:
    """The `gonk.browse.root` name gonk serves this repository under: `[review] root`, or
    this working tree's directory name."""
    return process.get("review", {}).get("root") or repo.name


def layout_problems(repo: Path, layout: dict) -> tuple[list[str], list[str]]:
    """(what the layout declares, what is wrong with it)."""
    ok, bad = [], []
    if layout.get("version") != 1:
        bad.append(".gonk/layout.toml: `version = 1` is the only layout this tool reads")
        return ok, bad
    for spec in layout.get("spec", []):
        fmt, root = spec.get("format"), spec.get("root")
        if not fmt or not root:
            bad.append(".gonk/layout.toml: each [[spec]] names a `format` and a `root`")
            continue
        if not (repo / root).is_dir():
            bad.append(f"[[spec]] {fmt}: {root}/ does not exist")
            continue
        missing = [d for d in spec.get("dirs", []) if not (repo / root / d).is_dir()]
        if missing:
            bad.append(f"[[spec]] {fmt}: {root}/ has no {', '.join(missing)}/")
            continue
        ok.append(f"{fmt} at {root}/")
    if not layout.get("spec"):
        bad.append(".gonk/layout.toml declares no [[spec]]")
    for section in ("artifacts", "queries", "scripts"):
        root = layout.get(section, {}).get("root")
        if not root:
            bad.append(f".gonk/layout.toml: [{section}] names no `root`")
        elif not (repo / root).is_dir():
            bad.append(f"[{section}] {root}/ does not exist")
        else:
            ok.append(f"{section} at {root}/")
    return ok, bad


def named_artifacts(repo: Path, layout: dict) -> list[tuple[str, str]]:
    """Every (file, artifact name) a spec or change names on its marker line."""
    marker = layout.get("artifacts", {}).get("marker", "Artifacts:")
    found = []
    for spec in layout.get("spec", []):
        root = repo / spec.get("root", "")
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            if md.name == "README.md":
                continue
            for line in md.read_text(encoding="utf-8").splitlines():
                text = line.strip().lstrip("-* ").strip()
                if text.startswith(marker):
                    for name in re.findall(r"`([^`]+)`", text[len(marker) :]):
                        found.append((str(md.relative_to(repo)), name))
    return found


def artifact_problems(repo: Path, layout: dict) -> list[str]:
    root = layout.get("artifacts", {}).get("root", "artifacts")
    bad = []
    for where, name in named_artifacts(repo, layout):
        if not ARTIFACT_SHAPE.match(name):
            bad.append(f"{where} names artifact `{name}`, which is not a directory name here")
        elif not (repo / root / name).is_dir():
            bad.append(f"{where} names artifact `{name}`, and {root}/{name}/ does not exist")
    return bad


# --------------------------------------------------------------------------- openspec

# The IRIs below are the ones the gonk Book's OpenSpec mapping mints
# (`urn:markdown:mapping:openspec`), so an item seeded here and a node in the lifted spec
# graph are one name:
#   urn:openspec:change:<id>:task:<number>
#   urn:openspec:spec:<capability>:requirement:<slug>
TASK_LINE = re.compile(r"^\s*[-*] \[( |x|X)\] (\d+(?:\.\d+)*) +(.+?)\s*$")
CITES = re.compile(r"^(.*?) *\(([^()]+)\)$")
DELTA_SECTION = re.compile(r"^## (ADDED|MODIFIED|REMOVED|RENAMED) Requirements\s*$")
REQUIREMENT = re.compile(r"^### Requirement: (.+?)\s*$")


def slug(name: str) -> str:
    """The mapping's slug: lowercase, every run of other characters one `-`, none at the ends."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def requirement_iri(capability: str, name: str) -> str:
    return f"urn:openspec:spec:{capability}:requirement:{slug(name)}"


@dataclass
class Task:
    change: str
    number: str
    title: str
    done: bool
    cites: str | None  # the requirement named in the task's trailing parentheses
    requirement: str | None  # its IRI, when the change's delta adds or modifies that name

    @property
    def iri(self) -> str:
        return f"urn:openspec:change:{self.change}:task:{self.number}"


@dataclass
class Change:
    id: str
    title: str
    artifacts: list[str]
    tasks: list[Task]


def openspec_root(repo: Path, layout: dict) -> Path | None:
    for spec in layout.get("spec", []):
        if spec.get("format") == "openspec" and spec.get("root"):
            return repo / spec["root"]
    return None


def open_changes(repo: Path, layout: dict) -> list[Change]:
    """Every change in flight (`changes/<id>/`, not `changes/archive/`), with its tasks."""
    root = openspec_root(repo, layout)
    if root is None or not (root / "changes").is_dir():
        return []
    marker = layout.get("artifacts", {}).get("marker", "Artifacts:")
    changes = []
    for folder in sorted(p for p in (root / "changes").iterdir() if p.is_dir()):
        if folder.name == "archive":
            continue
        if not ARTIFACT_SHAPE.match(folder.name):
            raise ConfigError(
                f"{folder.relative_to(repo)}: a change id is lowercase letters, digits, `-` and "
                "`_` (it becomes a ledger label and an IRI segment)"
            )
        title, artifacts = folder.name, []
        proposal = folder / "proposal.md"
        if proposal.is_file():
            for line in proposal.read_text(encoding="utf-8").splitlines():
                if line.startswith("# ") and title == folder.name:
                    title = line[2:].strip()
                text = line.strip().lstrip("-* ").strip()
                if text.startswith(marker):
                    artifacts += re.findall(r"`([^`]+)`", text[len(marker) :])
        # The requirements this change's delta adds or modifies, by name: what a task's
        # parenthesis may cite. A typo cites nothing, rather than the wrong requirement.
        named: dict[str, str] = {}
        for delta in sorted((folder / "specs").glob("*/spec.md")):
            capability, section = delta.parent.name, None
            for line in delta.read_text(encoding="utf-8").splitlines():
                m = DELTA_SECTION.match(line)
                if m:
                    section = m.group(1)
                    continue
                m = REQUIREMENT.match(line)
                if m and section in ("ADDED", "MODIFIED"):
                    named[m.group(1)] = requirement_iri(capability, m.group(1))
        tasks = []
        tasks_md = folder / "tasks.md"
        if tasks_md.is_file():
            for line in tasks_md.read_text(encoding="utf-8").splitlines():
                m = TASK_LINE.match(line)
                if not m:
                    continue
                text, cites = m.group(3), None
                c = CITES.match(text)
                if c:
                    text, cites = c.group(1), c.group(2)
                tasks.append(
                    Task(
                        change=folder.name,
                        number=m.group(2),
                        title=text,
                        done=m.group(1) != " ",
                        cites=cites,
                        requirement=named.get(cites) if cites else None,
                    )
                )
        changes.append(Change(folder.name, title, artifacts, tasks))
    return changes


# What `append` answers: `acme#3 <iri>` when it filed, `acme#3 <iri> existing <status>` when
# an item already carries the key (ikigai-ledger's keyed append).
APPENDED = re.compile(r"^(\S+#\d+) (\S+)(?: (existing) (\S+))?\s*$", re.MULTILINE)


def seed_request(change: Change, task: Task) -> tuple[str, str, str]:
    """(the append's arguments, its content, a one-line description) for one open task."""
    labels = ",".join([f"change:{change.id}", *(f"artifact:{a}" for a in change.artifacts)])
    about = " ".join([task.iri, *([task.requirement] if task.requirement else [])])
    words = [f"labels={labels}", f"key={task.iri}", f'about="{about}"']
    body = [
        f"Task {task.number} of the change `{change.id}` ({change.title}), "
        f"openspec/changes/{change.id}/tasks.md.",
    ]
    if task.requirement:
        body.append(f"It implements the requirement {task.cites!r} ({task.requirement}).")
    elif task.cites:
        body.append(
            f"It cites {task.cites!r}, which the change's delta neither adds nor modifies, so "
            "it is about the task alone."
        )
    if change.artifacts:
        body.append("Artifacts: " + ", ".join(f"`{a}`" for a in change.artifacts))
    content = f"{change.id} {task.number}: {task.title}\n\n" + "\n".join(body)
    return " ".join(words), content, f"{change.id} {task.number}: {task.title}"


@dataclass
class Seeded:
    """One open task's item, as `append` answered for it."""

    task: Task
    display: str  # `library#3`
    iri: str
    new: bool  # filed by this run, rather than already carrying the task's key

    @property
    def number(self) -> int:
        return int(self.display.rsplit("#", 1)[1])


def blocks(item: dict, iri: str) -> bool:
    """Does this item (its JSON face) already carry a `blocks` link to `iri`?"""
    return any(
        link.get("type") == "blocks" and (link.get("target") or {}).get("iri") == iri
        for link in item.get("links") or []
    )


def order_pairs(change: Change, seeded: dict[str, Seeded]) -> list[tuple[Task, Task]]:
    """Each open task of a change and the open task after it, in the order of tasks.md.

    A checked-off task is skipped, so the tasks either side of it are linked to each other:
    the order still holds across it. A task with no item (its append failed) breaks the chain
    there, and the next seed, which files it, links it in."""
    open_tasks = [t for t in change.tasks if not t.done]
    return [
        (a, b)
        for a, b in zip(open_tasks, open_tasks[1:], strict=False)
        if a.iri in seeded and b.iri in seeded
    ]


def cmd_seed(args, cfg: Config, make_ikigai) -> int:
    """File one ledger item per open task of every change in flight, keyed by the task's IRI,
    then link each task's item to the next one's with `blocks`.

    The order of an OpenSpec tasks.md IS a dependency (1.2 is written assuming 1.1 is done),
    and without it the ledger's `next` ranks unprioritized items newest first, so freshly
    seeded tasks would come out last-first. With the links, only the first open task of each
    change is ready; closing it makes the next one ready.

    Idempotent through the ledger itself: an append whose key an item already carries files
    nothing and answers `existing`, in the same store update that would have filed it, so two
    seeds at once cannot both file a task. A link is added only when it is missing (a link
    from an item filed by this run always is), so a rerun changes nothing. Reordering a
    tasks.md after seeding leaves the old links in place; `next` names any cycle that makes,
    with the `delete` that removes a link."""
    changes = open_changes(cfg.repo, cfg.layout)
    todo = [(c, t) for c in changes for t in c.tasks if not t.done]
    checked = sum(1 for c in changes for t in c.tasks if t.done)
    if not args.dry_run:
        need(cfg, "ledger", "target")
    if not todo:
        print(f"seed: no open tasks in {len(changes)} change(s) in flight; nothing to file")
        return 0
    if args.dry_run:
        for change, task in todo:
            _, _, what = seed_request(change, task)
            print(f"would file  {what}\n            key={task.iri}")
        planned = {t.iri: None for _, t in todo}
        pairs = [p for c in changes for p in order_pairs(c, planned)]
        for a, b in pairs:
            print(f"would link  {a.change} {a.number} blocks {b.number}")
        print(
            f"\nseed --dry-run: {len(todo)} open task(s) would be filed, in "
            f"{len(pairs)} order link(s); {checked} checked off"
        )
        return 0
    ikigai = make_ikigai(cfg.target)
    filed = existing = failed = 0
    seeded: dict[str, Seeded] = {}
    for change, task in todo:
        words, content, what = seed_request(change, task)
        answer = ikigai.run(f"sink {cfg.resource('append')} {words}", stdin=content)
        m = APPENDED.search(answer.out) if answer.ok else None
        if m is None:
            failed += 1
            print(f"failed      {what}: {answer.text() or f'exit {answer.code}'}", file=sys.stderr)
            continue
        seeded[task.iri] = Seeded(task, m.group(1), m.group(2), new=not m.group(3))
        if m.group(3):
            existing += 1
            print(f"existing    {m.group(1)} {what} ({m.group(4)})")
        else:
            filed += 1
            print(f"filed       {m.group(1)} {what}")
    linked = 0
    for change in changes:
        for a, b in order_pairs(change, seeded):
            first, then = seeded[a.iri], seeded[b.iri]
            what = (
                f"{first.display} blocks {then.display} ({a.change} {a.number} before {b.number})"
            )
            if not (first.new or then.new):
                # Both were there before this run: the link is too, unless an earlier seed
                # stopped between filing and linking. Ask rather than write, so a rerun
                # leaves every item's modified time alone.
                item, err = item_json(cfg, ikigai, first.number)
                if item is None:
                    failed += 1
                    print(
                        f"failed      {what}: could not read {first.display}: {err}",
                        file=sys.stderr,
                    )
                    continue
                if blocks(item, then.iri):
                    continue
            # The object goes in by pipe, where `link` takes it (`content`); an IRI rather than
            # `library#3`, since `#` starts a comment in the engine grammar.
            command = f"sink {cfg.resource('link')} item={first.iri} type=blocks"
            answer = ikigai.run(command, stdin=then.iri)
            if answer.ok:
                linked += 1
                print(f"ordered     {what}")
            else:
                failed += 1
                print(
                    f"failed      {what}: {answer.text() or f'exit {answer.code}'}", file=sys.stderr
                )
    print(
        f"\nseed: {filed} filed, {existing} already filed, {checked} checked off (not filed), "
        f"{linked} order link(s) added" + (f", {failed} FAILED" if failed else "")
    )
    return 1 if failed else 0


# --------------------------------------------------------------------------- saved scripts

# A saved query or script names its own ledger as `LEDGER` (`urn:iki:ledger:graph:LEDGER`,
# `urn:iki:ledger:LEDGER:next`): a valid IRI segment as written, and never a ledger name
# (those are lowercase), so the committed file is valid SPARQL or Turtle and `init` can
# publish it for whichever ledger .gonk/gonk.toml names.
LEDGER_TOKEN = "LEDGER"
SCRIPT_LANGUAGES = {".rq": "sparql", ".plan.ttl": "plan", ".scm": "lisp", ".lisp": "lisp"}


@dataclass
class Saved:
    path: Path
    stem: str
    language: str

    def published_name(self, ledger: str) -> str:
        """`urn:script:{ledger}-{stem}`: one gonk holds many repositories' scripts."""
        return f"{ledger}-{self.stem}"

    def text_for(self, ledger: str) -> str:
        return self.path.read_text(encoding="utf-8").replace(LEDGER_TOKEN, ledger)


def saved_scripts(repo: Path, layout: dict) -> tuple[list[Saved], list[str]]:
    """(every saved query and script, what is wrong with any file that is neither)."""
    found, bad = [], []
    for section in ("queries", "scripts"):
        root = layout.get(section, {}).get("root")
        if not root or not (repo / root).is_dir():
            continue
        for p in sorted((repo / root).iterdir()):
            if not p.is_file() or p.name == "README.md" or p.name.startswith("."):
                continue
            ext = next((e for e in SCRIPT_LANGUAGES if p.name.endswith(e)), None)
            if ext is None:
                bad.append(
                    f"{p.relative_to(repo)}: not a script this tool knows "
                    f"({', '.join(SCRIPT_LANGUAGES)})"
                )
                continue
            stem = p.name[: -len(ext)]
            if not ARTIFACT_SHAPE.match(stem):
                bad.append(f"{p.relative_to(repo)}: a script name is lowercase, digits, `-`, `_`")
                continue
            found.append(Saved(p, stem, SCRIPT_LANGUAGES[ext]))
    return found, bad


def publish_rows(cfg: Config, ikigai: Ikigai, saved: list[Saved], row) -> None:
    """Publish each saved query and script to gonk's `urn:script:` (ikigai-script)."""
    if not cfg.ledger:
        row(
            "missing",
            "scripts",
            "name the ledger first: the saved scripts are published per ledger",
        )
        return
    published, failed = [], []
    for s in saved:
        name = s.published_name(cfg.ledger)
        command = f"sink {PREFIX}script:{name} language={s.language}"
        answer = ikigai.run(command, stdin=s.text_for(cfg.ledger))
        (published if answer.ok else failed).append((name, answer))
    if published:
        names = ", ".join(f"urn:script:{n}" for n, _ in published)
        row("ok", "scripts", f"published {len(published)}: {names}")
    for name, answer in failed:
        row("failed", "scripts", f"urn:script:{name} was refused: {answer.text()}")


# --------------------------------------------------------------------------- ikigai


@dataclass
class Answer:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def text(self) -> str:
        return (self.out + self.err).strip()


class Ikigai:
    """Runs `ikigai` with this repository's mount. A test passes its own `run`."""

    @staticmethod
    def locate() -> tuple[str | None, str]:
        """Where `ikigai` is on PATH, and what it says its version is."""
        binary = shutil.which("ikigai")
        if not binary:
            return None, ""
        done = subprocess.run([binary, "--version"], capture_output=True, text=True)
        return binary, done.stdout.strip() or "ikigai"

    def __init__(self, target: Target, binary: str = "ikigai") -> None:
        self.target = target
        self.binary = binary

    def run(self, command: str, stdin: str | None = None) -> Answer:
        argv = [self.binary, *self.target.mount_args(), "-c", command]
        try:
            done = subprocess.run(
                argv,
                input=stdin if stdin is not None else "",
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
            )
        except FileNotFoundError:
            return Answer(127, "", f"`{self.binary}` is not on PATH")
        except subprocess.TimeoutExpired:
            return Answer(124, "", f"no answer within {TIMEOUT_S}s from {self.target.describe()}")
        return Answer(done.returncode, done.stdout, without_cache_trailer(done.stderr))


# What `ikigai -c` writes to stderr after every answer: how the kernel's cache treated it
# (`[uncacheable]`, `[cached]`, `[2 cached · 1 computed]`). It is for a person at the REPL,
# and echoed here it read as part of what gonk said.
CACHE_TRAILER = re.compile(
    r"^\[(?:\d+ )?(?:cached|computed|uncacheable)(?: · \d+ (?:cached|computed|uncacheable))*\]$"
)


def without_cache_trailer(err: str) -> str:
    return "".join(
        ln for ln in err.splitlines(keepends=True) if not CACHE_TRAILER.match(ln.strip())
    )


LEDGERS_ROW = re.compile(r"^\s*(\S+)\s+(\d+) open\s+(\d+) total\b")


def parse_ledgers(text: str) -> dict[str, tuple[int, int]]:
    """`source urn:iki:ledger:ledgers` → {name: (open, total)}."""
    rows = {}
    for line in text.splitlines():
        m = LEDGERS_ROW.match(line)
        if m:
            rows[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return rows


def offered(answer: Answer) -> bool | None:
    """Whether a `describe` found an endpoint: True, False, or None when gonk did not answer.

    `describe` of a name the peer does not bind still exits 0, with a placeholder whose
    title is `contract unavailable` and a note naming `no endpoint resolved`."""
    if not answer.ok:
        return None
    text = answer.text()
    return not ("no endpoint resolved" in text or "contract unavailable" in text)


# --------------------------------------------------------------------------- claims


def iso_now() -> datetime:
    return datetime.now(UTC)


def parse_instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


CLAIMED = re.compile(r"(?:claimed by|taken over from \S+ by) (\S+)(?: until (\S+))?")


def read_claim(cfg: Config) -> dict | None:
    table = read_toml(cfg.claim_file)
    return table or None


def item_json(cfg: Config, ikigai: Ikigai, number: int) -> tuple[dict | None, str | None]:
    answer = ikigai.run(f"source {cfg.resource(f'item:{number}')} as=application/json")
    if not answer.ok:
        return None, answer.text() or f"exit {answer.code}"
    try:
        return json.loads(answer.out)["item"], None
    except (ValueError, KeyError) as e:
        return None, f"the item's JSON face did not parse: {e}"


def check_claim(cfg: Config, ikigai: Ikigai, now: datetime | None = None) -> tuple[bool, str]:
    """Does this working tree hold a live claim, according to gonk? (allowed, why)."""
    claim = read_claim(cfg)
    if claim is None:
        return False, (
            "this working tree holds no claim. Claim the item first: "
            "`python3 tools/gonk.py claim <number>` (or `just claim <number>`)"
        )
    if claim.get("ledger") != cfg.ledger:
        return False, (
            f"the claim in .gonk/state/claim.toml is on ledger `{claim.get('ledger')}`, and "
            f"this repository's ledger is `{cfg.ledger}`: release it and claim again"
        )
    number = claim.get("item")
    if not isinstance(number, int):
        return False, f"{cfg.claim_file} names no item number: release and claim again"
    item, err = item_json(cfg, ikigai, number)
    if item is None:
        return False, f"could not read item {number} from gonk to check the claim: {err}"
    held = (item.get("claim") or {}).get("holder")
    if held != claim.get("holder"):
        who = f"by `{held}`" if held else "by nobody"
        return False, (
            f"item {item.get('display', number)} is held {who}, not `{claim.get('holder')}`: "
            "the claim was released or taken over. Stop writing; claim another item"
        )
    if item.get("status") != "open":
        return False, f"item {item.get('display', number)} is {item.get('status')}"
    expires = (item.get("claim") or {}).get("expires")
    if expires and parse_instant(expires) <= (now or iso_now()):
        return False, (
            f"the lease on item {item.get('display', number)} ran out at {expires}. It is not "
            "free (nobody else may take it without a takeover), but renew before writing: "
            f"`python3 tools/gonk.py claim {number}`"
        )
    until = f" until {expires}" if expires else " (no lease)"
    return True, f"item {item.get('display', number)} is held by `{held}`{until}"


# --------------------------------------------------------------------------- commands


def need(cfg: Config, *what: str) -> None:
    missing = []
    if "ledger" in what and not cfg.ledger:
        missing.append(
            "no ledger is named in .gonk/gonk.toml: `python3 tools/gonk.py init --ledger <name>`"
        )
    if "target" in what and cfg.target is None:
        missing.append(
            "this working tree does not say how to reach gonk (.gonk/local.toml): "
            "`python3 tools/gonk.py init --socket <path>` or "
            "`init --quic quic://host:port --cert-dir <bundle>`"
        )
    if "holder" in what and not cfg.holder:
        missing.append(
            "this working tree has no holder name (.gonk/local.toml): "
            "`python3 tools/gonk.py init --holder <name>`"
        )
    if missing:
        raise ConfigError("\n".join(missing))


def cmd_init(args, cfg: Config, make_ikigai) -> int:
    repo = cfg.repo
    # 1. Write what the flags say, before anything is checked, so a re-run converges.
    if args.ledger is not None:
        problem = ledger_problem(args.ledger)
        if problem:
            raise ConfigError(problem)
        set_key(cfg.gonk_toml, GONK_TOML_HEADER, "ledger", args.ledger)
        cfg.ledger = args.ledger
    local = read_toml(cfg.local_toml)
    if args.socket and args.quic:
        raise ConfigError("--socket and --quic name two ways to reach one gonk: pick one")
    if args.cert_dir and not args.quic:
        raise ConfigError("--cert-dir is the QUIC client bundle, and means nothing without --quic")
    if args.socket:
        local = {"transport": "socket", "socket": expand(args.socket, Path.cwd())}
        local |= {"holder": cfg.holder} if cfg.holder else {}
    if args.quic:
        if not args.cert_dir:
            raise ConfigError(
                "--quic needs --cert-dir: the directory holding this machine's client "
                "certificate (`ikigai cert generate --dir <dir>`)"
            )
        if not args.quic.startswith("quic://"):
            raise ConfigError(f"--quic {args.quic!r} is not a quic://host:port URL")
        local = {
            "transport": "quic",
            "quic": args.quic,
            "cert_dir": expand(args.cert_dir, Path.cwd()),
        }
        local |= {"holder": cfg.holder} if cfg.holder else {}
    if args.holder is not None:
        problem = holder_problem(args.holder)
        if problem:
            raise ConfigError(problem)
        local["holder"] = args.holder
    if args.socket or args.quic or args.holder is not None:
        write_toml(cfg.local_toml, LOCAL_TOML_HEADER, local)
        cfg = load(repo)

    rows: list[tuple[str, str, str]] = []  # (status, what, detail)

    def row(status: str, what: str, detail: str) -> None:
        rows.append((status, what, detail))

    ok, bad = layout_problems(repo, cfg.layout)
    for line in bad:
        row("missing", "layout", line)
    if ok and not bad:
        row("ok", "layout", "; ".join(ok))
    for line in artifact_problems(repo, cfg.layout):
        row("missing", "artifacts", line)
    saved, unknown = saved_scripts(repo, cfg.layout)
    for line in unknown:
        row("missing", "scripts", line)
    # The harness questions are asked by `gonk.py harness` through the store, never published:
    # gonk runs every script under a ceiling of ledger reads (its README, "The ceiling"), and
    # they read `urn:iki:harness:graph:<ledger>`, so a published one could only be refused, and
    # offering a tool that can only refuse is what the manifold must not do.
    questions = [s for s in saved if s.stem.startswith(HARNESS_PREFIX)]
    saved = [s for s in saved if not s.stem.startswith(HARNESS_PREFIX)]
    if questions:
        row(
            "note",
            "harness",
            f"{len(questions)} harness question(s) in .gonk/queries/ are asked by "
            "`python3 tools/gonk.py harness` through gonk's store, not published: gonk runs a "
            "script under a ceiling of ledger reads, and they read the harness graph",
        )
    tasks = [t for c in open_changes(repo, cfg.layout) for t in c.tasks if not t.done]
    if tasks:
        changes = len({t.change for t in tasks})
        row(
            "note",
            "openspec",
            f"{len(tasks)} open task(s) in {changes} change(s) in flight: "
            "`python3 tools/gonk.py seed` files them as ledger items (again is a no-op)",
        )

    process = read_process(repo)
    process_rows(cfg, process, row)

    binary, version = make_ikigai.locate()
    if binary:
        row("ok", "ikigai", f"{version} at {binary}")
    else:
        row(
            "missing",
            "ikigai",
            "`ikigai` is not on PATH: `cargo install --locked ikigai-cli --features quic`",
        )

    if cfg.ledger:
        row("ok", "ledger", f"`{cfg.ledger}` (.gonk/gonk.toml)")
    else:
        row(
            "missing",
            "ledger",
            "name the ledger this repository's work lives in: `init --ledger <name>`",
        )

    if cfg.holder:
        row("ok", "holder", f"`{cfg.holder}` (.gonk/local.toml, this working tree only)")
    else:
        row(
            "missing",
            "holder",
            "name the holder this working tree claims under, one per agent: `init --holder <name>`",
        )

    if cfg.target is None:
        row(
            "missing",
            "gonk",
            "say how this machine reaches gonk: `init --socket ~/.ikigai/gonk.sock` (gonk's "
            "default on this machine) or `init --quic quic://<host>:1062 --cert-dir <bundle>`",
        )
    else:
        identity_rows(cfg, row)
    if cfg.target is not None and binary:
        if cfg.target.socket and not Path(cfg.target.socket).exists():
            row("missing", "gonk", f"no socket at {cfg.target.socket}: is gonk running there?")
        else:
            ikigai = make_ikigai(cfg.target)
            answer = ikigai.run(f"source {PREFIX}iki:ledger:ledgers")
            if not answer.ok:
                row("missing", "gonk", f"{cfg.target.describe()} did not answer: {answer.text()}")
            else:
                ledgers = parse_ledgers(answer.out)
                row("ok", "gonk", f"{cfg.target.describe()} answers")
                if cfg.ledger in ledgers:
                    open_, total = ledgers[cfg.ledger]
                    row("ok", "items", f"`{cfg.ledger}` holds {total} item(s), {open_} open")
                elif cfg.ledger:
                    row(
                        "note",
                        "items",
                        f"`{cfg.ledger}` holds nothing yet, or this identity may not read it. A "
                        "ledger has no create: it exists once something is filed in it "
                        '(`python3 tools/gonk.py file "<title>"`)',
                    )
                for what, name, about, meanwhile in OPTIONAL_FAMILIES:
                    found = offered(ikigai.run(f"describe {PREFIX}{name[4:]}"))
                    if found:
                        row("ok", what, f"gonk offers `{name}`: {about}")
                        if what == "scripts" and saved:
                            publish_rows(cfg, ikigai, saved, row)
                    elif found is False:
                        ready = (
                            f"; {len(saved)} saved here are ready but unpublished"
                            if what == "scripts" and saved
                            else ""
                        )
                        row(
                            "absent",
                            what,
                            f"gonk does not offer `{name}` yet, so {meanwhile}{ready}; "
                            "nothing to do until it does",
                        )
                    else:
                        row("unknown", what, f"could not ask gonk about `{name}`")
                if process and "review" in process.get("checks", []):
                    review_rows(cfg, process, ikigai, row)

    width = max(len(w) for _, w, _ in rows)
    print(f"gonk init: {repo}")
    for status, what, detail in rows:
        print(f"  {status:<8} {what:<{width}}  {detail}")
    if cfg.enrollment:
        print("\nthis machine's QUIC identity, for the gonk operator to enroll (once):")
        for line in cfg.enrollment:
            print(f"  {line}")
    if cfg.target is not None:
        print("\nagents over MCP (configure the agent with this command):")
        print(f"  {cfg.target.mcp_command()}")
    missing = sum(1 for status, _, _ in rows if status == "missing")
    print(f"\n{missing} thing(s) missing." if missing else "\nready.")
    return 1 if missing else 0


def identity_rows(cfg: Config, row) -> None:
    """Who gonk will say is asking, and what the operator must do for a QUIC identity."""
    target = cfg.target
    if target.socket:
        row(
            "ok",
            "identity",
            "the socket's owner: every request over it is `owner`, so the log cannot tell "
            "this tree from any other process of yours. A machine that must be named uses QUIC",
        )
        return
    certs = Path(target.cert_dir)
    crt, server = certs / "client.crt", certs / "server.crt"
    if not crt.is_file():
        row(
            "missing",
            "identity",
            f"no client.crt in {certs}: make this machine's own identity there with "
            f"`ikigai cert generate --dir {certs}` (its private key never leaves this machine), "
            "then run init again for the lines the gonk operator needs",
        )
        return
    fingerprint = cert_fingerprint(crt.read_text(encoding="utf-8"))
    row("ok", "identity", f"{client_iri(fingerprint)} (from {crt})")
    row(
        "ok",
        "alias",
        f"older records name it {legacy_client_iri(fingerprint)}: gonk reads that spelling as "
        "this machine too, and never mints it again",
    )
    cfg.enrollment = enrollment_lines(fingerprint, cfg.holder or "<name>", cfg.ledger or "<ledger>")
    if not server.is_file():
        row(
            "missing",
            "server",
            f"no server.crt in {certs}: copy the one from the bundle `ikigai-gonk client add` "
            "wrote on the gonk machine, so this machine can pin the server",
        )


def git_out(repo: Path, *argv: str) -> str | None:
    """A git command's output in `repo`, or None when it fails (not a working tree, no git)."""
    try:
        done = subprocess.run(["git", *argv], cwd=repo, capture_output=True, text=True)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def hook_settings_on(repo: Path) -> bool:
    """Is the Claude Code claim hook merged into this working tree's settings?"""
    settings = [repo / ".claude" / n for n in ("settings.json", "settings.local.json")]
    return any(s.is_file() and "require-claim.py" in s.read_text("utf-8") for s in settings)


def post_commit_installed(repo: Path) -> bool:
    """Does this working tree's git point `core.hooksPath` at hooks/git?"""
    return git_out(repo, "config", "--get", "core.hooksPath") == "hooks/git"


def worker_configured(repo: Path) -> bool:
    """Is this machine configured to run a worker (.gonk/worker.toml)?"""
    return (repo / ".gonk" / "worker.toml").is_file()


def process_rows(cfg: Config, process: dict | None, row) -> None:
    """The process this repository works by, and what its profiles need that `init` can see
    without asking gonk."""
    repo = cfg.repo
    if process is None:
        row(
            "note",
            "process",
            "no .gonk/process.toml, so this repository names no process; one generated by "
            "ikigai-gonk-starter's `tools/new.py --process <names>` has one",
        )
        return
    names = process.get("process", [])
    row("ok", "process", f"{', '.join(names) or '(none)'} (.gonk/process.toml)")
    for name in names:
        about = process.get("profile", {}).get(name, {})
        row("note", name, about.get("summary", "a profile this tool has no summary of"))
        for role in about.get("roles", []):
            row("note", "role", role)
    hooks = process.get("hooks", [])
    if "claude:require-claim" in hooks:
        on = hook_settings_on(repo)
        row(
            "ok" if on else "note",
            "hook",
            "the Claude Code claim hook is on (.claude/settings*.json)"
            if on
            else "the Claude Code claim hook is optional and off: merge "
            "hooks/claude/settings.sample.json into .claude/settings.json to turn it on",
        )
    if "git:post-commit" in hooks:
        hook = repo / "hooks" / "git" / "post-commit"
        if not hook.is_file():
            row("missing", "hook", "hooks/git/post-commit is not in this repository")
        elif git_out(repo, "rev-parse", "--git-dir") is None:
            row("missing", "hook", "not a git working tree yet: `git init`, then `just hooks`")
        elif not post_commit_installed(repo):
            row(
                "missing",
                "hook",
                "the post-commit review hook is not installed: "
                "`git config core.hooksPath hooks/git` (or `just hooks`)",
            )
        else:
            row("ok", "hook", "post-commit asks gonk to review each commit (core.hooksPath)")
    checks = process.get("checks", [])
    if "worker-config" in checks:
        if worker_configured(repo):
            row("ok", "worker", ".gonk/worker.toml configures this machine's worker loop")
        else:
            row(
                "note",
                "worker",
                "no .gonk/worker.toml, so this machine runs no worker: a worker machine copies "
                "worker/worker.sample.toml there (`python3 tools/worker.py plan` checks it)",
            )
    if "review" in checks:
        if cfg.target is not None and cfg.target.quic:
            row(
                "absent",
                "review",
                "this tree reaches gonk over QUIC, and a review request is dropped into a queue "
                "on gonk's own machine, which no network door reaches: commits here queue "
                "nothing (the hook says so and the commit goes ahead)",
            )
        elif shutil.which("ikigai-gonk") is None:
            row(
                "missing",
                "review",
                "`ikigai-gonk` is not on PATH: the post-commit hook drops each request with it",
            )


def review_rows(cfg: Config, process: dict, ikigai: Ikigai, row) -> None:
    """What review on every commit needs from gonk, asked of gonk: this repository as a browse
    root, a model behind it, and a review queue that is armed."""
    root = review_root(cfg.repo, process)
    served = offered(ikigai.run(f"describe {PREFIX}repo:{root}:tree"))
    if not served:
        row(
            "missing",
            "review",
            f"gonk serves no browse root named `{root}`: its operator adds "
            f'`gonk.browse.root = "{root}=<path to this repository on gonk\'s machine>"`, or '
            "name gonk's root for it in .gonk/process.toml's `[review] root`",
        )
        return
    row("ok", "review", f"gonk serves this repository as the browse root `{root}`")
    if offered(ikigai.run(f"describe {PREFIX}repo:{root}:review:AGENTS.md")):
        row("ok", "model", f"gonk binds review for `{root}`, so a model is mounted")
    else:
        row(
            "missing",
            "model",
            "gonk binds no review pass: it reviews through a model peer, mounted with `gonk.mount`",
        )
    depth = ikigai.run(f"source {PREFIX}iki:gonk:review:depth as=application/json")
    try:
        state = json.loads(depth.out) if depth.ok else None
    except ValueError:
        state = None
    if not isinstance(state, dict):
        row("unknown", "queue", f"could not read gonk's review queue: {depth.text()}")
    elif not state.get("configured"):
        row("missing", "queue", 'gonk has no review queue: `gonk.review.space = "reviews"`')
    elif not state.get("armed"):
        row(
            "missing",
            "queue",
            "gonk's review queue is not armed, so a request waits for a person to run it: "
            "`gonk.review.grant` and `gonk.review.arm = true`",
        )
    else:
        row("ok", "queue", state.get("sentence") or "gonk's review queue is armed")


# (row, the family's resource, what it offers, what holds until gonk offers it). The last is
# a clause of its own per family, since one template over both read "drafting a query from a
# sentence stay files".
OPTIONAL_FAMILIES = [
    (
        "scripts",
        "urn:script:catalog",
        "saved queries and workflow scripts (ikigai-script)",
        "saved queries and workflow scripts (ikigai-script) stay files in this repository",
    ),
    (
        "nl",
        "urn:nl:sparql",
        "drafting a query from a sentence (ikigai-nl)",
        "a query is written by hand rather than drafted from a sentence (ikigai-nl)",
    ),
]

GONK_TOML_HEADER = """\
# Which ledger this repository's work lives in. Committed: every clone files and claims
# in the same ledger. How a machine reaches gonk is NOT here; see .gonk/local.toml.
# Written by `python3 tools/gonk.py init --ledger <name>`."""

LOCAL_TOML_HEADER = """\
# How THIS working tree reaches gonk, and the holder name it claims under. Git-ignored:
# a socket path and a certificate directory are this machine's, and a holder is one agent's.
# Written by `python3 tools/gonk.py init`; re-run it to change any of it."""


def set_key(path: Path, header: str, key: str, value: str) -> None:
    """Set one `key = "value"` line in a small TOML file, keeping its comments."""
    text = path.read_text(encoding="utf-8") if path.exists() else header + "\n"
    line = f"{key} = {toml_string(value)}"
    pattern = re.compile(rf"^#?\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    text = (
        pattern.sub(line, text, count=1)
        if pattern.search(text)
        else text.rstrip("\n") + f"\n{line}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def show(answer: Answer) -> int:
    sys.stdout.write(answer.out)
    sys.stderr.write(answer.err)
    return 0 if answer.ok else 1


def cmd_file(args, cfg: Config, make_ikigai) -> int:
    need(cfg, "ledger", "target")
    words = []
    if args.priority is not None:
        words.append(f"priority={int(args.priority)}")
    if args.labels:
        if not re.fullmatch(r"[A-Za-z0-9._,:-]+", args.labels):
            raise ConfigError("--labels is a comma-separated list of label words, no spaces")
        words.append(f"labels={args.labels}")
    if args.key:
        if not re.fullmatch(r"\S+", args.key) or "#" in args.key:
            raise ConfigError("--key is one word, with no `#` (the grammar's comment)")
        words.append(f"key={args.key}")
    body = args.title.strip() + (f"\n\n{args.body.strip()}" if args.body else "")
    command = " ".join(["sink", cfg.resource("append"), *words])
    return show(make_ikigai(cfg.target).run(command, stdin=body))


def cmd_next(args, cfg: Config, make_ikigai) -> int:
    need(cfg, "ledger", "target")
    return show(make_ikigai(cfg.target).run(f"source {cfg.resource('next')}"))


def cmd_claim(args, cfg: Config, make_ikigai) -> int:
    need(cfg, "ledger", "target", "holder")
    if not LEASE_SHAPE.match(args.lease):
        raise ConfigError(
            f"--lease {args.lease!r}: `30m`, `2h`, `1d` or an xsd:duration like `PT30M`"
        )
    held = read_claim(cfg)
    if held and held.get("item") != args.item:
        raise refuse(
            "one-claim-per-tree",
            cfg.repo,
            f"this working tree already holds item {held.get('item')} on `{held.get('ledger')}`: "
            "one item per working tree. Release or close it first",
            args,
        )
    words = ["sink", cfg.resource("claim"), f"item={args.item}", f"lease={args.lease}"]
    if args.takeover_from:
        problem = holder_problem(args.takeover_from)
        if problem:
            raise ConfigError(problem)
        words += ["takeover=true", f"from={args.takeover_from}"]
    answer = make_ikigai(cfg.target).run(" ".join(words), stdin=cfg.holder)
    code = show(answer)
    if not answer.ok:
        fire_claim_refusal(cfg, answer.text(), bool(args.takeover_from), args)
    if answer.ok:
        m = CLAIMED.search(answer.out)
        state = {"ledger": cfg.ledger, "item": args.item, "holder": cfg.holder}
        if m and m.group(2):
            state["expires"] = m.group(2)
        cfg.claim_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.claim_file.write_text(
            "\n".join(
                [
                    "# The claim this working tree holds. Written by `tools/gonk.py claim`;",
                    "# gonk is the truth, and `status` and the optional hook ask it.",
                    *(
                        f"{k} = {v if isinstance(v, int) else toml_string(v)}"
                        for k, v in state.items()
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return code


def fire_claim_refusal(cfg: Config, text: str, takeover: bool, args) -> None:
    """Record the ledger's refusal of a claim, which gonk.py relays, as the sensor it is. The
    ledger enforces these rules and says which by its wording (ikigai-ledger, `claim`), so the
    wording is matched here; tests/test_integration.py holds it to a real gonk's."""
    if takeover:
        refused = (
            "only an expired lease can be taken over",
            "which never expires",
            "a takeover is a compare-and-set",
            "nothing to take over",
        )
        control = sensor("takeover-refused") if any(w in text for w in refused) else None
    elif "already claimed by" in text:
        control = sensor("claim-held-by-another")
    elif "an expired lease is never silently free" in text or "takeover=true from=" in text:
        control = sensor("claim-lease-expired")
    else:
        control = None  # gonk did not answer, or refused for another reason: no rule here
    if control:
        fire(control, "refusal", cfg.repo, command=command_line(args))


def sensor(control: str) -> str:
    """Names the sensor whose firing is recorded a few lines later; answers the id unchanged
    (`harness.sensor`), so `tools/harness.py check` can read the literal."""
    return control


def cmd_release(args, cfg: Config, make_ikigai) -> int:
    need(cfg, "ledger", "target")
    held = read_claim(cfg)
    number = args.item if args.item is not None else (held or {}).get("item")
    if number is None:
        raise ConfigError("this working tree holds no claim; name the item to release")
    ikigai = make_ikigai(cfg.target)
    if args.note and args.note.strip():
        # The note is a comment of its own, posted first, as `report` posts its evidence: the
        # cli gives a batch's stdin only to a content-less SINK, never to a DELETE, so a note
        # piped to the release never reached the item (ledger item 1229).
        posted = ikigai.run(f"sink {cfg.resource('comment')} item={number}", stdin=args.note)
        if not posted.ok:
            return show(posted)
    answer = ikigai.run(f"delete {cfg.resource('claim')} item={number}", stdin="")
    if answer.ok and held and held.get("item") == number:
        cfg.claim_file.unlink(missing_ok=True)
    return show(answer)


def evidence_note(args, cfg: Config) -> str:
    if not args.evidence or not EVIDENCE_SHAPE.match(args.evidence):
        raise refuse(
            "evidence-required",
            cfg.repo,
            "--evidence is a URL (the pull request, or the reproduction that shows it is not a "
            "defect): a report without evidence is a claim nobody can check",
            args,
        )
    return f"Evidence: {args.evidence}" + (f"\n\n{args.note.strip()}" if args.note else "")


def comment_flag(ikigai: Ikigai, cfg: Config, number: int, note: str, flag: str) -> Answer:
    """Post `note` with `flag=true`.

    The flag is a hand-back's state (`handback`, `rejects`: ikigai-ledger 0.8.0, ledger item
    1199). A ledger older than that does not know the argument and ignores it — the comment is
    written either way — so the `Evidence:` prefix stays in the note for the ledgers (and the
    readers) that have no such state.
    """
    return ikigai.run(f"sink {cfg.resource('comment')} item={number} {flag}=true", stdin=note)


def cmd_report(args, cfg: Config, make_ikigai) -> int:
    """The worker's end of an item: the evidence as a comment, then the claim handed back.

    The comment is marked `handback=true`, so the ledger knows the item waits for its
    dispatcher and `next` stops offering it to the next worker (ikigai-ledger 0.8.0, ledger
    item 1199). Closing is the dispatcher's, after checking that evidence.
    """
    need(cfg, "ledger", "target")
    note = evidence_note(args, cfg)
    ikigai = make_ikigai(cfg.target)
    answer = comment_flag(ikigai, cfg, args.item, note, "handback")
    code = show(answer)
    if not answer.ok:
        return code
    held = read_claim(cfg)
    if held and held.get("item") == args.item:
        code = show(ikigai.run(f"delete {cfg.resource('claim')} item={args.item}", stdin=""))
        if code == 0:
            cfg.claim_file.unlink(missing_ok=True)
    return code


def cmd_reject(args, cfg: Config, make_ikigai) -> int:
    """The dispatcher's rejection of a hand-back: the evidence did not hold, so the item is
    ready again. `rejects=true` clears the ledger's awaiting state (0.8.0, ledger item 1199),
    which a worker's ordinary comment cannot; the note says why."""
    need(cfg, "ledger", "target")
    note = (args.note or "").strip()
    if not note:
        raise refuse(
            "reject-needs-reason",
            cfg.repo,
            "--note is why the evidence did not hold: a rejection with no reason leaves the "
            "next worker with nothing",
            args,
        )
    answer = comment_flag(make_ikigai(cfg.target), cfg, args.item, note, "rejects")
    return show(answer)


def cmd_close(args, cfg: Config, make_ikigai) -> int:
    need(cfg, "ledger", "target")
    note = evidence_note(args, cfg)
    command = f"sink {cfg.resource('close')} item={args.item} reason={args.reason}"
    ikigai = make_ikigai(cfg.target)
    answer = ikigai.run(command, stdin=note)
    code = show(answer)
    held = read_claim(cfg)
    if answer.ok and held and held.get("item") == args.item:
        # A close does not end a claim in the ledger, and a machine's claim left on an
        # item that is no longer in flight is what the ledger's doctor reports as orphaned.
        code = show(ikigai.run(f"delete {cfg.resource('claim')} item={args.item}", stdin=""))
        if code == 0:
            cfg.claim_file.unlink(missing_ok=True)
    return code


def cmd_status(args, cfg: Config, make_ikigai) -> int:
    process = read_process(cfg.repo)
    names = ", ".join(process.get("process", [])) if process else None
    print(f"process: {names}" if names else "process: none named (no .gonk/process.toml)")
    need(cfg, "ledger", "target")
    allowed, why = check_claim(cfg, make_ikigai(cfg.target))
    print(("holds a live claim: " if allowed else "no live claim: ") + why)
    return 0 if allowed else 1


# --------------------------------------------------------------------------- the harness

HARNESS_PREFIX = "harness-"
TSV_TERM = re.compile(r'^"(.*)"(?:\^\^<([^>]*)>|@([A-Za-z-]+))?$|^<(.*)>$')
XSD_DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"


def fixed_datetime(text: str) -> str:
    """An `xsd:dateTime` at fixed width: `YYYY-MM-DDTHH:MM:SS.mmmZ`, UTC, as ikigai-log's own
    column is (`tools/harness.py`'s `stamp`).

    A store may write the same instant with or without the trailing zeros of its fraction
    (`…05:15:00.34Z` and `…05:15:00.340Z`), which changed the width of the coverage table's
    first and last columns between runs, so a checked transcript had to elide the row (ledger
    item 1480). A value that is not a date this module can read is left exactly as it came, and
    so is one with no `T` in it: that is not an xsd:dateTime whatever `fromisoformat` makes of
    it. A bare local time (no offset) is read as UTC, so the table is the same on every machine.
    """
    if "T" not in text:
        return text
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return text
    when = (when.replace(tzinfo=UTC) if when.tzinfo is None else when).astimezone(UTC)
    return when.strftime("%Y-%m-%dT%H:%M:%S") + f".{when.microsecond // 1000:03d}Z"


def query_header(text: str) -> dict[str, str]:
    """The `# QUESTION:` and `# EMPTY:` lines of a saved query's leading comment block."""
    found = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        m = re.match(r"^#\s*([A-Z]+):\s*(.+)$", line)
        if m:
            found[m.group(1)] = m.group(2).strip()
    return found


def graph_select(ikigai: Ikigai, query: str, ledger: str, graph: str) -> Answer:
    """Run a saved query over one named graph through gonk's store, as a published script's
    `:result` would: `LEDGER` replaced by the ledger, the comment lines dropped."""
    lines = [ln for ln in query.splitlines() if not ln.lstrip().startswith("#")]
    text = "\n".join(lines).replace(LEDGER_TOKEN, ledger)
    quoted = text.replace("\\", "\\\\").replace('"', '\\"')
    command = (
        f"source {PREFIX}iki:store:graph-select graph={graph} "
        f'as=text/tab-separated-values query="{quoted}"'
    )
    return ikigai.run(command)


def sensor_wiring(cfg: Config, graph, process: dict | None) -> dict[str, bool]:
    """Which of the graph's sensors are turned on in THIS working tree, by control id.

    A sensor whose fragment says `harness:wiredBy <part>` is on only where that part of the
    process is carried: the Claude Code hook where its settings are merged, the review hook
    where `core.hooksPath` points at it, the worker's sensors where this machine is configured
    to run a worker. Decided by the same checks `init` reports (ledger item 1480), so the graph
    can tell a sensor that is not wired from one that is wired and quiet.

    Only controls carrying `harness:wiredBy` are answered: a sensor without it is on wherever
    its code is, and needs no statement."""
    repo = cfg.repo
    parts = {
        "claude:require-claim": (process or {}).get("hooks", []),
        "git:post-commit": (process or {}).get("hooks", []),
        "worker-config": (process or {}).get("checks", []),
    }
    decides = {
        "claude:require-claim": hook_settings_on,
        "git:post-commit": post_commit_installed,
        "worker-config": worker_configured,
    }
    out: dict[str, bool] = {}
    for control in graph.of_type(harness.H + "Control"):
        for name in graph.values(control, harness.H + "wiredBy"):
            cid = control.value[len(harness.CONTROL_NS) :]
            # The part is carried only if the process names it, and it is on only if this
            # working tree shows it: a repository whose process does not carry the part is not
            # wired to it however its files look.
            decide = decides.get(name)
            out[cid] = bool(decide and name in parts[name] and decide(repo))
    return out


def wiring_triples(wiring: dict[str, bool]) -> list[tuple]:
    """The wiring this machine decided, as `harness:wired` statements about the controls."""
    return [
        (
            harness.I(harness.CONTROL_NS + cid),
            harness.I(harness.H + "wired"),
            harness.L("true" if on else "false", harness.XSD + "boolean"),
        )
        for cid, on in sorted(wiring.items())
    ]


def tsv_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """(columns, rows) of a tab-separated answer, each term shortened for a person to read.

    An `xsd:dateTime` is written at fixed precision (`fixed_datetime`), so a column of times is
    the same width whatever the store spelled."""
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("[")]
    if not lines:
        return [], []

    def short(term: str) -> str:
        m = TSV_TERM.match(term)
        if not m:
            return term
        value = m.group(1) if m.group(1) is not None else m.group(4)
        if m.group(2) == XSD_DATETIME:
            return fixed_datetime(value)
        value = value.replace('\\"', '"')
        for ns, alias in [
            (harness.CONTROL_NS, "ctl:"),
            (harness.GUIDE_NS, "guide:"),
            (harness.TEST_NS, "test:"),
        ]:
            if value.startswith(ns):
                return alias + value[len(ns) :]
        return value

    columns = [c.lstrip("?") for c in lines[0].split("\t")]
    return columns, [[short(t) for t in ln.split("\t")] for ln in lines[1:]]


def cmd_harness(args, cfg: Config, make_ikigai) -> int:
    """Load this repository's harness graph, and this working tree's firings, into gonk's named
    graph `urn:iki:harness:graph:{ledger}`, then ask the coverage questions
    (`.gonk/queries/harness-*.rq`) of it and print the answers.

    The load is ONE SPARQL UPDATE through `urn:iki:store:graph-update`: it replaces the graph's
    description of the controls and adds this tree's firings beside every firing another tree
    loaded (a firing's IRI is its log and sequence, so a second load adds nothing). Writing that
    graph needs `urn:cap:store:write:graph:<graph>` (and its read grant, for the update's
    WHERE), which gonk's owner-only socket holds and no ledger grant does: over QUIC a worker or
    a writer is refused and told so, and the questions are asked of what is already there.

    Beside the description it loads `harness:wired` for each sensor whose fragment names
    `harness:wiredBy`, decided here from this working tree's own files (`sensor_wiring`). That
    is the loading tree's wiring, like the description is the loading tree's graph: the load is
    the dispatcher's, from gonk's machine, and a sensor that is not wired there reads as *not
    wired* rather than as a rule that has landed (ledger item 1480)."""
    if harness is None:
        raise ConfigError("tools/harness.py is not beside tools/gonk.py")
    need(cfg, "ledger", "target")
    path = cfg.repo / harness.GRAPH_FILE
    if not path.is_file():
        raise ConfigError(
            f"no {harness.GRAPH_FILE}: a repository generated by ikigai-gonk-starter's "
            "tools/new.py carries its process's harness graph"
        )
    try:
        graph = harness.read_graph(path)
    except harness.TurtleError as e:
        raise ConfigError(str(e)) from e
    try:
        segment = harness.read_firings(cfg.repo)
    except harness.CorruptSegment as e:
        print(f"harness: the firing log is not loaded: {e}", file=sys.stderr)
        segment = None
    guides = len(graph.of_type(harness.H + "Guide"))
    controls = graph.of_type(harness.H + "Control")
    sensors = sum(
        1 for c in controls if harness.H + "feedback" in graph.values(c, harness.H + "direction")
    )
    firings = segment.firings if segment else []
    target = harness.graph_iri(cfg.ledger)
    other = len(controls) - sensors
    process = read_process(cfg.repo)
    # Which of these sensors are turned on in THIS working tree: the same checks `init`
    # reports, loaded with the graph so a coverage answer can tell a sensor that is not wired
    # from one that is wired and quiet (ledger item 1480).
    wiring = sensor_wiring(cfg, graph, process)
    print(
        f"harness: {guides} rule(s), {sensors} sensor(s) and {other} other control(s) in "
        f"{harness.GRAPH_FILE}; {len(firings)} firing(s) in this tree"
    )
    for cid, on in sorted(wiring.items()):
        print(f"  {'wired' if on else 'not wired'}: ctl:{cid}")
    ikigai = make_ikigai(cfg.target)
    failed = 0
    if not args.no_load:
        update = harness.load_update(
            target,
            graph.triples,
            [*wiring_triples(wiring), *(harness.firing_triples(segment) if segment else [])],
        )
        answer = ikigai.run(f"sink {PREFIX}iki:store:graph-update graph={target}", stdin=update)
        if answer.ok:
            print(f"loaded:  {answer.text()}")
        else:
            failed += 1
            print(f"not loaded into {target}: {answer.text()}", file=sys.stderr)
            print(
                f"  loading needs urn:cap:store:write:graph:{target} and its read grant, which "
                "gonk's owner-only socket holds and a ledger grant does not: load from gonk's "
                "machine, or ask gonk's operator to grant both",
                file=sys.stderr,
            )
    root = cfg.repo / cfg.layout.get("queries", {}).get("root", ".gonk/queries")
    for query in sorted(root.glob(f"{HARNESS_PREFIX}*.rq")):
        text = query.read_text(encoding="utf-8")
        about = query_header(text)
        print(f"\n{query.stem}: {about.get('QUESTION', '')}")
        answer = graph_select(ikigai, text, cfg.ledger, target)
        if not answer.ok:
            failed += 1
            print(f"  could not ask it: {answer.text()}")
            continue
        columns, rows = tsv_rows(answer.out)
        if not rows:
            print(f"  none. {about.get('EMPTY', '')}".rstrip())
            continue
        widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(columns)]
        print("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)).rstrip())
        for row in rows:
            print("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)).rstrip())
    return 1 if failed else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gonk.py", description=__doc__.splitlines()[0])
    p.add_argument("--repo", help="the repository (default: found from the working directory)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="write what the flags say, then report what is missing")
    s.add_argument("--ledger", help="the ledger this repository's work lives in")
    s.add_argument("--socket", help="gonk's owner-only socket on this machine")
    s.add_argument("--quic", help="a remote gonk's QUIC door, quic://host:port")
    s.add_argument("--cert-dir", help="this machine's client certificate and gonk's server.crt")
    s.add_argument("--holder", help="the name this working tree claims under")
    s.set_defaults(run=cmd_init)

    s = sub.add_parser("file", help="file a ledger item")
    s.add_argument("title")
    s.add_argument("--body")
    s.add_argument("--priority", type=int)
    s.add_argument("--labels")
    s.add_argument("--key", help="your own name for it: a second file with the key files nothing")
    s.set_defaults(run=cmd_file)

    s = sub.add_parser(
        "seed", help="file each open task of the changes in flight as a ledger item (idempotent)"
    )
    s.add_argument(
        "--dry-run", action="store_true", help="list what would be filed; ask gonk nothing"
    )
    s.set_defaults(run=cmd_seed)

    s = sub.add_parser("next", help="the ready set, ranked")
    s.set_defaults(run=cmd_next)

    s = sub.add_parser("claim", help="take an item, or renew this tree's lease on it")
    s.add_argument("item", type=int)
    s.add_argument("--lease", default=DEFAULT_LEASE)
    s.add_argument("--takeover-from", help="take over an EXPIRED lease from this holder")
    s.set_defaults(run=cmd_claim)

    s = sub.add_parser("release", help="hand an item back")
    s.add_argument("item", type=int, nargs="?")
    s.add_argument("--note", help="why, recorded as a comment")
    s.set_defaults(run=cmd_release)

    s = sub.add_parser("report", help="post the evidence and hand the item back (a worker's end)")
    s.add_argument("item", type=int)
    s.add_argument("--evidence", help="the URL that shows the work (required)")
    s.add_argument("--note")
    s.set_defaults(run=cmd_report)

    s = sub.add_parser(
        "reject", help="the evidence did not hold: the item is ready again (a dispatcher's act)"
    )
    s.add_argument("item", type=int)
    s.add_argument("--note", help="why the evidence did not hold (required)")
    s.set_defaults(run=cmd_reject)

    s = sub.add_parser("close", help="close an item, with evidence (the dispatcher's act)")
    s.add_argument("item", type=int)
    s.add_argument("--evidence", help="the URL that shows it is done (required)")
    s.add_argument("--reason", default="done", choices=CLOSE_REASONS)
    s.add_argument("--note")
    s.set_defaults(run=cmd_close)

    s = sub.add_parser("status", help="does this working tree hold a live claim?")
    s.set_defaults(run=cmd_status)

    s = sub.add_parser(
        "harness",
        help="load this repository's harness and this tree's firings into gonk; ask its coverage",
    )
    s.add_argument(
        "--no-load", action="store_true", help="ask the questions of what gonk already holds"
    )
    s.set_defaults(run=cmd_harness)
    return p


def main(argv: list[str] | None = None, make_ikigai=Ikigai) -> int:
    args = parser().parse_args(argv)
    args.argv = list(sys.argv[1:] if argv is None else argv)
    try:
        repo = Path(args.repo).resolve() if args.repo else find_repo(Path.cwd())
        if not (repo / ".gonk" / "layout.toml").is_file():
            raise ConfigError(f"{repo} has no .gonk/layout.toml")
        return args.run(args, load(repo), make_ikigai)
    except ConfigError as e:
        print(f"gonk.py: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
