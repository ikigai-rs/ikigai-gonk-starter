"""`worker.py onboard`: a node enrolls itself with a pairing code, then configures itself.

Standard library only, beside tools/worker.py; the `ikigai` command line is the one thing it
runs, to make the certificate and to speak QUIC to the layer door, as tools/gonk.py does.

    python3 tools/worker.py onboard --door door.example.org:4434 --pin <sha256> \\
        --server-cert door.crt --code <pairing code> --invite <id> [--name mini] [--once]

The controller's `onboard invite <name> --kind <kind>` prints the code, the invite id, the door
and the door's server-certificate fingerprint (ledger item 2009). In order, this:

1. Makes this node's certificate HERE (`ikigai cert generate`), in its identity directory
   (default `~/.ikigai/onboard/<name>`, mode 0700). Only `client.crt` and `client.key` are kept,
   moved, never copied; the key is never printed. A rerun reuses the certificate it finds.
2. Pins the door: the door's `server.crt` (from `--server-cert`, or the one a run before kept)
   must hash to `--pin`, or nothing is posted. The identity directory is then a pinned cert set
   for the door, which the `ikigai` command line verifies at every handshake.
3. Posts the `onb:EnrollmentRequest` (`request_turtle`) to `urn:layer:enroll:say`. Its proof is
   HMAC-SHA256 with the pairing code's UTF-8 bytes as the key and the fingerprint's ASCII as the
   message (`proof`), so only a holder of the code could have made it; the code is never stored.
4. Polls the `grants` layer's entries for an `onb:Entitlement` naming this fingerprint, with
   exponential backoff (`--max-wait`, default 24 h, the invite's lifetime), or once (`--once`,
   exit 3 while not enrolled). Until the door enrolls this certificate it refuses the read, and
   that refusal is "not enrolled yet".
5. On an entitlement, writes this node's configuration, never replacing a file that differs
   without `--force`:
   - the config-home `mount` lines for the layer door (`<identity>/config-home.toml`, to copy
     into the config home's config.toml: a config home is inherited by every ikigai server on
     the machine, so this never edits it);
   - for a gonk grant (`worker` kind): `.gonk/worker-<agent>.toml` per agent, through
     init-agents' own code, with the entitlement's routers; and `.gonk/local.toml` when
     `--gonk` and gonk's server certificate are given;
   - for a layer grant (`task-member` kind): the layers client's cert set,
     `~/.ikigai/layers-client/<name>`, a link to the identity directory;
   - then prints what to start: the launchd or systemd lines `worker.py plist` writes.
6. Is idempotent: `<identity>/onboard.json` records the request it posted, so a rerun with the
   same certificate and invite resumes polling and posts nothing.

Exit codes: 0 enrolled and configured; 1 the door refused the request, a configuration was
kept because it differs, or the entitlement was revoked; 2 a problem on this side (named);
3 not enrolled yet.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ONB = "https://ikigai-rs.dev/ns/onboard#"
ENROLLED, ERROR, CONFIG, NOT_YET = 0, 1, 2, 3

# One resource family for both halves: the layer door hosts `enroll`, `grants` and the task
# layers, and these three prefixes are what the layers client and `ikigai layers` route there.
LAYER_PREFIXES = "urn:layer:,urn:layers:,urn:collab:"

NAME_SHAPE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")  # a participant short name
INVITE_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DOOR_SHAPE = re.compile(r"^(?:quic://)?([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):(\d{1,5})$")

FIRST_DELAY, MAX_DELAY = 5.0, 300.0
SLEEP = time.sleep  # the tests replace it
NOW = lambda: datetime.now(UTC)  # noqa: E731  (the tests replace it)


class Refused(Exception):
    """A problem on this side, named: exit 2, nothing more done."""


# --------------------------------------------------------------------------- the contract


def proof(code: str, fingerprint: str) -> str:
    """`onb:proof`: lowercase hex HMAC-SHA256, key = the code's UTF-8, message = the
    fingerprint's ASCII. The contract (devtools research note for ledger item 2009) fixes it."""
    return hmac.new(code.encode("utf-8"), fingerprint.encode("ascii"), hashlib.sha256).hexdigest()


def turtle_string(text: str) -> str:
    """A Turtle short string (ECHAR escapes), safe for a PEM's newlines."""
    out = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def request_turtle(invite: str, certificate_pem: str, fingerprint: str, proof_hex: str) -> str:
    """The `onb:EnrollmentRequest` the node says on `enroll`, exactly the contract's shape:
    one skolemized subject, four properties, no blank nodes."""
    return (
        f"@prefix onb: <{ONB}> .\n"
        f"<urn:iki:onboard:request:{fingerprint}> a onb:EnrollmentRequest ;\n"
        f"    onb:invite <urn:iki:onboard:invite:{invite}> ;\n"
        f"    onb:certificate {turtle_string(certificate_pem.strip() + chr(10))} ;\n"
        f'    onb:fingerprint "{fingerprint}" ;\n'
        f'    onb:proof "{proof_hex}" .\n'
    )


def normal_pin(text: str) -> str:
    """A SHA-256 fingerprint as an operator may paste it (colons, upper case) in the one form
    every tool compares: lowercase hex, no separators."""
    pin = text.replace(":", "").strip().lower()
    if not HEX64.match(pin):
        raise Refused(f"--pin {text!r} is not a SHA-256 fingerprint (64 hex digits)")
    return pin


# --------------------------------------------------------------------------- the door


@dataclass
class Door:
    """The layer door as a gonk.Ikigai target: `--override` of the layer prefixes, with the
    identity directory as the cert set (client.crt, client.key, and the pinned server.crt)."""

    url: str
    cert_dir: Path

    def describe(self) -> str:
        return f"the layer door {self.url} (certificates in {self.cert_dir})"

    def mount_args(self, prefix: str | None = None) -> list[str]:
        return ["--override", f"{LAYER_PREFIXES}={self.url}", "--cert-dir", str(self.cert_dir)]

    def mount_line(self) -> str:
        return f"override {LAYER_PREFIXES}={self.url} {self.cert_dir}"


def door_url(text: str) -> str:
    m = DOOR_SHAPE.match(text.strip())
    if not m or not 0 < int(m.group(2)) < 65536:
        raise Refused(f"--door {text!r} is not HOST:PORT (or quic://HOST:PORT)")
    return f"quic://{m.group(1)}:{m.group(2)}"


# --------------------------------------------------------------------------- the identity


def make_certificate(identity: Path, binary: str = "ikigai") -> None:
    """`ikigai cert generate` into a scratch directory inside the identity directory, then MOVE
    the client pair up. The server pair it also writes is a server identity of its own, which this
    node never uses, so it is deleted with the scratch directory."""
    scratch = identity / ".generate"
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        done = subprocess.run(
            [binary, "cert", "generate", "--dir", str(scratch)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as e:
        raise Refused(
            f"`{binary}` is not on PATH: `cargo install --locked ikigai-cli --features quic`"
        ) from e
    if done.returncode != 0:
        raise Refused(f"`ikigai cert generate` failed: {(done.stderr or done.stdout).strip()}")
    for name in ("client.crt", "client.key"):
        if not (scratch / name).is_file():
            raise Refused(f"`ikigai cert generate` wrote no {name} in {scratch}")
    os.replace(scratch / "client.key", identity / "client.key")
    os.replace(scratch / "client.crt", identity / "client.crt")
    shutil.rmtree(scratch, ignore_errors=True)


def ensure_identity(identity: Path, gonk, make_cert) -> tuple[str, str]:
    """This node's certificate PEM and fingerprint, made here on the first run."""
    identity.mkdir(parents=True, exist_ok=True)
    os.chmod(identity, 0o700)
    crt, key = identity / "client.crt", identity / "client.key"
    if crt.exists() != key.exists():
        raise Refused(
            f"{identity} holds one of client.crt and client.key but not the other: move the "
            "directory aside and run again, and a new pair is made"
        )
    if crt.exists():
        print(f"identity {crt} (kept: a rerun reuses this node's certificate)")
    else:
        make_cert(identity)
        print(f"identity {crt} (made here; its key stays in {identity})")
    os.chmod(key, 0o600)
    pem = crt.read_text(encoding="utf-8")
    try:
        return pem, gonk.cert_fingerprint(pem)
    except gonk.ConfigError as e:
        raise Refused(f"{crt}: {e}") from e


def pin_server(identity: Path, source: str | None, pin: str, gonk, force: bool, what: str) -> None:
    """The door's server certificate in `identity/server.crt`, refused unless it hashes to
    `pin`: from `source` when given, else the one a run before kept."""
    target = identity / "server.crt"
    if source:
        pem = Path(source).expanduser().read_text(encoding="utf-8")
    elif target.exists():
        pem = target.read_text(encoding="utf-8")
    else:
        raise Refused(
            f"no server certificate for {what}: pass --server-cert with the file the invite came "
            f"with; it is checked against --pin before anything is sent"
        )
    try:
        got = gonk.cert_fingerprint(pem)
    except gonk.ConfigError as e:
        raise Refused(f"{source or target}: {e}") from e
    if got != pin:
        raise Refused(
            f"{source or target} is not {what}'s certificate: its SHA-256 is {got}, the pin "
            f"is {pin}. Nothing was sent. Ask the controller for the right file"
        )
    if source:
        if target.exists() and target.read_text(encoding="utf-8") != pem and not force:
            raise Refused(f"{target} holds another certificate; --force replaces it")
        target.write_text(pem, encoding="utf-8")
    print(f"pinned   {what} at {pin[:16]}… ({target})")


# --------------------------------------------------------------------------- state


def read_state(identity: Path) -> dict:
    try:
        return json.loads((identity / "onboard.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise Refused(f"{identity / 'onboard.json'} is not JSON: {e}") from e


def write_state(identity: Path, state: dict) -> None:
    path = identity / "onboard.json"
    tmp = path.with_name(f".onboard.json.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- entitlements


def _values(node: dict, local: str) -> list:
    """A property's values, whatever the egress context compacted its name to: the bare term,
    `onb:` + term, or the full IRI."""
    for key in (local, f"onb:{local}", ONB + local):
        if key in node:
            v = node[key]
            return v if isinstance(v, list) else [v]
    return []


def _text(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("@value", value.get("@id"))
    return value if isinstance(value, str) else None


def _types(node: dict) -> list[str]:
    t = node.get("@type", [])
    return [x for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def _is(node: dict, cls: str) -> bool:
    return any(t in (cls, f"onb:{cls}", ONB + cls) for t in _types(node))


@dataclass
class Entitlement:
    iri: str
    kind: str | None
    principal: str | None
    gonk_grants: list[str]
    layer_grants: list[tuple[str, list[str]]]
    routers: list[str]
    supersedes: list[str]
    expires: str | None

    @property
    def revocation(self) -> bool:
        return not self.gonk_grants and not self.layer_grants


def entitlements(doc, fingerprint: str) -> list[Entitlement]:
    """The entitlements in a `grants` entries answer (JSON-LD) that name `fingerprint`."""
    if isinstance(doc, dict):
        nodes = doc.get("@graph", [doc])
    else:
        nodes = doc if isinstance(doc, list) else []
    nodes = [n for n in nodes if isinstance(n, dict)]
    by_id = {n["@id"]: n for n in nodes if isinstance(n.get("@id"), str)}
    found = []
    for n in nodes:
        if not _is(n, "Entitlement"):
            continue
        if fingerprint not in [(_text(v) or "").lower() for v in _values(n, "fingerprint")]:
            continue
        layer_grants = []
        for g in _values(n, "layerGrant"):
            grant = by_id.get(g.get("@id"), g) if isinstance(g, dict) else by_id.get(g, {})
            layer = next((_text(v) for v in _values(grant, "layer")), None)
            verbs = [t for t in map(_text, _values(grant, "verb")) if t]
            if layer:
                layer_grants.append((layer, verbs))
        found.append(
            Entitlement(
                iri=n.get("@id", ""),
                kind=next((_text(v) for v in _values(n, "kind")), None),
                principal=next((_text(v) for v in _values(n, "principal")), None),
                gonk_grants=[t for t in map(_text, _values(n, "gonkGrant")) if t],
                layer_grants=layer_grants,
                routers=[t for t in map(_text, _values(n, "router")) if t],
                supersedes=[t for t in map(_text, _values(n, "supersedes")) if t],
                expires=next((_text(v) for v in _values(n, "expires")), None),
            )
        )
    return found


def current(found: list[Entitlement], now: datetime) -> list[Entitlement]:
    """Those nothing else supersedes and that have not expired (revocations included)."""
    superseded = {s for e in found for s in e.supersedes}
    live = []
    for e in found:
        if e.iri in superseded:
            continue
        if e.expires:
            try:
                if datetime.fromisoformat(e.expires.replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                continue  # an expiry that does not parse is no entitlement to act on
        live.append(e)
    return live


def poll(ikigai, fingerprint: str) -> tuple[list[Entitlement] | None, str]:
    """One read of the `grants` layer: (the current entitlements naming this node, why not)."""
    answer = ikigai.run(
        f"source urn:layer:grants:entries type={ONB}Entitlement as=application/ld+json"
    )
    if not answer.ok:
        return None, f"not enrolled yet: the door says {answer.text() or 'nothing'}"
    try:
        doc = json.loads(answer.out)
    except ValueError:
        return None, "not enrolled yet: the grants layer answered something other than JSON-LD"
    live = current(entitlements(doc, fingerprint), NOW())
    if not live:
        return None, "not enrolled yet: no entitlement names this certificate"
    return live, ""


# --------------------------------------------------------------------------- configuration


class Writer:
    """Writes configuration, keeping any file that differs unless `force`."""

    def __init__(self, force: bool) -> None:
        self.force = force
        self.kept: list[Path] = []

    def put(self, path: Path, text: str, show: Path | None = None) -> None:
        name = show or path
        if path.exists():
            if path.read_text(encoding="utf-8") == text:
                print(f"same     {name}")
                return
            if not self.force:
                self.kept.append(path)
                print(f"kept     {name} (it differs from what onboard would write; --force)")
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        print(f"wrote    {name}")

    def link(self, path: Path, target: Path) -> None:
        if path.is_symlink() and Path(os.readlink(path)) == target:
            print(f"same     {path} -> {target}")
            return
        if path.exists() or path.is_symlink():
            if not self.force:
                self.kept.append(path)
                print(f"kept     {path} (it exists and is not this identity; --force)")
                return
            if path.is_dir() and not path.is_symlink():
                raise Refused(f"{path} is a directory of its own; move it aside first")
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        print(f"linked   {path} -> {target}")


def base_config(worker, routers: list[str]) -> tuple[str, str, bool]:
    """The configuration init-agents starts from: the sample, with the entitlement's routers
    when it names any (otherwise the sample's placeholder, which the loop refuses until a person
    names the dispatcher)."""
    sample = Path(worker.__file__).resolve().parent.parent / "worker" / "worker.sample.toml"
    text = sample.read_text(encoding="utf-8")
    if routers:
        line = "routers = " + json.dumps(routers)
        text = re.sub(r"(?m)^routers = .*$", line, text, count=1)
    return text, "worker/worker.sample.toml (by onboard, with the entitlement's routers)", True


def write_worker(worker, repo: Path, name: str, agents: list[str], e, w: Writer) -> None:
    base, source, from_sample = base_config(worker, e.routers)
    for agent in agents:
        text = worker.agent_config(base, name, agent, source, from_sample)
        w.put(repo / ".gonk" / f"worker-{agent}.toml", text, Path(".gonk") / f"worker-{agent}.toml")
    if not e.routers:
        print("note     the entitlement names no routers: set `routers` in each before `run`")


def write_gonk_target(gonk, repo: Path, identity: Path, args, name: str, w: Writer) -> bool:
    """`.gonk/local.toml` for gonk over QUIC, with a cert set of its own: gonk's pinned
    server.crt beside LINKS to this node's client pair (one key, never copied)."""
    if not args.gonk:
        print(
            "next     reach gonk: run onboard again with --gonk quic://<host>:<port> "
            "--gonk-server-cert <file> --gonk-pin <sha256>, or `python3 tools/gonk.py init "
            f"--quic … --cert-dir {identity / 'gonk'}`"
        )
        return False
    url = door_url(args.gonk)
    certs = identity / "gonk"
    certs.mkdir(parents=True, exist_ok=True)
    pin_server(certs, args.gonk_server_cert, normal_pin(args.gonk_pin or ""), gonk, w.force, "gonk")
    for part in ("client.crt", "client.key"):
        if not (certs / part).is_symlink():
            if (certs / part).exists():
                raise Refused(f"{certs / part} is a file; it must be a link to {identity / part}")
            (certs / part).symlink_to(identity / part)
    local = "\n".join(
        [
            gonk.LOCAL_TOML_HEADER,
            "",
            'transport = "quic"',
            f"quic = {gonk.toml_string(url)}",
            f"cert_dir = {gonk.toml_string(str(certs))}",
            f"holder = {gonk.toml_string(name)}",
            "",
        ]
    )
    w.put(repo / ".gonk" / "local.toml", local, Path(".gonk") / "local.toml")
    return True


def start_lines(agents: list[str]) -> list[str]:
    joined = ",".join(agents)
    if platform.system() == "Darwin":
        return [
            f"python3 tools/worker.py plist --agents {joined} --out ~/gonk-worker-plists",
            "then install each the way worker/units/dev.ikigai-rs.gonk-worker.plist's header says",
        ]
    return [f"systemctl --user enable --now gonk-worker@{a}" for a in agents] + [
        "(the unit is worker/units/gonk-worker@.service; worker/README.md, step 8)"
    ]


# --------------------------------------------------------------------------- the command


def run(args, make_ikigai, home: Path | None, worker, gonk) -> int:
    try:
        return _run(args, make_ikigai, home, worker, gonk)
    except Refused as e:
        print(f"onboard: {e}")
        return CONFIG


def _run(args, make_ikigai, home: Path | None, worker, gonk) -> int:
    home = home or Path.home()
    name = args.name or socket.gethostname().split(".")[0].lower()
    if not NAME_SHAPE.match(name):
        raise Refused(f"--name {name!r}: lowercase letters, digits and -, at most 24")
    if not INVITE_SHAPE.match(args.invite):
        raise Refused(f"--invite {args.invite!r} is not an invite id")
    if not args.code or any(c.isspace() for c in args.code):
        raise Refused("--code is the pairing code the invite printed, with no spaces")
    url, pin = door_url(args.door), normal_pin(args.pin)
    agents = worker.agents_list(args.agents)
    repo = Path(args.repo).resolve() if args.repo else gonk.find_repo(Path.cwd())
    identity = Path(args.cert_dir).expanduser() if args.cert_dir else None
    identity = (identity or home / ".ikigai" / "onboard" / name).resolve()

    pem, fingerprint = ensure_identity(identity, gonk, make_certificate)
    print(f"         fingerprint {fingerprint}")
    pin_server(identity, args.server_cert, pin, gonk, args.force, "the door")
    door = Door(url, identity)
    ikigai = make_ikigai(door)

    state = read_state(identity)
    seen = state.get("fingerprint") == fingerprint and state.get("invite") == args.invite
    if seen and state.get("request"):
        print(f"request  already posted as {state['request']}: polling, nothing re-sent")
    else:
        body = request_turtle(args.invite, pem, fingerprint, proof(args.code, fingerprint))
        answer = ikigai.run("sink urn:layer:enroll:say", stdin=body)
        if not answer.ok:
            print(f"onboard: the door refused the request: {answer.text()}")
            return ERROR
        entry = answer.out.strip().splitlines()[-1] if answer.out.strip() else "(no IRI)"
        write_state(
            identity,
            {
                "fingerprint": fingerprint,
                "invite": args.invite,
                "door": url,
                "request": entry,
                "posted": NOW().strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        print(f"request  posted as {entry}; the controller approves it")

    delay, waited = FIRST_DELAY, 0.0
    max_wait = worker.seconds(args.max_wait, "--max-wait")
    while True:
        live, why = poll(ikigai, fingerprint)
        if live is not None:
            break
        if args.once or waited >= max_wait:
            print(why)
            return NOT_YET
        print(f"{why}; next look in {worker.spoken(delay)}")
        SLEEP(delay)
        waited += delay
        delay = min(delay * 2, MAX_DELAY)

    granted = [e for e in live if not e.revocation]
    if not granted:
        print(f"onboard: revoked: {', '.join(e.iri for e in live)} grants this node nothing")
        return ERROR
    for e in granted:
        print(f"entitled {e.iri} (kind {e.kind or 'unnamed'})")

    w = Writer(args.force)
    mount = f"mount = {gonk.toml_string(door.mount_line())}\n"
    w.put(
        identity / "config-home.toml",
        "# The layer door, as a config-home mount line: copy it into the config home's\n"
        "# config.toml for every ikigai command on this machine to reach the door. onboard never\n"
        "# edits config.toml itself: every ikigai server on this machine inherits it.\n" + mount,
    )
    is_worker = any(e.gonk_grants or (e.kind or "").endswith(":worker") for e in granted)
    is_member = any(e.layer_grants or (e.kind or "").endswith(":task-member") for e in granted)
    reaches_gonk = False
    if is_worker:
        routers = list(dict.fromkeys(r for e in granted for r in e.routers))
        merged = dataclasses.replace(granted[0], routers=routers)
        write_worker(worker, repo, name, agents, merged, w)
        reaches_gonk = write_gonk_target(gonk, repo, identity, args, name, w)
    if is_member:
        w.link(home / ".ikigai" / "layers-client" / name, identity)
        layers = sorted({layer for e in granted for layer, _ in e.layer_grants})
        print(f"         ikigai-layers --name {name} me   (layers: {', '.join(layers) or 'none'})")

    if w.kept:
        print(f"onboard: kept {len(w.kept)} configuration(s) that differ; rerun with --force")
        return ERROR
    if is_worker:
        print("start:")
        if not reaches_gonk:
            print("  first reach gonk (above), then")
        for line in start_lines(agents):
            print(f"  {line}")
    return ENROLLED


def add_parser(sub, cmd) -> None:
    s = sub.add_parser(
        "onboard", help="enroll this node with a pairing code, then write its configuration"
    )
    s.add_argument("--door", required=True, help="the layer door, HOST:PORT")
    s.add_argument("--pin", required=True, help="the door's server-certificate SHA-256")
    s.add_argument(
        "--server-cert", help="the door's server.crt, as the invite came with it (checked by --pin)"
    )
    s.add_argument("--code", required=True, help="the pairing code (never stored)")
    s.add_argument("--invite", required=True, help="the invite id")
    s.add_argument("--name", help="this node's name (default: the short host name)")
    s.add_argument("--agents", default="claude,codex", help="the worker loops, for a worker kind")
    s.add_argument("--cert-dir", help="the identity directory (default ~/.ikigai/onboard/<name>)")
    s.add_argument("--gonk", help="gonk's QUIC door, HOST:PORT, for a worker kind")
    s.add_argument("--gonk-server-cert", help="gonk's server.crt (checked by --gonk-pin)")
    s.add_argument("--gonk-pin", help="gonk's server-certificate SHA-256")
    s.add_argument("--once", action="store_true", help="look once; exit 3 while not enrolled")
    s.add_argument("--max-wait", default="24h", help="how long to poll (default 24h)")
    s.add_argument("--force", action="store_true", help="replace configuration that differs")
    s.set_defaults(run=cmd)
