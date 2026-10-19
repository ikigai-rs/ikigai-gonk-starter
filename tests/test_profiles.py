"""Process profiles (profiles/): what each one generates, how they compose, and what `init`,
`status` and each profile's own tools do with them.

That every generated repository's own tests PASS, for every profile on the python target and
for a combination on each other target, is checked by scripts/gates.sh (and so by CI).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, FakeIkigai, gonk, ok

spec = importlib.util.spec_from_file_location("new", ROOT / "tools" / "new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)

PROFILES = new.catalog()["profiles"]
SECTIONS = ROOT / "profiles" / "sections"

# What each profile carries beyond the base, and so what must be ABSENT without it.
OWN_FILES = {
    "workers": [
        "tools/worker.py",
        "tools/worker_repos.py",
        "worker/worker.sample.toml",
        "worker/units/gonk-worker.service",
        "worker/units/gonk-worker@.service",
    ],
    "hub": ["tools/hub.py", ".gonk/templates/brief.md"],
    "review-every-commit": ["hooks/git/post-commit", "hooks/git/review-on-commit.py"],
    "audits": ["tools/audit.py", ".gonk/templates/audit.md"],
}
OWN_RECIPES = {
    "workers": ["worker", "worker-once", "worker-plan", "worker-forget"],
    "hub": ["arc", "dispatch"],
    "review-every-commit": ["hooks"],
    "audits": ["audit"],
}
EXECUTABLE = [
    "tools/gonk.py",
    "tools/hub.py",
    "tools/audit.py",
    "hooks/claude/require-claim.py",
    "hooks/git/post-commit",
    "hooks/git/review-on-commit.py",
]


def scratch(test, name="repo") -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gonk-profiles-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    return tmp.resolve() / name


def run_tool(main, repo: Path, argv: list[str], fake=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--repo", str(repo), *argv], make_ikigai=fake or FakeIkigai())
    return code, out.getvalue(), err.getvalue()


def wire(repo: Path, *, quic=False, holder="hub-1"):
    (repo / ".gonk" / "gonk.toml").write_text('ledger = "acme"\n')
    if quic:
        lines = ['transport = "quic"', 'quic = "quic://box:1062"', f'cert_dir = "{repo}/c"']
    else:
        lines = ['transport = "socket"', f'socket = "{repo / "g.sock"}"']
    (repo / ".gonk" / "local.toml").write_text("\n".join([*lines, f'holder = "{holder}"']) + "\n")


def git(repo: Path, *argv: str, env=None) -> subprocess.CompletedProcess:
    ident = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    return subprocess.run(
        ["git", *ident, *argv], cwd=repo, capture_output=True, text=True, env=env, check=True
    )


def load_tool(repo: Path, rel: str, name: str):
    """A tool of the GENERATED repository, imported from there (it imports that gonk.py)."""
    spec = importlib.util.spec_from_file_location(name, repo / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TheProfilesThemselves(unittest.TestCase):
    def test_the_profile_data_is_consistent(self):
        """Every section exists and has one owner, every hook and check is one the tools
        know, every starter file and recipe a profile carries exists, and the default names
        real profiles."""
        self.assertEqual(new.profile_problems(), [])

    def test_the_starters_own_agents_md_and_process_are_the_default_process(self):
        """A repository made from this starter as a TEMPLATE (a copy, not new.py) carries the
        same rules and the same record as one new.py generates with no --process."""
        names = new.resolve(None)
        fix = "rewrite them with `python3 tools/new.py --sync-starter`"
        self.assertEqual((ROOT / "AGENTS.md").read_text(), new.agents_md(names), fix)
        self.assertEqual(
            (ROOT / ".gonk" / "process.toml").read_text(), new.process_toml(names), fix
        )

    def test_the_default_is_what_this_starter_generated_before_profiles(self):
        self.assertEqual(new.resolve(None), ["solo", "workers", "spec"])
        repo = scratch(self)
        new.generate(repo, "python")
        # Every recipe the starter's justfile had for a generated repository is still there.
        names = {n for n, _ in new.recipe_blocks((repo / "justfile").read_text()) if n}
        starter = {n for n, _ in new.recipe_blocks((ROOT / "justfile").read_text()) if n}
        self.assertLessEqual(starter - new.STARTER_ONLY_RECIPES, names)


class EachProfile(unittest.TestCase):
    """Each profile alone, generated for the default target."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gonk-profiles-")).resolve()
        cls.repos = {}
        for name in PROFILES:
            cls.repos[name] = cls.tmp / name
            new.generate(cls.repos[name], "python", [name])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_agents_md_holds_exactly_its_sections(self):
        base = set(new.catalog()["base"]["sections"])
        for name, repo in self.repos.items():
            own = set(new.profile(name).get("sections", []))
            agents = (repo / "AGENTS.md").read_text()
            for section in SECTIONS.glob("*.md"):
                text = section.read_text().strip()
                expected = section.stem in base or section.stem in own
                self.assertEqual(text in agents, expected, f"{name}: {section.stem}")
            self.assertEqual(agents, new.agents_md([name]), name)

    def test_each_carries_accessibility_md_and_the_agents_rule(self):
        # Ledger item 1620: whatever the process, a generated repository has an
        # ACCESSIBILITY.md to grow and the AGENTS.md rule that points agents at it.
        for name, repo in self.repos.items():
            self.assertTrue((repo / "ACCESSIBILITY.md").is_file(), name)
            self.assertIn(
                "Read `ACCESSIBILITY.md` before any change to a user interface",
                (repo / "AGENTS.md").read_text(),
                name,
            )

    def test_its_files_and_hooks_are_present_and_executable_and_others_absent(self):
        for name, repo in self.repos.items():
            for owner, files in OWN_FILES.items():
                for rel in files:
                    self.assertEqual((repo / rel).is_file(), owner == name, f"{name}: {rel}")
            for rel in EXECUTABLE:
                if (repo / rel).exists():
                    self.assertTrue(os.access(repo / rel, os.X_OK), f"{name}: {rel}")
            # The optional Claude Code claim hook is every process's: every process claims.
            self.assertTrue(os.access(repo / "hooks/claude/require-claim.py", os.X_OK), name)

    def test_its_recipes_are_there_and_others_are_not(self):
        for name, repo in self.repos.items():
            names = {n for n, _ in new.recipe_blocks((repo / "justfile").read_text()) if n}
            for recipe in ["init", "next", "claim", "report", "close", "status", "gates"]:
                self.assertIn(recipe, names, name)
            for owner, recipes in OWN_RECIPES.items():
                for recipe in recipes:
                    self.assertEqual(recipe in names, owner == name, f"{name}: {recipe}")

    def test_process_toml_names_it_and_what_it_asks_for(self):
        for name, repo in self.repos.items():
            process = gonk.read_process(repo)
            self.assertEqual(process["process"], [name])
            self.assertIn("claude:require-claim", process["hooks"])
            self.assertEqual("git:post-commit" in process["hooks"], name == "review-every-commit")
            self.assertEqual(process["profile"][name]["summary"], new.profile(name)["summary"])
            self.assertEqual("review" in process, name == "review-every-commit", name)

    def test_init_and_status_name_it(self):
        for name, repo in self.repos.items():
            code, out, _ = run_tool(gonk.main, repo, ["init"])
            self.assertRegex(out, rf"ok\s+process\s+{name} \(\.gonk/process\.toml\)", name)
            self.assertIn(new.profile(name)["summary"], out)
            code, out, _ = run_tool(gonk.main, repo, ["status"])
            self.assertEqual(code, 2, out)  # not wired: but it says the process first
            self.assertEqual(out.splitlines()[0], f"process: {name}")


class Composition(unittest.TestCase):
    def test_a_list_in_any_order_is_the_catalogs_order_once(self):
        self.assertEqual(new.resolve(["spec", "workers", "spec"]), ["workers", "spec"])

    def test_an_unknown_profile_is_refused_naming_the_known_ones(self):
        with self.assertRaises(SystemExit) as refused:
            new.resolve(["solo", "kanban"])
        self.assertIn("'kanban'", str(refused.exception))
        self.assertIn(
            "solo, workers, hub, spec, review-every-commit, audits", str(refused.exception)
        )

    def test_a_conflict_is_refused_with_its_reason_whichever_side_declares_it(self):
        for pair in (["audits", "review-every-commit"], ["review-every-commit", "audits"]):
            with self.assertRaises(SystemExit) as refused:
                new.resolve(pair)
            self.assertIn("how bugs are found", str(refused.exception))

    def test_nothing_named_is_refused(self):
        with self.assertRaises(SystemExit):
            new.resolve([])

    def test_a_combination_carries_both_profiles(self):
        repo = scratch(self)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = new.main([str(repo), "--process", "spec, hub,workers"])
        self.assertEqual(code, 0)
        self.assertIn("process workers, hub, spec", out.getvalue())
        self.assertEqual(gonk.read_process(repo)["process"], ["workers", "hub", "spec"])
        agents = (repo / "AGENTS.md").read_text()
        for heading in ["## Spec first", "## Worker machines", "## Hub and spoke"]:
            self.assertIn(heading, agents)
        self.assertNotIn("## Periodic audits", agents)
        for rel in [*OWN_FILES["workers"], *OWN_FILES["hub"]]:
            self.assertTrue((repo / rel).is_file(), rel)
        # Sections keep the catalog's order however the profiles were named.
        self.assertLess(agents.index("## Spec first"), agents.index("## Worker machines"))


class ReadProcess(unittest.TestCase):
    def test_absent_is_none_and_init_says_so_as_a_note(self):
        repo = scratch(self)
        new.generate(repo, "python", ["solo"])
        (repo / ".gonk" / "process.toml").unlink()
        self.assertIsNone(gonk.read_process(repo))
        code, out, _ = run_tool(gonk.main, repo, ["init"])
        self.assertRegex(out, r"note\s+process\s+no \.gonk/process\.toml")
        code, out, _ = run_tool(gonk.main, repo, ["status"])
        self.assertEqual(out.splitlines()[0], "process: none named (no .gonk/process.toml)")

    def test_a_version_this_tool_does_not_read_is_refused(self):
        repo = scratch(self)
        new.generate(repo, "python", ["solo"])
        (repo / ".gonk" / "process.toml").write_text('version = 2\nprocess = ["solo"]\n')
        with self.assertRaises(gonk.ConfigError):
            gonk.read_process(repo)


class ReviewInit(unittest.TestCase):
    """`init` for review-every-commit: what it checks on this machine and asks of gonk."""

    def setUp(self):
        self.repo = scratch(self)
        new.generate(self.repo, "python", ["review-every-commit"])
        wire(self.repo)
        (self.repo / "g.sock").touch()

    def init(self, depth: dict | None, which="/bin/ikigai-gonk", served=True):
        absent = ok('<x> ik:title "contract unavailable" .')
        script = [
            ("ledgers", ok("acme  1 open  1 total  urn:…\n")),
            (f"describe urn:gk:repo:{self.repo.name}:tree", ok("x") if served else absent),
            (f"describe urn:gk:repo:{self.repo.name}:review:", ok("x")),
            ("review:depth", ok(json.dumps(depth)) if depth else gonk.Answer(1, "", "denied")),
            ("describe", absent),
        ]
        with mock.patch.object(gonk.shutil, "which", lambda name: which):
            return run_tool(gonk.main, self.repo, ["init"], FakeIkigai(script))

    def test_an_armed_gonk_and_an_installed_hook_are_ready(self):
        git(self.repo, "init", "-q")
        git(self.repo, "config", "core.hooksPath", "hooks/git")
        code, out, _ = self.init({"configured": True, "armed": True, "sentence": "Empty."})
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"ok\s+hook\s+post-commit asks gonk")
        self.assertRegex(out, rf"ok\s+review\s+gonk serves this repository as .*`{self.repo.name}`")
        self.assertRegex(out, r"ok\s+model")
        self.assertRegex(out, r"ok\s+queue\s+Empty\.")

    def test_each_missing_piece_is_named(self):
        git(self.repo, "init", "-q")
        code, out, _ = self.init({"configured": True, "armed": False}, which=None)
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"missing\s+hook\s+the post-commit review hook is not installed")
        self.assertRegex(out, r"missing\s+review\s+`ikigai-gonk` is not on PATH")
        self.assertRegex(out, r"missing\s+queue\s+gonk's review queue is not armed")

    def test_an_unserved_root_says_what_the_operator_adds(self):
        code, out, _ = self.init(None, served=False)
        self.assertRegex(out, r"missing\s+hook\s+not a git working tree yet")
        self.assertIn(f'gonk.browse.root = "{self.repo.name}=', out)

    def test_a_quic_tree_is_told_its_commits_queue_nothing_and_that_is_not_missing(self):
        wire(self.repo, quic=True)
        code, out, _ = run_tool(gonk.main, self.repo, ["init"])
        self.assertRegex(out, r"absent\s+review\s+this tree reaches gonk over QUIC")


class ReviewHook(unittest.TestCase):
    """hooks/git/post-commit and review-on-commit.py, against a fake `ikigai-gonk` that
    records what it was asked: never a live gonk."""

    def setUp(self):
        self.repo = scratch(self, "lib")
        new.generate(self.repo, "python", ["review-every-commit"])
        wire(self.repo)
        self.bin = self.repo.parent / "bin"
        self.bin.mkdir()
        self.log = self.repo.parent / "requests.log"
        fake = self.bin / "ikigai-gonk"
        fake.write_text(f'#!/bin/sh\necho "$*" >> "{self.log}"\n')
        fake.chmod(0o755)
        git(self.repo, "init", "-q")
        (self.repo / "Cargo.lock").write_text("machine-written\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "everything it wrote")
        self.hook = load_tool(self.repo, "hooks/git/review-on-commit.py", "review_on_commit")

    def which(self, name):
        return str(self.bin / name) if name == "ikigai-gonk" else None

    def test_one_request_per_file_the_commit_touched_skipping_lockfiles(self):
        (self.repo / "AGENTS.md").write_text("changed\n")
        (self.repo / "README.md").unlink()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "change one, delete one")
        commands, note = self.hook.requests(self.repo, which=self.which)
        self.assertIsNone(note)
        self.assertEqual(
            commands, [[str(self.bin / "ikigai-gonk"), "review", "request", "lib", "AGENTS.md"]]
        )
        self.assertEqual(self.hook.drop(commands), 0)
        self.assertEqual(self.log.read_text(), "review request lib AGENTS.md\n")

    def test_the_root_and_config_come_from_process_toml(self):
        process = self.repo / ".gonk" / "process.toml"
        process.write_text(
            process.read_text()
            .replace('# root = "my-repo"', 'root = "library"')
            .replace('# config = "~/.config/ikigai/config.toml"', 'config = "/g/config.toml"')
        )
        commands, _ = self.hook.requests(self.repo, which=self.which)
        self.assertTrue(commands)
        self.assertNotIn(["Cargo.lock"], [c[4:5] for c in commands])
        for command in commands:
            self.assertEqual(command[3], "library")
            self.assertEqual(command[-2:], ["--config", "/g/config.toml"])

    def test_what_it_cannot_do_it_says_and_queues_nothing(self):
        commands, note = self.hook.requests(self.repo, which=lambda name: None)
        self.assertEqual(commands, [])
        self.assertIn("`ikigai-gonk` is not on PATH", note)
        wire(self.repo, quic=True)
        commands, note = self.hook.requests(self.repo, which=self.which)
        self.assertEqual(commands, [])
        self.assertIn("over QUIC", note)
        (self.repo / ".gonk" / "local.toml").unlink()
        commands, note = self.hook.requests(self.repo, which=self.which)
        self.assertEqual(commands, [])
        self.assertIn("does not say how to reach gonk", note)

    def test_a_repository_whose_process_is_not_review_does_nothing_silently(self):
        process = self.repo / ".gonk" / "process.toml"
        process.write_text(
            process.read_text().replace('process = ["review-every-commit"]', 'process = ["solo"]')
        )
        self.assertEqual(self.hook.requests(self.repo, which=self.which), ([], None))

    def test_a_real_commit_runs_it_detached_and_never_fails(self):
        git(self.repo, "config", "core.hooksPath", "hooks/git")
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        (self.repo / "AGENTS.md").write_text("changed again\n")
        git(self.repo, "add", "AGENTS.md")
        git(self.repo, "commit", "-q", "-m", "one file", env=env)
        deadline = time.monotonic() + 15
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.log.read_text(), "review request lib AGENTS.md\n")
        # With no ikigai-gonk at all the commit still succeeds, and the hook says why.
        bare = dict(
            os.environ, PATH=os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
        )
        (self.repo / "AGENTS.md").write_text("and again\n")
        git(self.repo, "add", "AGENTS.md")
        done = git(self.repo, "commit", "-q", "-m", "no gonk here", env=bare)
        self.assertIn("not on PATH", done.stderr)


class Hub(unittest.TestCase):
    def setUp(self):
        self.repo = scratch(self)
        new.generate(self.repo, "python", ["hub"])
        wire(self.repo)
        self.hub = load_tool(self.repo, "tools/hub.py", "hub")

    def test_an_arc_is_its_brief_filed_and_linked(self):
        brief = self.repo / "brief.md"
        brief.write_text("## Why\n\nBecause.\n")
        iri = "urn:iki:ledger:acme:item:01abc"
        fake = FakeIkigai([("append", ok(f"acme#7 {iri}\n")), ("link", ok("linked\n"))])
        argv = ["arc", "Do the thing", "--brief", str(brief), "--parent", "4"]
        code, out, _ = run_tool(self.hub.main, self.repo, [*argv, "--after", "3"], fake)
        self.assertEqual(code, 0, out)
        (append, body), parent, after = fake.calls
        self.assertEqual(append, "sink urn:gk:iki:ledger:acme:append labels=arc")
        self.assertEqual(body, "Do the thing\n\n## Why\n\nBecause.")
        self.assertEqual(parent, (f"sink urn:gk:iki:ledger:acme:link item={iri} type=parent", "4"))
        self.assertEqual(after, ("sink urn:gk:iki:ledger:acme:link item=3 type=blocks", iri))
        self.assertIn("linked  acme#7 has parent 4", out)

    def test_an_empty_brief_is_refused(self):
        brief = self.repo / "brief.md"
        brief.write_text("\n")
        code, _, err = run_tool(self.hub.main, self.repo, ["arc", "T", "--brief", str(brief)])
        self.assertEqual(code, 2)
        self.assertIn("is empty", err)

    def test_dispatch_makes_a_tree_and_a_holder_of_its_own(self):
        git(self.repo, "init", "-q")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "start")
        dest = self.repo.parent / "arc-7"
        code, out, _ = run_tool(self.hub.main, self.repo, ["dispatch", "7", str(dest)])
        self.assertEqual(code, 0, out)
        cfg = gonk.load(dest)
        self.assertEqual(cfg.holder, "arc-7")
        self.assertEqual(cfg.target.socket, str(self.repo / "g.sock"))
        self.assertEqual(git(dest, "branch", "--show-current").stdout.strip(), "arc-7")
        # A second dispatch to the same directory is refused rather than reusing a tree.
        code, _, err = run_tool(self.hub.main, self.repo, ["dispatch", "7", str(dest)])
        self.assertEqual(code, 2)
        self.assertIn("exists", err)


class Audit(unittest.TestCase):
    def setUp(self):
        self.repo = scratch(self, "library")
        new.generate(self.repo, "python", ["audits"])
        wire(self.repo)
        self.audit = load_tool(self.repo, "tools/audit.py", "audit")

    def test_it_files_the_template_with_the_files_and_the_budget(self):
        fake = FakeIkigai([("append", ok("acme#9 urn:x\n"))])
        files = [str(self.repo / "README.md"), str(self.repo / "tools" / "gonk.py")]
        code, out, _ = run_tool(self.audit.main, self.repo, [*files, "--budget", "40"], fake)
        self.assertEqual(code, 0, out)
        ((command, body),) = fake.calls
        self.assertEqual(command, "sink urn:gk:iki:ledger:acme:append labels=audit")
        self.assertTrue(body.startswith("Audit library: 2 file(s), 40 tool calls\n\n"), body)
        self.assertIn("Repository: `library`. Budget: 40 tool calls.", body)
        self.assertIn("- `README.md`\n- `tools/gonk.py`", body)
        self.assertIn("A reproduction for every bug", body)
        self.assertNotIn("{files}", body)

    def test_a_file_that_is_not_there_is_refused(self):
        code, _, err = run_tool(self.audit.main, self.repo, [str(self.repo / "nope.py")])
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)
        code, _, err = run_tool(self.audit.main, self.repo, [str(ROOT / "README.md")])
        self.assertEqual(code, 2)
        self.assertIn("not in this repository", err)


if __name__ == "__main__":
    unittest.main()
