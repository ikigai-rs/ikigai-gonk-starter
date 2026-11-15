#!/usr/bin/env python3
"""This repository's harness: its controls as a graph, and a log of every time one fires.

    python3 tools/harness.py check      does the graph agree with the code and with AGENTS.md?
    python3 tools/harness.py firings    what fired in this working tree, by control

The words are Birgitta Böckeler's ("Harness engineering for coding agent users",
martinfowler.com): a harness is CONTROLS. A **guide** acts before the agent does (a rule in
AGENTS.md); a **sensor** observes what was done and refuses, warns or reports (the claim hook,
`tools/gonk.py` refusing a report without evidence, the worker skipping a busy repository).
Each is **computational** (a program decides) or **inferential** (a model or a person decides),
and acts at a **stage** of a change's life.

THE GRAPH. `.gonk/harness/harness.ttl` describes every control this repository's process has,
assembled by `tools/new.py` from `profiles/harness/<section>.ttl`, one fragment per AGENTS.md
section, exactly as AGENTS.md is assembled from `profiles/sections/<section>.md`: a repository
describes the rules it follows and the sensors it carries, and nothing else. The terms are
`.gonk/harness/vocabulary.ttl`, `https://ikigai-rs.dev/ns/harness#` (`harness:`), and the form
an entry takes is `.gonk/harness/shapes.ttl`, closed SHACL shapes for a guide and a control,
which `check` validates the graph against (`validate` says which SHACL Core it reads). Every
control has a stable IRI, `urn:iki:harness:starter:control:{id}` (a guide `…:guide:{id}`), whose id
never changes: firings recorded under it stay joined to it. No blank nodes.

THE FIRING LOG. Every refusal or warning a sensor makes in this repository's own tools writes
one line to `.gonk/state/harness/firings.log` (git-ignored, this working tree's), in
ikigai-log's line grammar, so ikigai-log can read it and so can this file:

    2026-10-13T20:01:02.003Z harness:Refusal urn:iki:harness:starter:control:evidence-required
        seq=3 cmd=sha256:…          (one line in the file; folded here)

The file is one ikigai-log v1 segment, named `urn:log:harness:tree-{id}` by an id minted when
it is created (never a path), and UNSEALED: nothing here holds a key or knows which firing is
the last. Never in it: a command's text, a file's contents, a path, a person's name. `cmd=` is
the first 12 hex digits of the command's SHA-256, enough to tie repeated firings of one command
together.

★ BEST EFFORT, AND PROVABLY SO. `fire` never raises, never writes to standard output, and never
waits more than LOCK_WAIT seconds for the file. A failure is reported on standard error at most
once per process, and the decision it was recording is unchanged: tests/test_harness.py proves
it with an unwritable directory, a failing write and a corrupt segment.

HOOK RULES are sensors stated as data (`harness:HookRule`): the tools a rule applies to, a
pattern over the call's command or path in a stated dialect, refuse or warn, the sentence the
agent sees, the guide it enforces, and examples that must fire. The agent hook loads them with
`load_hook_rules` and applies them with `fire_hook_rules`; `check` runs every example and
counter-example as the rule's proof. A malformed rule is reported and ignored, never applied.

COVERAGE is asked of gonk, where SPARQL runs: `python3 tools/gonk.py harness` loads the graph,
the wiring of each sensor in this working tree (`harness:wired`) and the firings into the named
graph `urn:iki:harness:graph:{ledger}`, and asks the saved questions in
`.gonk/queries/harness-*.rq`. A sensor's fragment may say `harness:wiredBy <hook or check>`
(the part of the process that turns it on); the load records whether that part is on here, so
an answer can tell a sensor that is not wired from one that is wired and quiet.

THE TURTLE HERE is a subset, read by this file (standard library only, so no RDF library):
`@prefix` lines, statements with `;` and `,`, IRIs, prefixed names, `a`, one-line strings
(with `@lang` or `^^type`), integers and booleans. A blank node, a collection or a long string
is refused with its line number, never skipped.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:  # POSIX; on a platform without it the log is written unlocked (docs/windows.md: use WSL2)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

H = "https://ikigai-rs.dev/ns/harness#"
LOG = "https://ikigai-rs.dev/ns/log#"
PROV = "http://www.w3.org/ns/prov#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_TYPE = RDF + "type"
SH = "http://www.w3.org/ns/shacl#"

CONTROL_NS = "urn:iki:harness:starter:control:"
GUIDE_NS = "urn:iki:harness:starter:guide:"
TEST_NS = "urn:iki:harness:starter:test:"
SEGMENT_NS = "urn:log:harness:tree-"
INSTANCE_NS = "urn:iki:harness:tree:"
SESSION_NS = "urn:agents:session:"
GRAPH_NS = "urn:iki:harness:graph:"

GRAPH_FILE = Path(".gonk") / "harness" / "harness.ttl"
VOCAB_FILE = Path(".gonk") / "harness" / "vocabulary.ttl"
SHAPES_FILE = Path(".gonk") / "harness" / "shapes.ttl"
FIRINGS_FILE = Path(".gonk") / "state" / "harness" / "firings.log"

ID = re.compile(r"\A[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
BARE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")
KINDS = {"refusal": "harness:Refusal", "warning": "harness:Warning"}
VERSION_LINE = "# ikigai-log v1"
PREFIXES = (("harness", H), ("log", LOG))
LOCK_WAIT = 0.5
TAIL_BYTES = 8192
ENTRY = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z) (\S+) (\S+)((?: [A-Za-z]+=\S+)*)$")


# --------------------------------------------------------------------------- the firing log


def graph_iri(ledger: str) -> str:
    """The named graph a ledger's harness and firings are loaded into on gonk."""
    return GRAPH_NS + ledger


def digest(command: str) -> str:
    """`sha256:` and 12 hex digits: ties repeated firings of one command, never its text."""
    return "sha256:" + hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()[:12]


def stamp(now: float | None = None) -> str:
    """`YYYY-MM-DDTHH:MM:SS.mmmZ`, ikigai-log's fixed-width UTC column."""
    ms = int(round((time.time() if now is None else now) * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"


def canonical_header(tree: str, started: str) -> str:
    """The header as ikigai-log renders it, which is what its chain hashes: the version line,
    the prefixes sorted and aligned, then the five fields, then a blank line."""
    width = max(len(p) for p, _ in PREFIXES) + 1
    lines = [VERSION_LINE]
    lines += [f"@prefix {p + ':':<{width}} <{ns}> ." for p, ns in sorted(PREFIXES)]
    lines += [
        f"@name     {SEGMENT_NS}{tree}",
        f"@instance {INSTANCE_NS}{tree}",
        "@level    log:info",
        f"@started  {started}",
        "@prev     genesis",
    ]
    return "\n".join(lines) + "\n\n"


def header(tree: str, started: str) -> str:
    comment = [
        "# This working tree's harness firings: one line each time a sensor in this",
        "# repository's tools refused or warned. UNSEALED: no key, and no last line is",
        "# known. Written by tools/harness.py; `python3 tools/gonk.py harness` loads it.",
    ]
    return canonical_header(tree, started)[:-1] + "\n".join(comment) + "\n\n"


def entry_line(control: str, kind: str, seq: int, when: str, fields: dict[str, str]) -> str:
    words = [f"seq={seq}", *(f"{k}={v}" for k, v in fields.items())]
    return f"{when} {KINDS[kind]} {CONTROL_NS}{control} {' '.join(words)}"


class CorruptSegment(Exception):
    """The firing log is not one this file wrote whole: it is left exactly as it is."""


def _write(fd: int, data: bytes) -> int:
    """The one write. A seam for the failing-write test, nothing else."""
    return os.write(fd, data)


def _lock(fd: int) -> None:
    if fcntl is None:
        return
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"the firing log stayed locked for {LOCK_WAIT}s") from None
            time.sleep(0.005)


def _next_seq(fd: int, size: int) -> int:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.read(fd, len(VERSION_LINE) + 1) != (VERSION_LINE + "\n").encode():
        raise CorruptSegment(f"it does not begin with {VERSION_LINE!r}")
    start = max(0, size - TAIL_BYTES)
    os.lseek(fd, start, os.SEEK_SET)
    tail = os.read(fd, size - start)
    if not tail.endswith(b"\n"):
        raise CorruptSegment("its last line is torn")
    seqs = re.findall(r" seq=(\d+)(?: |$)", tail.decode("utf-8", "replace"), re.M)
    if seqs:
        return int(seqs[-1]) + 1
    if start > 0:
        raise CorruptSegment(f"no entry in its last {TAIL_BYTES} bytes")
    return 1


def clean_fields(tool: str | None, session: str | None, command: str | None) -> dict[str, str]:
    """The context a firing carries, each part only when it is a safe bare word."""
    out: dict[str, str] = {}
    if session:
        key = re.sub(r"[^A-Za-z0-9-]", "", session)[:64]
        if key:
            out["session"] = SESSION_NS + key
    if tool and BARE.match(tool):
        out["tool"] = tool
    if command:
        out["cmd"] = digest(command)
    return out


def _fire(control: str, kind: str, repo: Path, fields: dict[str, str]) -> None:
    if kind not in KINDS or not ID.match(control or ""):
        raise ValueError(f"not a control firing: {control!r} {kind!r}")
    path = Path(repo) / FIRINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        _lock(fd)
        size = os.fstat(fd).st_size
        when = stamp()
        if size == 0:
            seq, text = 1, header(secrets.token_hex(8), when)
        else:
            seq, text = _next_seq(fd, size), ""
        text += entry_line(control, kind, seq, when, fields) + "\n"
        _write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


_reported = False


def fire(
    control: str,
    kind: str,
    repo: Path | str | None,
    *,
    tool: str | None = None,
    session: str | None = None,
    command: str | None = None,
) -> None:
    """Record one firing of `control` ("refusal" or "warning") in `repo`'s firing log.

    NEVER raises and never writes to standard output: the decision being recorded is the
    caller's, and is the same whether or not this line lands. A failure is said on standard
    error once per process. `repo` None records nothing (no repository was found)."""
    global _reported
    if repo is None:
        return
    try:
        _fire(control, kind, Path(repo), clean_fields(tool, session, command))
    except Exception as e:  # noqa: BLE001 — best effort, and tests/test_harness.py proves it
        if not _reported:
            _reported = True
            try:
                sys.stderr.write(
                    f"harness: could not record {control} ({type(e).__name__}: {e}); "
                    "the decision is unaffected\n"
                )
            except Exception:  # noqa: BLE001
                pass


def sensor(control: str) -> str:
    """Marks a control id where a decision is made and recorded later: answers it unchanged.
    `check` reads the literal argument, as it reads `fire`'s."""
    return control


@dataclass
class Firing:
    when: str
    kind: str  # "refusal" or "warning"
    control: str  # the id
    seq: int
    fields: dict[str, str]


@dataclass
class Segment:
    name: str
    instance: str
    started: str
    firings: list[Firing] = field(default_factory=list)


def read_firings(repo: Path) -> Segment | None:
    """This working tree's firing log, or None when nothing has fired here. Raises
    CorruptSegment on a file this module did not write whole."""
    path = Path(repo) / FIRINGS_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith(VERSION_LINE + "\n"):
        raise CorruptSegment(f"{path}: it does not begin with {VERSION_LINE!r}")
    head, sep, body = text.partition("\n\n")
    if not sep:
        raise CorruptSegment(f"{path}: no blank line ends its header")
    values, prefixes = {}, {}
    for line in head.splitlines():
        m = re.match(r"^@prefix\s+(\w+):\s+<([^>]*)>\s*\.$", line)
        if m:
            prefixes[m.group(1)] = m.group(2)
        elif line.startswith("@"):
            key, _, value = line[1:].partition(" ")
            values[key] = value.strip()
    if not values.get("name", "").startswith(SEGMENT_NS):
        raise CorruptSegment(f"{path}: its @name is not a harness segment's")
    seg = Segment(values["name"], values.get("instance", ""), values.get("started", ""))
    for number, line in enumerate(body.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        m = ENTRY.match(line)
        if not m:
            raise CorruptSegment(f"{path}: entry line {number} is not one this module writes")
        when, curie, subject, rest = m.groups()
        cls = expand(curie, prefixes)
        kind = {H + "Refusal": "refusal", H + "Warning": "warning"}.get(cls)
        if kind is None or not subject.startswith(CONTROL_NS):
            continue  # an entry of another class: not a firing
        fields = dict(word.split("=", 1) for word in rest.split())
        seq = int(fields.pop("seq", "0"))
        seg.firings.append(Firing(when, kind, subject[len(CONTROL_NS) :], seq, fields))
    return seg


def expand(curie: str, prefixes: dict[str, str]) -> str:
    prefix, sep, local = curie.partition(":")
    return prefixes[prefix] + local if sep and prefix in prefixes else curie


def firing_triples(seg: Segment) -> list[tuple]:
    """The segment as ikigai-log transrepts it: each entry `urn:log:{segment}:{seq}`, typed by
    its class, with prov:startedAtTime, its control as log:subject and harness:firedControl,
    and one predicate per key (vocabulary.ttl's `log:keyName` bindings)."""
    seg_iri, inst = I(seg.name), I(seg.instance)
    out = [
        (seg_iri, I(RDF_TYPE), I(LOG + "Segment")),
        (seg_iri, I(LOG + "level"), I(LOG + "info")),
        (seg_iri, I(PROV + "startedAtTime"), L(seg.started, XSD + "dateTime")),
        (seg_iri, I(PROV + "wasAttributedTo"), inst),
        (seg_iri, I(LOG + "prevSeal"), L("genesis")),
        (inst, I(RDF_TYPE), I(LOG + "Instance")),
        (inst, I(RDF_TYPE), I(PROV + "SoftwareAgent")),
    ]
    for f in seg.firings:
        node, ctl = I(f"{seg.name}:{f.seq}"), I(CONTROL_NS + f.control)
        cls = "Refusal" if f.kind == "refusal" else "Warning"
        out += [
            (node, I(RDF_TYPE), I(H + cls)),
            (node, I(LOG + "segment"), seg_iri),
            (node, I(PROV + "startedAtTime"), L(f.when, XSD + "dateTime")),
            (node, I(PROV + "wasAssociatedWith"), inst),
            (node, I(LOG + "subject"), ctl),
            (node, I(H + "firedControl"), ctl),
            (node, I(LOG + "sequence"), L(str(f.seq), XSD + "integer")),
        ]
        if "session" in f.fields:
            out.append((node, I(H + "session"), I(f.fields["session"])))
        if "tool" in f.fields:
            out.append((node, I(H + "tool"), L(f.fields["tool"])))
        if "cmd" in f.fields:
            out.append((node, I(H + "commandDigest"), L(f.fields["cmd"])))
    return out


# --------------------------------------------------------------------------- the Turtle subset


@dataclass(frozen=True)
class I:  # noqa: E742 — an IRI term; one letter, as in the triples it fills
    value: str

    def nt(self) -> str:
        return f"<{self.value}>"


@dataclass(frozen=True)
class L:
    value: str
    datatype: str | None = None
    lang: str | None = None

    def nt(self) -> str:
        text = (
            self.value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        if self.lang:
            return f'"{text}"@{self.lang}'
        if self.datatype and self.datatype != XSD + "string":
            return f'"{text}"^^<{self.datatype}>'
        return f'"{text}"'


class TurtleError(Exception):
    pass


TOKEN = re.compile(
    r"""
    (?P<ws>[ \t\r]+) | (?P<nl>\n) | (?P<comment>\#[^\n]*)
  | (?P<long>\"\"\"|''') | (?P<squote>')
  | (?P<blank>_:|\[|\]) | (?P<coll>\(|\))
  | (?P<iri><[^<>"{}|^`\\\s]*>)
  | (?P<str>"(?:[^"\\\n]|\\["\\nrt])*")
  | (?P<at>@[A-Za-z]+(?:-[A-Za-z0-9]+)*)
  | (?P<dt>\^\^)
  | (?P<num>[+-]?\d+(?:[.eE][^\s;,]*)?)
  | (?P<pname>[A-Za-z][A-Za-z0-9_-]*:(?:[A-Za-z0-9_](?:[A-Za-z0-9_.:-]*[A-Za-z0-9_:-])?)?)
  | (?P<word>[A-Za-z][A-Za-z0-9_-]*)
  | (?P<punct>[;,.])
  | (?P<other>.)
""",
    re.X,
)


def tokens(text: str, where: str):
    line = 1
    for m in TOKEN.finditer(text):
        kind, val = m.lastgroup, m.group()
        if kind == "nl":
            line += 1
            continue
        if kind in ("ws", "comment"):
            continue
        if kind == "blank":
            raise TurtleError(f"{where}:{line}: a blank node ({val}): every node gets an IRI")
        if kind == "coll":
            raise TurtleError(f"{where}:{line}: a collection ({val}) is made of blank nodes")
        if kind in ("long", "squote"):
            raise TurtleError(f'{where}:{line}: {val} strings are outside the subset; use "…"')
        if kind == "num" and not re.fullmatch(r"[+-]?\d+", val):
            raise TurtleError(f"{where}:{line}: {val!r}: only integers are in the subset")
        if kind == "other":
            raise TurtleError(f"{where}:{line}: unexpected {val!r}")
        yield kind, val, line


def unescape(body: str) -> str:
    return re.sub(
        r"\\(.)", lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), body
    )


def parse_turtle(text: str, where: str = "<turtle>") -> tuple[list[tuple], dict[str, str]]:
    """(triples, prefixes) of a document in the subset; raises TurtleError naming the line."""
    toks = list(tokens(text, where))
    prefixes: dict[str, str] = {}
    triples: list[tuple] = []
    i = 0

    def at(k: int):
        return toks[k] if k < len(toks) else ("eof", "", toks[-1][2] if toks else 1)

    def term(k: int, position: str):
        kind, val, line = at(k)
        if kind == "iri":
            return I(val[1:-1]), k + 1
        if kind == "pname":
            prefix, _, local = val.partition(":")
            if prefix not in prefixes:
                raise TurtleError(f"{where}:{line}: undeclared prefix {prefix}:")
            return I(prefixes[prefix] + local), k + 1
        if kind == "word" and val == "a" and position == "p":
            return I(RDF_TYPE), k + 1
        if position == "o":
            if kind == "str":
                value, k = unescape(val[1:-1]), k + 1
                nk, nv, _ = at(k)
                if nk == "at":
                    return L(value, lang=nv[1:]), k + 1
                if nk == "dt":
                    dt, k = term(k + 1, "dt")
                    return L(value, datatype=dt.value), k
                return L(value), k
            if kind == "num":
                return L(str(int(val)), XSD + "integer"), k + 1
            if kind == "word" and val in ("true", "false"):
                return L(val, XSD + "boolean"), k + 1
        raise TurtleError(f"{where}:{line}: expected a {position} term, found {val!r}")

    while i < len(toks):
        kind, val, line = toks[i]
        if kind == "at" and val == "@prefix":
            pk, pv, _ = at(i + 1)
            ik, iv, _ = at(i + 2)
            dk, dv, _ = at(i + 3)
            if pk != "pname" or not pv.endswith(":") or ik != "iri" or (dk, dv) != ("punct", "."):
                raise TurtleError(f"{where}:{line}: @prefix <name>: <iri> .")
            prefixes[pv[:-1]] = iv[1:-1]
            i += 4
            continue
        if kind in ("at", "word") and val.lower() in ("@base", "base", "prefix"):
            raise TurtleError(f"{where}:{line}: {val} is outside the subset; use @prefix")
        subject, i = term(i, "s")
        while True:
            predicate, i = term(i, "p")
            while True:
                obj, i = term(i, "o")
                triples.append((subject, predicate, obj))
                if at(i)[:2] == ("punct", ","):
                    i += 1
                    continue
                break
            if at(i)[:2] == ("punct", ";"):
                i += 1
                if at(i)[:2] == ("punct", "."):
                    break
                continue
            break
        k, v, ln = at(i)
        if (k, v) != ("punct", "."):
            raise TurtleError(f"{where}:{ln}: a statement ends with `.`, found {v!r}")
        i += 1
    return triples, prefixes


class Graph:
    def __init__(self, triples: list[tuple]) -> None:
        self.triples = triples
        self.by_subject: dict[I, list[tuple[I, object]]] = {}
        for s, p, o in triples:
            self.by_subject.setdefault(s, []).append((p, o))

    def objects(self, s: I, p: str) -> list:
        return [o for pp, o in self.by_subject.get(s, []) if pp.value == p]

    def values(self, s: I, p: str) -> list[str]:
        return [o.value for o in self.objects(s, p)]

    def of_type(self, cls: str) -> list[I]:
        return [s for s, p, o in self.triples if p.value == RDF_TYPE and o == I(cls)]


def read_graph(path: Path) -> Graph:
    return Graph(parse_turtle(path.read_text(encoding="utf-8"), str(path))[0])


def insert_data(graph: str, triples: list[tuple]) -> str:
    lines = [f"    {s.nt()} {p.nt()} {o.nt()} ." for s, p, o in triples]
    return f"INSERT DATA {{ GRAPH <{graph}> {{\n" + "\n".join(lines) + "\n} }"


def load_update(graph: str, description: list[tuple], firings: list[tuple]) -> str:
    """One SPARQL UPDATE: replace the harness description in `graph`, keep every firing any
    working tree loaded before, and add this tree's. A firing's IRI is its segment and seq, so
    loading the same log twice adds nothing."""
    firing = f'STRSTARTS(STR(?s), "urn:log:harness:") || STRSTARTS(STR(?s), "{INSTANCE_NS}")'
    keep = f"FILTER(!({firing}))"
    drop = (
        f"DELETE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}\n"
        f"WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o {keep} }} }}"
    )
    return drop + " ;\n" + insert_data(graph, [*description, *firings])


# --------------------------------------------------------------------------- the shapes

# Why a private SHACL subset rather than a shared validator: the starter's tools are standard
# library only (README, "Python (standard library only)"), so no SHACL library can be a
# dependency here. Hosts that need the full standard use ikigai-shacl; this is the subset the
# harness shapes need and nothing more.
#
# The SHACL Core this file evaluates. A shape using any other sh: term is refused, never
# skipped, so a shape cannot say more than is checked.
SH_TERMS = {
    SH + t
    for t in (
        "targetClass closed property path minCount maxCount nodeKind datatype hasValue not message"
    ).split()
}
SH_KINDS = {SH + "IRI": I, SH + "Literal": L}


def curie(iri: str) -> str:
    for prefix, ns in (("harness", H), ("rdfs", RDFS), ("rdf", RDF)):
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns) :]}"
    return iri


def _datatype(o) -> str | None:
    if not isinstance(o, L):
        return None
    if o.lang:
        return RDF + "langString"
    return o.datatype or XSD + "string"


def _property_breaches(shapes: Graph, ps: I, g: Graph, focus: I) -> list[str]:
    """How the values of `focus` at the property shape `ps`'s path break it, as phrases."""
    paths = shapes.objects(ps, SH + "path")
    if len(paths) != 1 or not isinstance(paths[0], I):
        return [f"property shape {ps.value} has no single IRI sh:path"]
    path = paths[0].value
    values = g.objects(focus, path)
    out = []
    for n in shapes.values(ps, SH + "minCount"):
        if len(values) < int(n):
            out.append(f"{curie(path)}: has {len(values) or 'none'}, needs at least {n}")
    for n in shapes.values(ps, SH + "maxCount"):
        if len(values) > int(n):
            out.append(f"{curie(path)}: has {len(values)}, may have at most {n}")
    for kind in shapes.values(ps, SH + "nodeKind"):
        for v in values:
            if not isinstance(v, SH_KINDS[kind]):
                what = "an IRI" if kind == SH + "IRI" else "a literal"
                out.append(f"{curie(path)}: {v.value!r} is not {what}")
    for dt in shapes.values(ps, SH + "datatype"):
        for v in values:
            if _datatype(v) != dt:
                out.append(f"{curie(path)}: {v.value!r} is not of type {dt.rsplit('#', 1)[-1]}")
    missing = len(values) < min((int(n) for n in shapes.values(ps, SH + "minCount")), default=0)
    for want in shapes.objects(ps, SH + "hasValue"):
        if want not in values and not missing:  # a missing value is said once, as a count
            out.append(f"{curie(path)}: needs {curie(want.value)}")
    return out


def breaches(shapes: Graph, shape: I, g: Graph, focus: I) -> list[tuple[str, I | None]]:
    """Each way `focus` breaks the node shape `shape` (its targets aside): a phrase, and the
    property shape it came from (whose sh:message says it), or None for the node shape's own."""
    out: list[tuple[str, I | None]] = []
    allowed = set()
    for ps in shapes.objects(shape, SH + "property"):
        allowed.update(shapes.values(ps, SH + "path"))
        out += [(phrase, ps) for phrase in _property_breaches(shapes, ps, g, focus)]
    if shapes.values(shape, SH + "closed") == ["true"]:
        for p in dict.fromkeys(p for p, _ in g.by_subject.get(focus, [])):
            if p.value not in allowed:
                out.append(
                    (f"{curie(p.value)} is not a property a {_name(shapes, shape)} has", None)
                )
    for inner in shapes.objects(shape, SH + "not"):
        if not breaches(shapes, inner, g, focus):
            out.append((f"it is {_name(shapes, inner)}", None))
    return out


def _name(shapes: Graph, shape: I) -> str:
    return (shapes.values(shape, RDFS + "label") or [shape.value.rsplit("#", 1)[-1]])[0]


def shape_problems(shapes: Graph) -> list[str]:
    """The shapes graph's own faults: a sh: term this file does not evaluate, or a node kind
    other than sh:IRI or sh:Literal."""
    bad = []
    for s, p, o in shapes.triples:
        if p.value.startswith(SH) and p.value not in SH_TERMS:
            bad.append(
                f"{SHAPES_FILE}: {s.value} uses {p.value}, which tools/harness.py does not evaluate"
            )
        if p.value == SH + "nodeKind" and o.value not in SH_KINDS:
            bad.append(
                f"{SHAPES_FILE}: {s.value} has sh:nodeKind {o.value}; only sh:IRI or sh:Literal"
            )
    return bad


def validate(shapes: Graph, g: Graph) -> list[str]:
    """Validate the data graph `g` against `shapes`: one sentence per breach, naming the focus
    node, the shape's message (or its label) and each property that breaks it. Empty conforms.

    SHACL Core, the part a list-free, blank-node-free shapes graph needs: node shapes with
    sh:targetClass (instances by their asserted rdf:type; the data graph states no subclasses),
    sh:closed, sh:property, sh:not and sh:message; property shapes with an IRI sh:path,
    sh:minCount, sh:maxCount, sh:nodeKind (sh:IRI, sh:Literal), sh:datatype and sh:hasValue.
    Each breach is its own sentence, so a guide missing two fields gets two, each naming its
    property."""
    bad = shape_problems(shapes)
    if bad:
        return bad
    targeted = {s for s, p, _ in shapes.triples if p.value == SH + "targetClass"}
    for shape in sorted(targeted, key=lambda t: t.value):
        own = shapes.values(shape, SH + "message") or [f"a {_name(shapes, shape)} breaks its shape"]
        for cls in shapes.values(shape, SH + "targetClass"):
            for focus in sorted(set(g.of_type(cls)), key=lambda t: t.value):
                for phrase, ps in breaches(shapes, shape, g, focus):
                    said = (shapes.values(ps, SH + "message") if ps else []) or own
                    bad.append(f"{focus.value}: {said[0]} ({phrase})")
    return bad


# --------------------------------------------------------------------------- hook rules

# A hook rule is a sensor stated as data (harness:HookRule): the agent hook reads every one in
# the repository's graph when it starts, and applies each before the tool calls it names. The
# dialects and inputs below are the whole set this file reads: a rule naming any other is
# reported and ignored, never applied by a guess.
DIALECTS = {H + "pythonRe"}
INPUTS = {H + "commandText": "command", H + "filePath": "path"}
RULE_OUTCOMES = {H + "refuse": "refusal", H + "warn": "warning"}


@dataclass(frozen=True)
class HookRule:
    """One hook rule, loaded and proved: its pattern compiles and every example fires."""

    id: str
    tools: tuple[str, ...]
    over: str  # "command" or "path"
    pattern: re.Pattern
    kind: str  # "refusal" or "warning", the firing it records
    says: str
    examples: tuple[str, ...]
    counter_examples: tuple[str, ...]

    def fires(self, text: str) -> bool:
        return self.pattern.search(text) is not None


def _one(g: Graph, s: I, p: str, what: str, problems: list[str]) -> str | None:
    found = g.values(s, H + p)
    if len(found) != 1:
        problems.append(f"{s.value}: has {len(found) or 'no'} {what}; a hook rule has one")
        return None
    return found[0]


def hook_rule(g: Graph, s: I) -> tuple[HookRule | None, list[str]]:
    """The hook rule `s` describes, or None and why it cannot be applied, in sentences."""
    bad: list[str] = []
    cid = control_id(s) if s.value.startswith(CONTROL_NS) else None
    if cid is None or not ID.match(cid):
        bad.append(f"{s.value}: a hook rule is {CONTROL_NS}<kebab-id>")
    tools = tuple(g.values(s, H + "appliesTo"))
    if not tools:
        bad.append(f"{s.value}: names no tool it applies to (harness:appliesTo)")
    over = _one(g, s, "matchesOn", "harness:matchesOn", bad)
    if over is not None and over not in INPUTS:
        bad.append(
            f"{s.value}: matchesOn {over}, which is neither harness:commandText nor filePath"
        )
    dialect = _one(g, s, "patternDialect", "harness:patternDialect", bad)
    if dialect is not None and dialect not in DIALECTS:
        bad.append(f"{s.value}: its pattern is in {dialect}, a dialect this hook does not read")
    source = _one(g, s, "pattern", "harness:pattern", bad)
    pattern = None
    if source is not None:
        try:
            pattern = re.compile(source)
        except re.error as e:
            bad.append(f"{s.value}: its pattern is not a Python regular expression ({e})")
    outcome = _one(g, s, "outcome", "harness:outcome", bad)
    if outcome is not None and outcome not in RULE_OUTCOMES:
        bad.append(f"{s.value}: a hook rule refuses or warns, and its outcome is {outcome}")
    says = _one(g, s, "says", "harness:says", bad)
    examples = tuple(g.values(s, H + "example"))
    counter = tuple(g.values(s, H + "counterExample"))
    if not examples:
        bad.append(f"{s.value}: has no example that must fire (harness:example)")
    if pattern is not None:
        for text in examples:
            if not pattern.search(text):
                bad.append(f"{s.value}: its example {text!r} does not fire")
        for text in counter:
            if pattern.search(text):
                bad.append(f"{s.value}: its counter-example {text!r} fires")
    if bad:
        return None, bad
    rule = HookRule(
        cid, tools, INPUTS[over], pattern, RULE_OUTCOMES[outcome], says, examples, counter
    )
    return rule, []


def hook_rules(g: Graph) -> tuple[list[HookRule], list[str]]:
    """Every hook rule of `g` that can be applied, and one sentence for each problem of those
    that cannot. A malformed rule is ignored, never applied: its pattern may not compile, its
    dialect may be one this file does not read, or an example of it may not fire."""
    rules, bad = [], []
    for s in sorted(set(g.of_type(H + "HookRule")), key=lambda t: t.value):
        rule, problems = hook_rule(g, s)
        bad += problems
        if rule is not None:
            rules.append(rule)
    return rules, bad


def load_hook_rules(repo: Path) -> tuple[list[HookRule], list[str]]:
    """The hook rules of `repo`'s graph, and what was wrong with it. A repository with no
    graph has no rules and nothing to report; a graph that does not parse gives no rules and
    says why."""
    path = Path(repo) / GRAPH_FILE
    if not path.is_file():
        return [], []
    try:
        g = read_graph(path)
    except (OSError, UnicodeError, TurtleError) as e:
        return [], [f"{GRAPH_FILE} does not load, so no hook rule applies: {e}"]
    return hook_rules(g)


def rule_input(event: dict, over: str, repo: Path | None) -> str | None:
    """The part of a tool call a rule matches: its command, or its path (repository-relative
    with `/` when inside `repo`)."""
    tool_input = event.get("tool_input") or {}
    if over == "command":
        command = tool_input.get("command")
        return command if isinstance(command, str) else None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(event.get("cwd") or ".") / path
    path = Path(os.path.normpath(path))
    if repo is not None:
        try:
            return path.relative_to(Path(repo)).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def fire_hook_rules(
    rules: list[HookRule], event: dict, repo: Path | None
) -> tuple[list[HookRule], list[HookRule]]:
    """(refusals, warnings): the rules that fire on this tool call, each recorded as a firing
    (the command's digest, never its text; never the path)."""
    refused, warned = [], []
    tool = event.get("tool_name")
    for rule in rules:
        if tool not in rule.tools:
            continue
        text = rule_input(event, rule.over, repo)
        if text is None or not rule.fires(text):
            continue
        (refused if rule.kind == "refusal" else warned).append(rule)
        fire(
            rule.id,
            rule.kind,
            repo,
            tool=tool,
            session=event.get("session_id"),
            command=text if rule.over == "command" else None,
        )
    return refused, warned


# --------------------------------------------------------------------------- the check

# The `## ` headings of AGENTS.md that state no rule: the loop is a walk-through, and the
# optional hook's section describes a sensor (claude-hook.ttl describes it as one).
NON_RULE_HEADINGS = {"The loop", "Optional: a hook for Claude Code"}

# Calls whose FIRST positional argument is a control id. One whose id is an expression is
# allowed only inside a function that passes an id through: one of these, or a `fire_…`.
EMITTERS = {"fire", "refuse", "sensor"}
# Code is read from these directories; a sensor that writes firings lives in one of them.
CODE_ROOTS = ("tools", "hooks")


def control_id(iri: I) -> str | None:
    for ns in (CONTROL_NS, GUIDE_NS):
        if iri.value.startswith(ns):
            return iri.value[len(ns) :]
    return None


def is_local(where: str) -> bool:
    """`path[#name]` in this repository, rather than `owner/repo:path` in another."""
    return ":" not in where.split("#", 1)[0]


def process_parts(repo: Path) -> set[str]:
    """The parts of the process that can wire a sensor: the hooks and checks `.gonk/process.toml`
    names. Empty when the file is absent or unreadable, so `wiredBy` is then refused as naming
    no part this repository carries."""
    try:
        import tomllib

        with (repo / ".gonk" / "process.toml").open("rb") as f:
            process = tomllib.load(f)
    except (OSError, ValueError):
        return set()
    return {*process.get("hooks", []), *process.get("checks", [])}


def fires_into_log(g: Graph, s: I) -> bool:
    """A sensor of this repository's own code that refuses or warns: one that writes firings."""
    outcomes = set(g.values(s, H + "outcome"))
    local = [w for w in g.values(s, H + "enforcedAt") if is_local(w) and w.startswith(CODE_ROOTS)]
    return (
        H + "feedback" in g.values(s, H + "direction")
        and bool(outcomes & {H + "refuse", H + "warn"})
        and bool(local)
        and not g.values(s, H + "noFiringTest")
    )


def emissions(repo: Path) -> tuple[list[tuple[str, str, str]], list[str]]:
    """((file, function, control id) for every emitter call with a literal id, problems)."""
    found, bad = [], []
    for top in CODE_ROOTS:
        for path in sorted((repo / top).rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), rel)
            found_here, bad_here = _emitters_in(tree, rel)
            found += found_here
            bad += bad_here
    return found, bad


def _emitters_in(tree: ast.AST, rel: str):
    found, bad = [], []

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                walk(child, child.name)
                continue
            if isinstance(child, ast.Call):
                f = child.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name in EMITTERS and child.args:
                    first = child.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        found.append((rel, func, first.value))
                    elif func not in EMITTERS and not func.startswith("fire"):
                        bad.append(
                            f"{rel}:{child.lineno}: `{name}(…)` in {func} names its control with "
                            'an expression; name it with a literal id (or `sensor("id")`)'
                        )
            walk(child, func)

    walk(tree, "<module>")
    return found, bad


def defined_in(path: Path, name: str) -> bool:
    if name == "<module>":
        return True
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        pattern = rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b"
        return re.search(pattern, text, re.M) is not None
    if path.name == "justfile":
        return re.search(rf"^{re.escape(name)}\b[^:=\n]*:(?!=)", text, re.M) is not None
    return name in text


def check(repo: Path) -> list[str]:
    """Everything that disagrees between this repository's harness graph, its shapes, its
    vocabulary, its code and its AGENTS.md, as sentences. Empty is agreement."""
    bad: list[str] = []
    try:
        vocab = read_graph(repo / VOCAB_FILE)
        shapes = read_graph(repo / SHAPES_FILE)
        g = read_graph(repo / GRAPH_FILE)
    except (OSError, TurtleError) as e:
        return [str(e)]
    # The form of every entry: .gonk/harness/shapes.ttl's closed shapes (required fields,
    # how many, of what kind, and no property a guide or a control does not have).
    bad += validate(shapes, g)
    terms = {s.value for s, _, _ in vocab.triples if s.value.startswith(H)}
    # The parts of the process a sensor can be wired by: the hooks and checks
    # .gonk/process.toml names, which is what `harness:wiredBy` may cite.
    parts = process_parts(repo)
    for s, p, o in g.triples:
        for t in (s, p, o):
            if isinstance(t, I) and t.value.startswith(H) and t.value not in terms:
                bad.append(f"{GRAPH_FILE}: {t.value} is not a term of {VOCAB_FILE}")

    controls = set(g.of_type(H + "Control")) | set(g.of_type(H + "Guide"))
    guides = set(g.of_type(H + "Guide"))
    for s in sorted(controls, key=lambda t: t.value):
        cid = control_id(s)
        ns = GUIDE_NS if s in guides else CONTROL_NS
        if not s.value.startswith(ns) or cid is None or not ID.match(cid):
            bad.append(f"{s.value}: a {'guide' if s in guides else 'control'} is {ns}<kebab-id>")
        sensor_ = H + "feedback" in g.values(s, H + "direction")
        only_sensors = ("outcome", "noFiringTest", "example", "counterExample")
        if not sensor_ and any(g.values(s, H + p) for p in only_sensors):
            bad.append(f"{s.value}: only a sensor has an outcome, a noFiringTest or an example")
        for target in g.objects(s, H + "statedBy"):
            if target not in guides:
                bad.append(f"{s.value}: statedBy {target.value}, which is no guide here")
        for test in g.values(s, H + "firedBy"):
            if not test.startswith(TEST_NS):
                bad.append(f"{s.value}: firedBy {test} is not a test of this starter ({TEST_NS})")
        for part in g.values(s, H + "wiredBy"):
            if not sensor_:
                bad.append(f"{s.value}: only a sensor says what wires it (harness:wiredBy)")
            elif part not in parts:
                bad.append(
                    f"{s.value}: wiredBy {part!r}, which .gonk/process.toml does not carry "
                    f"({', '.join(sorted(parts)) or 'nothing'})"
                )
        for where in g.values(s, H + "enforcedAt"):
            if not is_local(where):
                continue
            path, _, name = where.partition("#")
            if not (repo / path).is_file():
                bad.append(f"{s.value}: enforcedAt {where}, and {path} is not in this repository")
            elif name and not defined_in(repo / path, name):
                bad.append(f"{s.value}: enforcedAt {where}, and {path} has no {name}")
        if fires_into_log(g, s) and not g.values(s, H + "firedBy"):
            bad.append(
                f"{s.value}: a sensor that writes firings names a test that fires it "
                "(harness:firedBy) or says why none can (harness:noFiringTest)"
            )

    # The code: every id it emits is a firing sensor described as enforced where it is
    # emitted, and every firing sensor is emitted everywhere it is said to be enforced.
    found, problems = emissions(repo)
    bad += problems
    emitted = {(f"{rel}#{func}", cid) for rel, func, cid in found}
    for rel, func, cid in found:
        s = I(CONTROL_NS + cid)
        if s not in controls:
            bad.append(f"{rel} ({func}) fires `{cid}`, which {GRAPH_FILE} does not describe")
        elif not fires_into_log(g, s):
            bad.append(f"{rel} ({func}) fires `{cid}`, which is not a sensor that refuses or warns")
        elif f"{rel}#{func}" not in g.values(s, H + "enforcedAt"):
            bad.append(f"{rel} ({func}) fires `{cid}`; its enforcedAt does not name {rel}#{func}")
    rules = set(g.of_type(H + "HookRule"))
    for s in controls:
        if not fires_into_log(g, s) or s in rules:
            continue  # a hook rule fires by its data (fire_hook_rules), not by a literal id
        for where in g.values(s, H + "enforcedAt"):
            path = where.split("#", 1)[0]
            code = is_local(where) and path.endswith(".py") and path.startswith(CODE_ROOTS)
            if code and (where, control_id(s)) not in emitted:
                bad.append(f"{s.value}: enforcedAt {where}, which fires no `{control_id(s)}`")

    # The hook rules: each loads, and each example fires and no counter-example does.
    bad += hook_rules(g)[1]

    # AGENTS.md: every guide cites a heading it has, and every rule heading has a guide.
    agents = repo / "AGENTS.md"
    if agents.is_file():
        lines = agents.read_text(encoding="utf-8").splitlines()
        headings = {ln[3:].strip() for ln in lines if ln.startswith("## ")}
        cited = set()
        for s in guides:
            for section in g.values(s, H + "section"):
                cited.add(section)
                if section not in headings:
                    bad.append(f"{s.value}: section {section!r} is not a heading of AGENTS.md")
        for heading in sorted(headings - cited - NON_RULE_HEADINGS):
            bad.append(f"AGENTS.md: `## {heading}` states rules no guide in {GRAPH_FILE} names")
    return bad


# --------------------------------------------------------------------------- the commands


def find_repo(start: Path) -> Path:
    here = start.resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".gonk" / "layout.toml").is_file():
            return candidate
    raise SystemExit(f"harness.py: no .gonk/layout.toml at or above {here}")


def cmd_check(repo: Path) -> int:
    problems = check(repo)
    for line in problems:
        print(line)
    g = read_graph(repo / GRAPH_FILE) if not problems else None
    if problems:
        print(f"\nharness check: {len(problems)} disagreement(s)")
        return 1
    guides = len(g.of_type(H + "Guide"))
    controls = len(g.of_type(H + "Control"))
    print(
        f"harness check: {guides} guide(s) and {controls} other control(s) conform to "
        f"{SHAPES_FILE.name} and agree with the code"
    )
    return 0


def cmd_firings(repo: Path) -> int:
    try:
        seg = read_firings(repo)
    except CorruptSegment as e:
        print(f"harness.py: {e}", file=sys.stderr)
        return 1
    if seg is None or not seg.firings:
        print(f"nothing has fired in this working tree ({FIRINGS_FILE} is absent or empty)")
        return 0
    counts: dict[tuple[str, str], list[str]] = {}
    for f in seg.firings:
        counts.setdefault((f.control, f.kind), []).append(f.when)
    for (control, kind), times in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(times):>5}  {kind:<8} {control:<36} last {max(times)}")
    print(f"\n{len(seg.firings)} firing(s) in {seg.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harness.py", description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["check", "firings"])
    p.add_argument("--repo", help="the repository (default: found from the working directory)")
    args = p.parse_args(argv)
    repo = Path(args.repo).resolve() if args.repo else find_repo(Path.cwd())
    return cmd_check(repo) if args.command == "check" else cmd_firings(repo)


if __name__ == "__main__":
    sys.exit(main())
