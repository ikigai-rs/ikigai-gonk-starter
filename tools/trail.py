#!/usr/bin/env python3
"""The session trail's writer: one `log:ToolCall` line per tool call an agent made, in
ikigai-log's segment format (ikigai-log 0.14.0, README "One tool-call line: an agent's trail").

    2026-11-21T23:04:17.512Z log:ToolCall urn:log:tool:name:Bash seq=9
        session=urn:claude:session:4f1c outcome=ok dur=184 args=sha256:9f86…   (one line)

Two places in this repository see tool calls and write them here: the Claude Code hook
(`hooks/claude/tool-trail.py`, and `require-claim.py` for the calls it refuses) and the worker
loop (`tools/worker.py`, from the agent's machine-readable events). Standard library only.

THE LINE is ikigai-log's `ToolCall::entry_with`, column for column: the subject is
`urn:log:tool:name:` and the tool's name percent-encoded as ikigai-log's `encode_text` does;
then `seq=` (the writer puts it first), `session=` the session's IRI, `outcome=` `ok`, `refused`
or `failed`, `dur=` milliseconds when known, `args=` the SHA-256 of the arguments as `sha256:`
and 64 lowercase hex digits, `result=` the same of the result when one was handed over. A value
is bare when it can be and quoted and escaped as ikigai-log's `render_value` does otherwise.

★ NO ARGUMENT OR RESULT TEXT BY DEFAULT. `args-text=` and `result-text=` are written only when
the operator opted in for the whole trail (`text=True`, from `[trail] text = true` in the
writer's configuration), as the Rust writer's `ToolCallLog::with_text()`; no single call can
turn it on. Opted-in text is cut at MAX_TOOL_TEXT bytes on a character boundary and ended with
`…`, and a cut is named in `cut=`; the digests always cover the whole text.

THE SEGMENT is an ikigai-log v1 segment like the harness firing log (tools/harness.py): a
canonical header with `@prev genesis`, entries with dense `seq=`, and UNSEALED. ikigai-log keeps
nothing per line on disk: its hash chain is computed on read from the header and the lines, and
an unsealed tail is `noted`, not `BROKEN`, by `urn:log:verify`. So this file writes a valid
segment without a key and without reimplementing the Rust writer's seals.

★ BEST EFFORT. `Trail.record` never raises and never writes to standard output: a hook or a
worker decides the same whether or not its line lands. A failure is said on standard error once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

try:  # POSIX; on a platform without it the trail is written unlocked (docs/windows.md: use WSL2)
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

LOG = "https://ikigai-rs.dev/ns/log#"
VERSION_LINE = "# ikigai-log v1"
TOOL_NS = "urn:log:tool:name:"
OUTCOMES = ("ok", "refused", "failed")
MAX_TOOL_TEXT = 4096  # ikigai_log::MAX_TOOL_TEXT
MAX_FIELD = 256  # ikigai_log::MAX_FIELD: the longest a subject is before it is cut
MAX_SESSION_LEN = 512  # ikigai_core::MAX_PRINCIPAL_LEN
CUT_MARKER = "\u2026"
LOCK_WAIT = 0.5
TAIL_BYTES = 65536  # more than one opted-in line can be (two 4 KiB texts, escaped)

# The session IRIs this repository's writers mint: the harness's own session id, after it.
CLAUDE_SESSION_NS = "urn:claude:session:"
CODEX_SESSION_NS = "urn:codex:session:"

ENTRY_HEAD = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z) (\S+) (\S+)")
SEQ_AT_HEAD = re.compile(rb"^\d{4}-\d\d-\d\dT[0-9:.]+Z \S+ \S+ seq=(\d+)(?: |$)", re.M)
SESSION_KEY = re.compile(r"[^A-Za-z0-9._-]")


# --------------------------------------------------------------------------- the line


def stamp(now: float | None = None) -> str:
    """`YYYY-MM-DDTHH:MM:SS.mmmZ`, ikigai-log's fixed-width UTC column."""
    ms = int(round((time.time() if now is None else now) * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"


def digest(text: str) -> str:
    """`sha256:` and the 64 lowercase hex digits of the UTF-8 text: ikigai-log's `tagged`."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def canonical_json(value) -> str:
    """The arguments (or result) as text, when the harness handed over a JSON value: sorted
    keys, no spaces, so equal arguments digest equally. A string is its own text."""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def encode_text(value: str) -> str:
    """ikigai-log's `encode_text`: every byte but A-Z a-z 0-9 and `-._~:/@!$'()*+,;`
    percent-encoded, upper-case hex. ASCII out."""
    keep = b"-._~:/@!$'()*+,;"
    out = []
    for byte in value.encode("utf-8", "surrogatepass"):
        if (chr(byte).isascii() and chr(byte).isalnum()) or byte in keep:
            out.append(chr(byte))
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def tool_iri(tool: str) -> str | None:
    """The subject column: TOOL_NS and the name, percent-encoded; None for an empty name or one
    ikigai-log would cut (a cut subject names no tool, so ikigai-log refuses it)."""
    iri = TOOL_NS + encode_text(tool)
    if not tool or len(iri) > MAX_FIELD:
        return None
    return iri


def is_bidi_format(c: str) -> bool:
    o = ord(c)
    return 0x202A <= o <= 0x202E or 0x2066 <= o <= 0x2069 or o in (0x200E, 0x200F, 0x061C)


def needs_escape(c: str) -> bool:
    """ikigai-log's `needs_escape`: a control character (Cc), U+2028, U+2029 or a bidi format."""
    return unicodedata.category(c) == "Cc" or c in "\u2028\u2029" or is_bidi_format(c)


def is_whitespace(c: str) -> bool:
    return c.isspace() or c in "\u0085\u200e\u200f\u2028\u2029"


def quote(v: str) -> str:
    """ikigai-log's `quote`: `"` and `\\` escaped, `\\n` `\\r` `\\t`, and `\\u{hex}` for every
    other character that `needs_escape`."""
    out = ['"']
    for c in v:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif needs_escape(c):
            out.append(f"\\u{{{ord(c):x}}}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def render_value(v: str) -> str:
    """Bare when it can be (non-empty, no quote, backslash, space or escape), else quoted."""
    bare = (
        v
        and '"' not in v
        and "\\" not in v
        and not any(is_whitespace(c) or needs_escape(c) for c in v)
    )
    return v if bare else quote(v)


def session_iri(namespace: str, session_id: str | None) -> str | None:
    """A session IRI a line can carry bare: `namespace` and the harness's session id, keeping
    only A-Z a-z 0-9 . _ - of it. None when nothing is left: no line is better than a guess."""
    key = SESSION_KEY.sub("", session_id or "")[:128]
    return namespace + key if key else None


def valid_session(iri: str) -> bool:
    return (
        bool(iri)
        and ":" in iri
        and len(iri.encode()) <= MAX_SESSION_LEN
        and render_value(iri) == iri
        and not any(c in iri for c in "<>")
    )


def cut_text(text: str) -> tuple[str, bool]:
    """Cut at MAX_TOOL_TEXT bytes on a character boundary and end with `…`; whether it cut."""
    raw = text.encode("utf-8", "surrogatepass")
    if len(raw) <= MAX_TOOL_TEXT:
        return text, False
    return raw[:MAX_TOOL_TEXT].decode("utf-8", "ignore") + CUT_MARKER, True


@dataclass
class Call:
    """One tool call as the host that saw it knew it, once it ended."""

    started: float  # seconds since the epoch, when the call began
    session: str  # the session's IRI
    tool: str
    arguments: str  # the arguments' text, as the tool received them (canonical_json)
    outcome: str  # ok, refused or failed
    duration_ms: int | None = None
    result: str | None = None


def fields_of(call: Call, text: bool) -> list[tuple[str, str]]:
    """The columns after `seq=`, in ikigai-log's `ToolCall::entry_with` order."""
    if call.outcome not in OUTCOMES:
        raise ValueError(f"not an outcome: {call.outcome!r}")
    if not valid_session(call.session):
        raise ValueError(f"not a session IRI a line can carry bare: {call.session!r}")
    out = [("session", call.session), ("outcome", call.outcome)]
    if call.duration_ms is not None:
        out.append(("dur", str(max(0, int(call.duration_ms)))))
    out.append(("args", digest(call.arguments)))
    if call.result is not None:
        out.append(("result", digest(call.result)))
    if text:
        cuts = []
        args, was_cut = cut_text(call.arguments)
        out.append(("args-text", args))
        if was_cut:
            cuts.append("args-text")
        if call.result is not None:
            result, was_cut = cut_text(call.result)
            out.append(("result-text", result))
            if was_cut:
                cuts.append("result-text")
        if cuts:
            out.append(("cut", ",".join(cuts)))
    return out


def entry_line(call: Call, seq: int, text: bool = False) -> str:
    subject = tool_iri(call.tool)
    if subject is None:
        raise ValueError(f"not a tool name a line can carry: {call.tool!r}")
    words = [f"seq={seq}", *(f"{k}={render_value(v)}" for k, v in fields_of(call, text))]
    return f"{stamp(call.started)} log:ToolCall {subject} {' '.join(words)}"


# --------------------------------------------------------------------------- the segment


def canonical_header(name: str, instance: str, started: str) -> str:
    """The header as ikigai-log renders it, which is what its chain hashes."""
    return (
        f"{VERSION_LINE}\n"
        f"@prefix log: <{LOG}> .\n"
        f"@name     {name}\n"
        f"@instance {instance}\n"
        "@level    log:info\n"
        f"@started  {started}\n"
        "@prev     genesis\n\n"
    )


class CorruptSegment(Exception):
    """The trail file is not one this module wrote whole: it is left exactly as it is."""


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
                raise TimeoutError(f"the trail stayed locked for {LOCK_WAIT}s") from None
            time.sleep(0.005)


def _next_seq(fd: int, size: int) -> int:
    """One more than the last entry's `seq=`, read at the head of a line (never from inside an
    opted-in text, which may hold ` seq=` of its own)."""
    os.lseek(fd, 0, os.SEEK_SET)
    if os.read(fd, len(VERSION_LINE) + 1) != (VERSION_LINE + "\n").encode():
        raise CorruptSegment(f"it does not begin with {VERSION_LINE!r}")
    start = max(0, size - TAIL_BYTES)
    os.lseek(fd, start, os.SEEK_SET)
    tail = os.read(fd, size - start)
    if not tail.endswith(b"\n"):
        raise CorruptSegment("its last line is torn")
    seqs = SEQ_AT_HEAD.findall(tail)
    if seqs:
        return int(seqs[-1]) + 1
    if start > 0:
        raise CorruptSegment(f"no entry in its last {TAIL_BYTES} bytes")
    return 1


@dataclass
class Trail:
    """One segment file of tool-call lines. `name_ns` and `instance_ns` are completed by an id
    minted when the file is created (never a path), as the harness firing log's are."""

    path: Path
    name_ns: str
    instance_ns: str
    text: bool = False  # the operator's opt-in to argument and result text
    _reported: bool = field(default=False, repr=False)

    def append(self, call: Call) -> int:
        """Write the call's line; its `seq`. Raises on any failure (`record` does not)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            _lock(fd)
            size = os.fstat(fd).st_size
            if size == 0:
                tree = secrets.token_hex(8)
                head = canonical_header(
                    self.name_ns + tree, self.instance_ns + tree, stamp(call.started)
                )
                seq = 1
            else:
                head, seq = "", _next_seq(fd, size)
            data = (head + entry_line(call, seq, self.text) + "\n").encode("utf-8")
            os.write(fd, data)
            return seq
        finally:
            os.close(fd)

    def record(self, call: Call) -> int | None:
        """Write the call's line, best effort: its `seq`, or None when it did not land."""
        try:
            return self.append(call)
        except Exception as e:  # noqa: BLE001  (a trail never changes what its host decides)
            if not self._reported:
                self._reported = True
                try:
                    sys.stderr.write(
                        f"trail: could not record a {call.tool!r} call ({type(e).__name__}: "
                        f"{e}); nothing else is affected\n"
                    )
                except Exception:  # noqa: BLE001
                    pass
            return None


# --------------------------------------------------------------------------- reading back


def unquote(token: str) -> str:
    out, i = [], 1
    while i < len(token) - 1:
        c = token[i]
        if c == "\\":
            nxt = token[i + 1]
            if nxt == "u":
                end = token.index("}", i)
                out.append(chr(int(token[i + 3 : end], 16)))
                i = end + 1
                continue
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_fields(rest: str) -> list[tuple[str, str]]:
    """`key=value` columns, a value bare or quoted, as ikigai-log's scanner reads them."""
    out, i = [], 0
    while i < len(rest):
        if rest[i] == " ":
            i += 1
            continue
        eq = rest.index("=", i)
        key = rest[i:eq]
        if eq + 1 < len(rest) and rest[eq + 1] == '"':
            j = eq + 2
            while rest[j] != '"':
                j += 2 if rest[j] == "\\" else 1
            out.append((key, unquote(rest[eq + 1 : j + 1])))
            i = j + 1
        else:
            j = rest.find(" ", eq)
            j = len(rest) if j < 0 else j
            out.append((key, rest[eq + 1 : j]))
            i = j
    return out


@dataclass
class Line:
    when: str
    tool: str  # the subject IRI
    fields: dict[str, str]

    @property
    def seq(self) -> int:
        return int(self.fields["seq"])


def read(path: Path) -> tuple[dict[str, str], list[Line]]:
    """The segment's header values and its `log:ToolCall` lines, in file order."""
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith(VERSION_LINE + "\n"):
        raise CorruptSegment(f"{path}: it does not begin with {VERSION_LINE!r}")
    head, sep, body = text.partition("\n\n")
    if not sep:
        raise CorruptSegment(f"{path}: no blank line ends its header")
    values = {}
    for line in head.splitlines():
        if line.startswith("@") and not line.startswith("@prefix"):
            key, _, value = line[1:].partition(" ")
            values[key] = value.strip()
    lines = []
    for raw in body.splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        m = ENTRY_HEAD.match(raw)
        if not m or m.group(2) != "log:ToolCall":
            continue
        lines.append(Line(m.group(1), m.group(3), dict(parse_fields(raw[m.end() :]))))
    return values, lines


def session_trail(path: Path, session: str) -> list[Line]:
    """One session's lines, in the order they were written (by `seq`)."""
    _, lines = read(path)
    return sorted((ln for ln in lines if ln.fields.get("session") == session), key=lambda x: x.seq)


# --------------------------------------------------------------------------- calls in flight


@dataclass
class Pending:
    tool: str
    arguments: str
    started: float  # time.time()
    clock: float  # time.monotonic()


class Calls:
    """Pairs a call's start with its end, for a host that sees both (the worker's reader):
    `start` when the agent asks for the tool, `end` when its result comes back, which writes the
    line with the time between them as `dur=`. A call that never ended is written by `close` as
    `failed`, with no `dur=`: it did not come back."""

    def __init__(self, trail: Trail, session_ns: str) -> None:
        self.trail, self.session_ns = trail, session_ns
        self.session: str | None = None
        self.open: dict[str, Pending] = {}
        self.order: list[str] = []
        self.written = 0

    def name_session(self, session_id: str | None) -> None:
        self.session = session_iri(self.session_ns, session_id) or self.session

    def start(self, call_id: str, tool: str, arguments) -> None:
        """The agent asked for `tool`: note it until its result comes back."""
        if not isinstance(call_id, str) or not call_id or call_id in self.open:
            return
        if not isinstance(tool, str) or tool_iri(tool) is None:
            return
        self.open[call_id] = Pending(tool, canonical_json(arguments), time.time(), time.monotonic())
        self.order.append(call_id)

    def end(self, call_id: str, outcome: str, result=None, timed: bool = True) -> None:
        """The call's result came back: write its line. `timed` False for a call whose start
        was not seen, so has no `dur=`."""
        p = self.open.pop(call_id, None) if isinstance(call_id, str) else None
        if p is None:
            return
        self.order.remove(call_id)
        dur = int((time.monotonic() - p.clock) * 1000) if timed else None
        self.write(p, outcome, dur, None if result is None else canonical_json(result))

    def write(self, p: Pending, outcome: str, dur: int | None, result: str | None) -> None:
        if self.session is None:
            return  # no session was named, so no trail can hold the line
        call = Call(p.started, self.session, p.tool, p.arguments, outcome, dur, result)
        if self.trail.record(call) is not None:
            self.written += 1

    def close(self) -> None:
        for call_id in list(self.order):
            self.write(self.open.pop(call_id), "failed", None, None)
        self.order.clear()


# --------------------------------------------------------------------------- a working tree's


TREE_TRAIL = Path(".gonk") / "state" / "trail" / "tools.log"
TREE_PENDING = Path(".gonk") / "state" / "trail" / "pending"
TREE_NAME_NS = "urn:log:tools:tree-"
TREE_INSTANCE_NS = "urn:iki:tools:tree:"
PENDING_KEEP = 86400  # a start no end came for (a call a person refused) is dropped after a day
CALL_ID = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def opted_in(config: Path) -> bool:
    """Whether `config`'s `[trail] text` is `true`: the operator's opt-in to argument and
    result text. A file missing, unreadable or saying anything else is off."""
    try:
        import tomllib

        with open(config, "rb") as f:
            table = tomllib.load(f).get("trail", {})
    except Exception:  # noqa: BLE001  (no readable opt-in is no opt-in)
        return False
    return isinstance(table, dict) and table.get("text") is True


def tree_trail(repo: Path) -> Trail:
    """This working tree's trail, `.gonk/state/trail/tools.log` (git-ignored), written by the
    Claude Code hook; text only when `.gonk/local.toml` says `[trail] text = true`."""
    repo = Path(repo)
    return Trail(
        repo / TREE_TRAIL,
        TREE_NAME_NS,
        TREE_INSTANCE_NS,
        text=opted_in(repo / ".gonk" / "local.toml"),
    )


def hook_call(event: dict, outcome: str, started: float, dur: int | None, result) -> Call | None:
    """The Call a Claude Code hook event describes, or None when it names no session or tool."""
    session = session_iri(CLAUDE_SESSION_NS, event.get("session_id"))
    tool = event.get("tool_name")
    if session is None or not isinstance(tool, str) or tool_iri(tool) is None:
        return None
    arguments = canonical_json(event.get("tool_input") or {})
    text = None if result is None else canonical_json(result)
    return Call(started, session, tool, arguments, outcome, dur, text)


def _pending_path(repo: Path, event: dict) -> Path | None:
    call_id = event.get("tool_use_id")
    if not isinstance(call_id, str) or not CALL_ID.match(call_id):
        return None
    return Path(repo) / TREE_PENDING / call_id


def hook_started(repo: Path, event: dict, now: float | None = None) -> None:
    """PreToolUse: note when the call began, for its end's `dur=`. Best effort."""
    now = time.time() if now is None else now
    try:
        path = _pending_path(repo, event)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{now:.3f}\n", encoding="utf-8")
        for old in path.parent.iterdir():
            if now - old.stat().st_mtime > PENDING_KEEP:
                old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def hook_take_start(repo: Path, event: dict) -> float | None:
    """When the call began, as PreToolUse noted it, forgetting the note; None when not noted."""
    try:
        path = _pending_path(repo, event)
        if path is None or not path.is_file():
            return None
        started = float(path.read_text(encoding="utf-8").strip())
        path.unlink(missing_ok=True)
        return started
    except Exception:  # noqa: BLE001
        return None


def hook_ended(repo: Path, event: dict, outcome: str, now: float | None = None) -> int | None:
    """A call's end, as a hook saw it: PostToolUse (`ok`, with the tool's response as the
    result), PostToolUseFailure (`failed`, with its error) or a refusal by a PreToolUse hook
    (`refused`, no result). Writes the line; its seq, or None. Never raises."""
    try:
        now = time.time() if now is None else now
        started = hook_take_start(repo, event)
        given = event.get("duration_ms")
        if isinstance(given, int) and not isinstance(given, bool) and given >= 0:
            dur = given
        elif started is not None:
            dur = int(round((now - started) * 1000))
        else:
            dur = None
        if outcome == "ok":
            result = event.get("tool_response")
        elif outcome == "failed":
            result = event.get("error")
        else:
            result = None
        call = hook_call(event, outcome, started if started is not None else now, dur, result)
        if call is None:
            return None
        return tree_trail(repo).record(call)
    except Exception:  # noqa: BLE001
        return None
