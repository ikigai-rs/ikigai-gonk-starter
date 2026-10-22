"""`worker.py onboard`: a node enrolls itself with a pairing code (ledger item 2009).

Hermetic: an in-process fake door answers the two commands onboard sends (the say on `enroll`
and the read of `grants`), and the node's certificate is a fixture placed where a run before
would have left one, so no `ikigai` is needed. The certificates here are not certificates at all,
only base64 bytes in PEM armor, which is all a fingerprint reads; nothing in them is secret.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from support import gonk, scratch_repo
from worker_support import worker

onboard = worker.worker_onboard  # tools/worker_onboard.py, as worker.py imports it

# JSON-LD transcription of tests/onboard.rs's entitlement_body / TASKS and
# the_node_reads_its_entitlement_with_its_new_certificate; the context is copied
# verbatim from onboard/layers/grants.context.jsonld. Fingerprint and entry IDs
# are fixed for this hermetic test (no certificate/signature verification here).
# Source: ikigai-rs/ikigai-layers at 3afdeb0e3336438876ee60de67c0b62f08040d2b.
ENTITLEMENT_FIXTURE = Path(__file__).parent / "fixtures" / "onboard-entitlement.jsonld"


# The request's shape, copied from the contract the three arcs of ledger item 2009 share:
# ikigai-devtools claude/research/node-onboarding-2009-2026-10-22.md, "The request". The arc
# that owns the closed SHACL shape is ikigai-layers'.
CONTRACT_REQUEST = """\
<urn:iki:onboard:request:{fingerprint}> a onb:EnrollmentRequest ;
    onb:invite <urn:iki:onboard:invite:{id}> ;
    onb:certificate "-----BEGIN CERTIFICATE-----…" ;
    onb:fingerprint "{lowercase hex SHA-256 of the leaf DER}" ;
    onb:proof "{lowercase hex HMAC-SHA256(key = the pairing code's UTF-8 bytes, \
message = the fingerprint's ASCII)}" .
"""


def pem(data: bytes) -> str:
    body = base64.b64encode(data).decode("ascii")
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"


NODE_PEM = pem(b"a test node certificate, not a real one")
NODE_FP = hashlib.sha256(b"a test node certificate, not a real one").hexdigest()
DOOR_PEM = pem(b"a test door certificate, not a real one")
DOOR_FP = hashlib.sha256(b"a test door certificate, not a real one").hexdigest()
GONK_PEM = pem(b"a test gonk certificate, not a real one")
GONK_FP = hashlib.sha256(b"a test gonk certificate, not a real one").hexdigest()
ROUTER = "urn:iki:gonk:passkey:dispatcher-test"


def entitlement(n: int, kind: str, gonk_grant=None, layer=None, supersedes=None, fp=NODE_FP):
    node = {
        "@id": f"urn:iki:onboard:entitlement:{fp}:{n}",
        "@type": "Entitlement",
        "fingerprint": fp,
        "principal": {"@id": "urn:collab:participant:mini"},
        "kind": {"@id": f"urn:iki:onboard:kind:{kind}"},
        "expires": {"@value": "2999-01-01T00:00:00Z"},
        "router": ROUTER,
    }
    node["request"] = {"@id": "urn:layer:enroll:entry:1"}
    if gonk_grant:
        node["gonkGrant"] = gonk_grant
    if layer:
        node["layerGrant"] = [
            {"@id": f"urn:cap:layer:{verb}:{layer}"} for verb in ("read", "out", "take")
        ]
    if supersedes:
        node["ik:supersedes"] = {"@id": supersedes}
    return [node]


class FakeDoor:
    """The layer door, in process: `enroll`'s say and `grants`' entries, as onboard asks them."""

    def __init__(self) -> None:
        self.says: list[str] = []
        self.reads = 0
        self.grants: list[dict] | None = None  # None: this certificate is not enrolled yet
        self.refuse_say = False
        self.targets = []

    def make(self, target):
        self.targets.append(target)
        return self

    def run(self, command: str, stdin: str | None = None) -> gonk.Answer:
        if command == "sink urn:layer:enroll:say":
            if self.refuse_say:
                return gonk.Answer(1, "", "conflict: at most 5 says per hour per fingerprint")
            self.says.append(stdin or "")
            return gonk.Answer(0, f"urn:layer:enroll:entry:{len(self.says)}\n", "")
        if command.startswith("source urn:layer:grants:entries "):
            self.reads += 1
            if self.grants is None:
                return gonk.Answer(1, "", "denied: urn:cap:layer:read:grants")
            doc = json.loads(ENTITLEMENT_FIXTURE.read_text())
            doc["@graph"] = self.grants
            return gonk.Answer(0, json.dumps(doc), "")
        return gonk.Answer(1, "", f"the fake door does not know {command!r}")


class Onboarding(unittest.TestCase):
    def setUp(self):
        self.repo = scratch_repo(self)
        self.home = Path(tempfile.mkdtemp(prefix="onboard-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.identity = self.home / ".ikigai" / "onboard" / "mini"
        self.identity.mkdir(parents=True)
        (self.identity / "client.crt").write_text(NODE_PEM)
        (self.identity / "client.key").write_text("a placeholder, not a key\n")
        self.files = self.home / "invite"
        self.files.mkdir()
        (self.files / "door.crt").write_text(DOOR_PEM)
        (self.files / "gonk.crt").write_text(GONK_PEM)
        self.door = FakeDoor()
        self.sleeps: list[float] = []
        saved = onboard.SLEEP
        onboard.SLEEP = self.sleeps.append
        self.addCleanup(setattr, onboard, "SLEEP", saved)

    def onboard(self, *extra: str, pin: str = DOOR_FP, invite: str = "inv-1") -> tuple[int, str]:
        argv = [
            "--repo",
            str(self.repo),
            "onboard",
            "--door",
            "door.example.org:4434",
            "--pin",
            pin,
            "--server-cert",
            str(self.files / "door.crt"),
            "--code",
            "K7Q2M9XW4P",
            "--invite",
            invite,
            "--name",
            "mini",
            *extra,
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = worker.main(argv, make_ikigai=self.door.make, home=self.home)
        return code, out.getvalue()

    # -- the contract

    def test_the_proof_matches_a_known_vector(self):
        # Computed independently with openssl:
        #   printf '%s' "$FP" | openssl dgst -sha256 -hmac 'K7Q2M9XW4P'
        fp = hashlib.sha256(b"abc").hexdigest()
        self.assertEqual(
            onboard.proof("K7Q2M9XW4P", fp),
            "7b214870c3bed39d4f6d5e8326ee1bcec5fd69e9931754295542419f05f05d74",
        )

    def test_the_request_has_the_contracts_shape(self):
        text = onboard.request_turtle("inv-1", NODE_PEM, NODE_FP, "ab" * 32)
        want = re.findall(r"(onb:\w+|\ba\b) ", CONTRACT_REQUEST)
        got = re.findall(r"(onb:\w+|\ba\b) ", text.split("\n", 1)[1])
        self.assertEqual(got, want)  # the same predicates, in the contract's order
        self.assertIn(f"<urn:iki:onboard:request:{NODE_FP}> a onb:EnrollmentRequest ;", text)
        self.assertIn("onb:invite <urn:iki:onboard:invite:inv-1> ;", text)
        self.assertIn(f'onb:fingerprint "{NODE_FP}" ;', text)
        self.assertIn(f'onb:proof "{"ab" * 32}" .', text)
        self.assertIn(f"@prefix onb: <{onboard.ONB}> .", text)
        self.assertNotIn("_:", text)  # skolemized: no blank nodes
        self.assertNotIn("[", text)
        # The certificate is one short string whose escapes give back the PEM exactly.
        literal = re.search(r'onb:certificate "((?:[^"\\]|\\.)*)" ;', text).group(1)
        self.assertEqual(json.loads(f'"{literal}"'), NODE_PEM)

    # -- the pin

    def test_a_wrong_pin_is_refused_and_nothing_is_sent(self):
        code, out = self.onboard(pin=GONK_FP)
        self.assertEqual(code, onboard.CONFIG)
        self.assertIn("is not the door's certificate", out)
        self.assertEqual(self.door.says, [])
        self.assertEqual(self.door.targets, [])
        self.assertFalse((self.identity / "server.crt").exists())

    def test_a_pin_with_colons_and_capitals_is_the_same_pin(self):
        spaced = ":".join(DOOR_FP[i : i + 2] for i in range(0, 64, 2)).upper()
        code, _ = self.onboard("--once", pin=spaced)
        self.assertEqual(code, onboard.NOT_YET)
        self.assertEqual((self.identity / "server.crt").read_text(), DOOR_PEM)

    # -- posting and polling

    def test_it_posts_the_request_then_is_not_enrolled_yet(self):
        code, out = self.onboard("--once")
        self.assertEqual(code, onboard.NOT_YET)
        self.assertIn("not enrolled yet", out)
        self.assertEqual(len(self.door.says), 1)
        body = self.door.says[0]
        self.assertIn(f'onb:proof "{onboard.proof("K7Q2M9XW4P", NODE_FP)}"', body)
        self.assertNotIn("K7Q2M9XW4P", body)  # the code itself never leaves
        state = json.loads((self.identity / "onboard.json").read_text())
        self.assertEqual(state["request"], "urn:layer:enroll:entry:1")
        self.assertNotIn("K7Q2M9XW4P", json.dumps(state))  # and is never stored
        target = self.door.targets[0]
        self.assertEqual(target.url, "quic://door.example.org:4434")
        self.assertEqual(
            target.mount_args(),
            [
                "--override",
                "urn:layer:,urn:layers:,urn:collab:=quic://door.example.org:4434",
                "--cert-dir",
                str(self.identity.resolve()),
            ],
        )

    def test_a_rerun_posts_nothing_new(self):
        self.onboard("--once")
        code, out = self.onboard("--once")
        self.assertEqual(code, onboard.NOT_YET)
        self.assertIn("already posted", out)
        self.assertEqual(len(self.door.says), 1)

    def test_a_new_invite_posts_again(self):
        self.onboard("--once")
        code, _ = self.onboard("--once", invite="inv-2")
        self.assertEqual(code, onboard.NOT_YET)
        self.assertEqual(len(self.door.says), 2)

    def test_a_refused_request_is_an_error_and_records_nothing(self):
        self.door.refuse_say = True
        code, out = self.onboard("--once")
        self.assertEqual(code, onboard.ERROR)
        self.assertIn("refused the request", out)
        self.assertFalse((self.identity / "onboard.json").exists())

    def test_it_backs_off_exponentially_up_to_the_maximum(self):
        code, _ = self.onboard("--max-wait", "20m")
        self.assertEqual(code, onboard.NOT_YET)
        self.assertEqual(self.sleeps[:7], [5, 10, 20, 40, 80, 160, 300])
        self.assertTrue(all(s <= onboard.MAX_DELAY for s in self.sleeps))
        self.assertGreaterEqual(sum(self.sleeps), 20 * 60)

    # -- on an entitlement

    def worker_grants(self):
        return entitlement(1, "worker", gonk_grant="default=worker") + entitlement(
            2, "task-member", layer="tasks"
        )

    def test_an_entitlement_writes_the_configuration(self):
        self.door.grants = self.worker_grants()
        code, out = self.onboard(
            "--agents",
            "claude,codex",
            "--gonk",
            "gonk.example.org:1062",
            "--gonk-server-cert",
            str(self.files / "gonk.crt"),
            "--gonk-pin",
            GONK_FP,
        )
        self.assertEqual(code, onboard.ENROLLED, out)
        identity = self.identity.resolve()
        # The mount line, for the config home, never written there.
        mount = (identity / "config-home.toml").read_text()
        self.assertIn(
            f'mount = "override urn:layer:,urn:layers:,urn:collab:=quic://door.example.org:4434 '
            f'{identity}"',
            mount,
        )
        # One loop per agent, through init-agents' own code, with the entitlement's routers.
        for agent in ("claude", "codex"):
            text = (self.repo / ".gonk" / f"worker-{agent}.toml").read_text()
            cfg = worker.tomllib.loads(text)
            self.assertEqual(cfg["labels"], ["worker:mini", f"agent:{agent}"])
            self.assertEqual(cfg["holder"], f"mini-{agent}")
            self.assertEqual(cfg["routers"], [ROUTER])
            # What the loop itself accepts (its repositories are worker_repos.py's to add).
            self.assertEqual(worker.load_routers(cfg["routers"], agent, self.repo), [ROUTER])
        # gonk over QUIC, with gonk's pinned server.crt and LINKS to the one client pair.
        local = worker.tomllib.loads((self.repo / ".gonk" / "local.toml").read_text())
        self.assertEqual(local["transport"], "quic")
        self.assertEqual(local["quic"], "quic://gonk.example.org:1062")
        certs = Path(local["cert_dir"])
        self.assertEqual((certs / "server.crt").read_text(), GONK_PEM)
        for part in ("client.crt", "client.key"):
            self.assertTrue((certs / part).is_symlink())
            self.assertEqual((certs / part).resolve(), identity / part)
        # The layers client's cert set, for the task layer: the identity, linked.
        link = self.home / ".ikigai" / "layers-client" / "mini"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), identity)
        self.assertIn("start:", out)
        self.assertNotIn("-----BEGIN", out)
        self.assertNotIn("placeholder, not a key", out)

    def test_a_rerun_after_enrollment_changes_nothing_and_posts_nothing(self):
        self.door.grants = self.worker_grants()
        self.assertEqual(self.onboard()[0], onboard.ENROLLED)
        before = (self.repo / ".gonk" / "worker-claude.toml").read_text()
        code, out = self.onboard()
        self.assertEqual(code, onboard.ENROLLED, out)
        self.assertIn("same     .gonk/worker-claude.toml", out)
        self.assertEqual((self.repo / ".gonk" / "worker-claude.toml").read_text(), before)
        self.assertEqual(len(self.door.says), 1)

    def test_force_is_required_to_overwrite(self):
        mine = self.repo / ".gonk" / "worker-claude.toml"
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("# my own edits\n")
        self.door.grants = self.worker_grants()
        code, out = self.onboard("--agents", "claude")
        self.assertEqual(code, onboard.ERROR)
        self.assertIn("kept     .gonk/worker-claude.toml", out)
        self.assertEqual(mine.read_text(), "# my own edits\n")
        code, out = self.onboard("--agents", "claude", "--force")
        self.assertEqual(code, onboard.ENROLLED, out)
        self.assertIn('labels = ["worker:mini", "agent:claude"]', mine.read_text())

    def test_an_existing_layers_client_set_is_kept_without_force(self):
        other = self.home / ".ikigai" / "layers-client" / "mini"
        other.parent.mkdir(parents=True)
        other.symlink_to(self.home)
        self.door.grants = entitlement(1, "task-member", layer="tasks")
        code, out = self.onboard()
        self.assertEqual(code, onboard.ERROR)
        self.assertEqual(other.resolve(), self.home.resolve())
        self.assertEqual(self.onboard("--force")[0], onboard.ENROLLED)
        self.assertEqual(other.resolve(), self.identity.resolve())

    def test_a_task_member_writes_no_worker_configuration(self):
        self.door.grants = entitlement(1, "task-member", layer="tasks")
        code, out = self.onboard()
        self.assertEqual(code, onboard.ENROLLED, out)
        self.assertFalse(list((self.repo / ".gonk").glob("worker-*.toml")))
        self.assertNotIn("start:", out)

    def test_an_entitlement_for_another_certificate_is_not_this_ones(self):
        self.door.grants = entitlement(1, "worker", gonk_grant="default=worker", fp="0" * 64)
        code, out = self.onboard("--once")
        self.assertEqual(code, onboard.NOT_YET)
        self.assertIn("no entitlement names this certificate", out)

    def test_a_revocation_that_supersedes_the_grant_is_revoked(self):
        first = entitlement(1, "worker", gonk_grant="default=worker")
        revoke = entitlement(2, "worker", supersedes=first[0]["@id"])
        self.door.grants = first + revoke
        code, out = self.onboard()
        self.assertEqual(code, onboard.ERROR)
        self.assertIn("revoked", out)
        self.assertFalse(list((self.repo / ".gonk").glob("worker-*.toml")))

    def test_the_upstream_entitlement_fixture(self):
        doc = json.loads(ENTITLEMENT_FIXTURE.read_text())
        e = onboard.entitlements(doc, NODE_FP)[0]
        self.assertEqual(e.layer_grants, [("tasks", ["read", "out", "take"])])
        self.assertEqual(e.request, "urn:layer:enroll:entry:1")
        self.door.grants = doc["@graph"]
        code, out = self.onboard()
        self.assertEqual(code, onboard.ENROLLED, out)
        self.assertIn("layers: tasks", out)
        self.assertTrue((self.home / ".ikigai" / "layers-client" / "mini").is_symlink())

    def test_supersession_uses_the_ik_namespace(self):
        for key in ("supersedes", "ik:supersedes", "https://ikigai-rs.dev/ns#supersedes"):
            with self.subTest(key=key):
                first = entitlement(1, "worker", gonk_grant="default=worker")
                revoke = entitlement(2, "worker")[0]
                revoke[key] = {"@id": first[0]["@id"]}
                live = onboard.current(
                    onboard.entitlements(first + [revoke], NODE_FP), onboard.NOW()
                )
                self.assertEqual([e.iri for e in live], [revoke["@id"]])
        revoke = entitlement(2, "worker")[0]
        revoke["onb:supersedes"] = {"@id": first[0]["@id"]}
        self.assertEqual(onboard.entitlements([revoke], NODE_FP)[0].supersedes, [])

    def test_capability_iris_are_grouped_by_layer(self):
        doc = entitlement(1, "task-member", layer="tasks")[0]
        doc["layerGrant"].append({"@id": "urn:cap:layer:read:grants"})
        e = onboard.entitlements([doc], NODE_FP)[0]
        self.assertEqual(e.layer_grants, [("tasks", ["read", "out", "take"]), ("grants", ["read"])])
        self.assertEqual(e.request, "urn:layer:enroll:entry:1")

    def test_malformed_layer_capabilities_are_refused(self):
        for value in (
            "urn:cap:other:read:tasks",
            "urn:cap:layer:read:",
            "urn:cap:layer:read:tasks:extra",
            "urn:cap:layer:unknown:tasks",
            "urn:cap:layer:read:tasks\n",
            {"layer": "tasks", "verb": "read"},
            {"@value": "urn:cap:layer:read:tasks"},
        ):
            with self.subTest(value=value):
                self.door.grants = entitlement(1, "task-member", layer="tasks")
                self.door.grants[0]["layerGrant"] = [value]
                code, out = self.onboard()
                self.assertEqual(code, onboard.CONFIG, out)
                self.assertIn("onb:layerGrant", out)
                self.assertFalse((self.identity / "config-home.toml").exists())

    def test_the_entitlement_terms_may_be_compacted_any_way(self):
        doc = entitlement(1, "worker", gonk_grant="default=worker", layer="tasks")[0]
        full = {(k if k.startswith("@") else onboard.ONB + k): v for k, v in doc.items()}
        full["@type"] = onboard.ONB + "Entitlement"
        found = onboard.entitlements({"@graph": [full]}, NODE_FP)
        self.assertEqual([e.gonk_grants for e in found], [["default=worker"]])
        self.assertEqual(found[0].request, "urn:layer:enroll:entry:1")
        self.assertEqual(found[0].layer_grants, [("tasks", ["read", "out", "take"])])

    # -- the identity

    def test_the_certificate_is_made_here_when_there_is_none(self):
        for part in ("client.crt", "client.key"):
            (self.identity / part).unlink()
        made = []

        def fake_generate(identity: Path, binary: str = "ikigai") -> None:
            made.append(identity)
            (identity / "client.crt").write_text(NODE_PEM)
            (identity / "client.key").write_text("a placeholder, not a key\n")

        saved = onboard.make_certificate
        onboard.make_certificate = fake_generate
        self.addCleanup(setattr, onboard, "make_certificate", saved)
        code, out = self.onboard("--once")
        self.assertEqual(code, onboard.NOT_YET)
        self.assertEqual(made, [self.identity.resolve()])
        self.assertEqual((self.identity / "client.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.identity.stat().st_mode & 0o777, 0o700)
        self.assertIn("made here", out)

    def test_half_an_identity_is_refused(self):
        (self.identity / "client.key").unlink()
        code, out = self.onboard("--once")
        self.assertEqual(code, onboard.CONFIG)
        self.assertIn("but not the other", out)


if __name__ == "__main__":
    unittest.main()
