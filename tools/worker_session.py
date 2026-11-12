"""A worker's session summary (ledger item 3023): the raw, content-free facts of one agent run,
read from the harness's own machine-readable output and recorded on the item's hand-back.

The ledger defines the summary (ikigai-ledger #3022, vocabulary 0.22.0): one JSON object sent to
`urn:iki:ledger:{ledger}:item:{id}:sessions` after the hand-back comment, checked against the
closed `ledger:SessionSummaryShape`. `SHAPE` below is that shape, key for key, so a summary the
ledger would refuse is caught here, before anything is sent, and the hand-back says why.

    Claude Code  `claude -p --output-format json` (or stream-json): the final `result` object's
                 session_id, duration_ms, num_turns, usage and is_error, and the model from
                 `modelUsage` (or stream-json's init event). Its cost is never read.
    Codex        `codex exec --json`: the thread (or, in older releases, session) id, the turn
                 and item events, and the token usage events.

RAW facts only: counts and names. No transcript text, prompt, tool result or file content
leaves the worker, nothing is copied anywhere, and no cost or price is read or sent. The agent's
own words still go to the item's log on this machine, as they did before (`Reader`).
"""

from __future__ import annotations

import json
import os
import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# The harnesses a session summary records: `ledger:harness sh:in ( "claude" "codex" )`.
HARNESSES = ("claude", "codex")

# `ledger:SessionSummaryShape`, as the worker sends it: each key is the `ledger:` property of
# the same name, and `session` becomes `ledger:sessionId`. (key, kind, required). The shape is
# closed: any other key (a transcript, a prompt, a cost) is refused.
SHAPE: tuple[tuple[str, str, bool], ...] = (
    ("session", "id", True),
    ("machineName", "string", True),
    ("harness", "harness", True),
    ("model", "string", True),
    ("exitStatus", "integer", True),
    ("wallClockMs", "count", True),
    ("turns", "count", False),
    ("toolCalls", "count", False),
    ("inputTokens", "count", False),
    ("cachedInputTokens", "count", False),
    ("cacheCreationTokens", "count", False),
    ("outputTokens", "count", False),
)
SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Codex's tool calls, by the item (`codex exec --json`) or event (older releases) type.
CODEX_TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search")
CODEX_TOOL_EVENTS = (
    "exec_command_begin",
    "patch_apply_begin",
    "mcp_tool_call_begin",
    "web_search_begin",
)


def harness_of(command: list[str], configured: str | None = None) -> str | None:
    """The harness a command runs: `[agent] harness` when set, else the program's own name."""
    if configured:
        return configured
    name = Path(command[0]).name if command else ""
    return name if name in HARNESSES else None


def has_flag(argv: list[str], name: str) -> bool:
    return any(word == name or word.startswith(name + "=") for word in argv)


def flag_value(argv: list[str], *names: str) -> str | None:
    """The value of the last `--name value` or `--name=value` among `names`, or None."""
    found = None
    for i, word in enumerate(argv):
        for name in names:
            if word == name and i + 1 < len(argv):
                found = argv[i + 1]
            elif word.startswith(name + "="):
                found = word[len(name) + 1 :]
    return found


def with_json_output(harness: str | None, argv: list[str]) -> list[str]:
    """The command asking its harness for machine-readable output, when it runs that harness by
    name and does not ask already: `claude --output-format json`, `codex exec --json`. A command
    of one's own (a wrapper, with `[agent] harness` set) is never rewritten: it asks itself."""
    if not argv or Path(argv[0]).name != harness:
        return argv
    if harness == "claude" and not has_flag(argv, "--output-format"):
        return [argv[0], "--output-format", "json", *argv[1:]]
    if harness == "codex" and "exec" in argv:
        if has_flag(argv, "--json") or has_flag(argv, "--experimental-json"):
            return argv
        at = argv.index("exec") + 1
        return [*argv[:at], "--json", *argv[at:]]
    return argv


def count(value) -> int | None:
    """A whole number of 0 or more, or None: a bool or a float is not a count."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def codex_config_model(env: dict[str, str] | None = None) -> str | None:
    """The `model` in Codex's own config.toml ($CODEX_HOME, else ~/.codex), or None."""
    env = os.environ if env is None else env
    home = env.get("CODEX_HOME") or str(Path(env.get("HOME", str(Path.home()))) / ".codex")
    try:
        with open(Path(home) / "config.toml", "rb") as f:
            model = tomllib.load(f).get("model")
    except (OSError, ValueError):
        return None
    return model if isinstance(model, str) and model else None


def argv_model(harness: str, argv: list[str]) -> str | None:
    """The model a command names: `--model`, codex's `-m`, or codex's `-c model=…`."""
    names = ("--model", "-m") if harness == "codex" else ("--model",)
    model = flag_value(argv, *names)
    if harness == "codex":
        for i, word in enumerate(argv):
            if word in ("-c", "--config") and i + 1 < len(argv):
                key, _, value = argv[i + 1].partition("=")
                if key.strip() == "model" and value.strip():
                    model = value.strip().strip("\"'")
    return model or None


class Collector:
    """Reads one harness's output, line by line: each JSON event is kept as facts, and the
    agent's own words are handed back for the log. `problems` says what did not parse."""

    harness = ""

    def __init__(self) -> None:
        self.events = 0
        self.problems: list[str] = []

    def feed(self, line: str) -> str | None:
        """The text for the log from one line of output: the line itself when it is not a JSON
        event (a warning on stderr), the agent's words from one that is, or None."""
        stripped = line.strip()
        if not stripped.startswith("{"):
            return line if stripped else None
        try:
            event = json.loads(stripped)
        except ValueError as e:
            self.problems.append(f"a line of its output did not parse as JSON ({e})")
            return line
        if not isinstance(event, dict):
            return line
        self.events += 1
        return self.take(event)

    def take(self, event: dict) -> str | None:  # pragma: no cover  (each harness has its own)
        raise NotImplementedError

    def facts(self) -> tuple[dict, str | None]:  # pragma: no cover
        raise NotImplementedError


class ClaudeCollector(Collector):
    """`claude -p --output-format json` prints one `result` object when it ends;
    `--output-format stream-json` prints events, ending with the same object."""

    harness = "claude"

    def __init__(self) -> None:
        super().__init__()
        self.result: dict | None = None
        self.init_session: str | None = None
        self.init_model: str | None = None
        self.tool_calls = 0
        self.streamed = False

    def take(self, event: dict) -> str | None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.init_session = event.get("session_id") or self.init_session
            self.init_model = event.get("model") or self.init_model
        elif kind == "assistant":
            self.streamed = True
            content = (event.get("message") or {}).get("content") or []
            self.tool_calls += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
        elif kind == "result":
            self.result = event
            text = event.get("result")
            if isinstance(text, str) and text:
                return text if text.endswith("\n") else text + "\n"
        return None

    def model(self) -> str | None:
        """The model the session ran: stream-json's init event, else the one `modelUsage` names
        with the most tokens (only its keys and token counts are read, never its cost)."""
        if self.init_model:
            return self.init_model
        usage = (self.result or {}).get("modelUsage")
        if not isinstance(usage, dict) or not usage:
            return None

        def tokens(name: str) -> int:
            entry = usage.get(name)
            if not isinstance(entry, dict):
                return 0
            keys = ("inputTokens", "outputTokens", "cacheReadInputTokens")
            return sum(count(entry.get(k)) or 0 for k in keys)

        return max(sorted(usage), key=tokens)

    def facts(self) -> tuple[dict, str | None]:
        if self.result is None:
            if self.problems:
                return {}, f"claude's output did not parse: {self.problems[0]}"
            return {}, "claude wrote no result object (run it with `--output-format json`)"
        r = self.result
        usage = r.get("usage") if isinstance(r.get("usage"), dict) else {}
        facts = {
            "session": r.get("session_id") or self.init_session,
            "model": self.model(),
            "wallClockMs": count(r.get("duration_ms")),
            "turns": count(r.get("num_turns")),
            "inputTokens": count(usage.get("input_tokens")),
            "cachedInputTokens": count(usage.get("cache_read_input_tokens")),
            "cacheCreationTokens": count(usage.get("cache_creation_input_tokens")),
            "outputTokens": count(usage.get("output_tokens")),
        }
        if self.streamed:
            facts["toolCalls"] = self.tool_calls
        return facts, None

    @property
    def is_error(self) -> bool:
        return bool((self.result or {}).get("is_error"))


class CodexCollector(Collector):
    """`codex exec --json` prints one event per line: `thread.started`, `turn.started`,
    `item.completed`, `turn.completed` (with its usage). Older releases print `{"id", "msg"}`
    events: `session_configured`, `token_count`, `agent_message`, `task_started`. Both read."""

    harness = "codex"

    def __init__(self) -> None:
        super().__init__()
        self.session: str | None = None
        self.model: str | None = None
        self.turns = 0
        self.tool_calls = 0
        self.turn_usage: dict[str, int] | None = None  # summed over turn.completed
        self.total_usage: dict | None = None  # the last token_count's running total
        self.failed = False

    def add_usage(self, usage: dict) -> None:
        self.turn_usage = self.turn_usage or {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            n = count(usage.get(key))
            if n is not None:
                self.turn_usage[key] = self.turn_usage.get(key, 0) + n

    def take(self, event: dict) -> str | None:
        kind = event.get("type")
        if kind == "thread.started":
            self.session = event.get("thread_id") or self.session
        elif kind == "turn.started":
            self.turns += 1
        elif kind == "turn.completed":
            if isinstance(event.get("usage"), dict):
                self.add_usage(event["usage"])
        elif kind == "turn.failed":
            self.failed = True
            message = (event.get("error") or {}).get("message")
            return f"codex: the turn failed: {message}\n" if message else None
        elif kind == "error":
            message = event.get("message")
            return f"codex: {message}\n" if message else None
        elif kind == "item.completed":
            item = event.get("item") or {}
            kind = item.get("type") or item.get("item_type")
            if kind in CODEX_TOOL_ITEMS:
                self.tool_calls += 1
            elif kind in ("agent_message", "assistant_message"):
                text = item.get("text")
                if isinstance(text, str) and text:
                    return text if text.endswith("\n") else text + "\n"
        if isinstance(event.get("model"), str):
            self.model = event["model"]
        msg = event.get("msg")
        if isinstance(msg, dict):
            return self.take_msg(msg)
        return None

    def take_msg(self, msg: dict) -> str | None:
        kind = msg.get("type")
        if kind == "session_configured":
            self.session = msg.get("session_id") or self.session
            self.model = msg.get("model") or self.model
        elif kind == "task_started":
            self.turns += 1
        elif kind == "token_count":
            total = (msg.get("info") or {}).get("total_token_usage")
            if isinstance(total, dict):
                self.total_usage = total
        elif kind in CODEX_TOOL_EVENTS:
            self.tool_calls += 1
        elif kind == "agent_message":
            text = msg.get("message")
            if isinstance(text, str) and text:
                return text if text.endswith("\n") else text + "\n"
        return None

    def facts(self) -> tuple[dict, str | None]:
        if self.session is None:
            if self.problems:
                return {}, f"codex's output did not parse: {self.problems[0]}"
            if not self.events:
                return {}, "codex wrote no JSON events (run it as `codex exec --json`)"
            return {}, "codex's events named no thread or session id"
        usage = self.turn_usage if self.turn_usage is not None else (self.total_usage or {})
        given = count(usage.get("input_tokens"))
        cached = count(usage.get("cached_input_tokens"))
        # Codex counts cached input inside its input tokens; the ledger's inputTokens is the
        # uncached part, and cachedInputTokens the rest.
        uncached = given - min(cached or 0, given) if given is not None else None
        facts = {
            "session": self.session,
            "model": self.model,
            "turns": self.turns if self.events else None,
            "toolCalls": self.tool_calls,
            "inputTokens": uncached,
            "cachedInputTokens": cached,
            "outputTokens": count(usage.get("output_tokens")),
        }
        return facts, None

    @property
    def is_error(self) -> bool:
        return self.failed


def collector(harness: str | None) -> Collector | None:
    return {"claude": ClaudeCollector, "codex": CodexCollector}.get(harness or "", lambda: None)()


def shape_problem(summary: dict) -> str | None:
    """Why `ledger:SessionSummaryShape` would refuse `summary`, in a few words, or None."""
    known = {key for key, _, _ in SHAPE}
    for key in summary:
        if key not in known:
            return f"`{key}` is not a key of a session summary, whose shape is closed"
    for key, kind, required in SHAPE:
        value = summary.get(key)
        if value is None:
            if required:
                return f"it has no {key}"
            continue
        if kind == "id" and not (isinstance(value, str) and SESSION_ID.match(value)):
            return f"its session id {value!r} is not 1 to 128 of A-Z a-z 0-9 - _ ."
        if kind in ("string", "harness") and not (isinstance(value, str) and value):
            return f"its {key} is not a string"
        if kind == "harness" and value not in HARNESSES:
            return f"its harness {value!r} is not one of {', '.join(HARNESSES)}"
        if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return f"its {key} is not a whole number"
        if kind == "count" and count(value) is None:
            return f"its {key} is not a whole number of 0 or more"
    return None


@dataclass
class Session:
    """What one run's hand-back says about its session: the summary to record, or why none."""

    harness: str | None
    summary: dict | None = None
    why: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def line(self) -> str:
        """The one line the hand-back carries."""
        if self.summary is None:
            return f"No session summary: {self.why}."
        s = self.summary
        extra = "".join(f"; {note}" for note in self.notes)
        return (
            f"Session summary: {s['harness']} session {s['session']} ({s['model']}, exit "
            f"{s['exitStatus']}{extra}), sent to the item's sessions after this hand-back."
        )


def summarize(
    harness: str | None,
    seen: Collector | None,
    argv: list[str],
    exit_status: int | None,
    wall_ms: int,
    machine: str,
    env: dict[str, str] | None = None,
) -> Session:
    """The session summary of a run that ended by itself, or why there is none. It never
    raises: a summary that cannot be made is a line on the hand-back, never a failed run."""
    if harness is None or seen is None:
        program = Path(argv[0]).name if argv else "the agent"
        return Session(
            None,
            why=f"`{program}` is not {' or '.join(HARNESSES)}, the harnesses a summary records",
        )
    try:
        facts, why = seen.facts()
    except Exception as e:  # noqa: BLE001  (a summary never fails the run)
        facts, why = {}, f"its output could not be read: {type(e).__name__}: {e}"
    if why:
        return Session(harness, why=why)
    model = facts.get("model") or argv_model(harness, argv)
    if not model and harness == "codex":
        model = codex_config_model(env)
    summary = {
        "session": facts.get("session"),
        "machineName": machine,
        "harness": harness,
        "model": model,
        "exitStatus": exit_status,
        "wallClockMs": facts.get("wallClockMs")
        if facts.get("wallClockMs") is not None
        else max(0, wall_ms),
        **{k: v for k, v in facts.items() if k not in ("session", "model", "wallClockMs")},
    }
    summary = {k: v for k, v in summary.items() if v is not None}
    problem = shape_problem(summary)
    if problem:
        return Session(harness, why=f"it does not fit ledger:SessionSummaryShape: {problem}")
    notes = [f"{harness} reported an error"] if seen.is_error else []
    return Session(harness, summary=summary, notes=notes)


class Reader(threading.Thread):
    """Reads the agent's combined output, feeding each line to the collector and writing what
    the log keeps: the agent's own words, and every line that is not a JSON event."""

    def __init__(self, stream, log, seen: Collector) -> None:
        super().__init__(daemon=True, name="agent-output")
        self.stream, self.log, self.seen = stream, log, seen

    def run(self) -> None:
        for raw in self.stream:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            try:
                text = self.seen.feed(line)
            except Exception as e:  # noqa: BLE001  (a summary never fails the run)
                self.seen.problems.append(f"{type(e).__name__}: {e}")
                text = line
            if text:
                self.log.write(text)
                self.log.flush()
