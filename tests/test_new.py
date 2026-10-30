"""tools/new.py: what a generated repository carries.

That the generated repository's own tests PASS is checked by scripts/gates.sh, which
generates every target into a scratch directory and runs its suite as that repository's CI
would, so a stale template fails `just gates` and CI alike.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, gonk

spec = importlib.util.spec_from_file_location("new", ROOT / "tools" / "new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)


def scratch(test) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    return tmp / "library"


class Generate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
        cls.repo = cls.tmp / "library"
        cls.written = new.generate(cls.repo, "python")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_starter_file_it_names_exists(self):
        for rel in new.STARTER_FILES:
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_the_starters_tools_and_rules_are_copied_unchanged(self):
        for rel in [
            "AGENTS.md",
            "CLAUDE.md",
            ".gitattributes",
            "docs/windows.md",
            "tools/gonk.py",
            "hooks/claude/require-claim.py",
        ]:
            self.assertEqual((self.repo / rel).read_bytes(), (ROOT / rel).read_bytes(), rel)
        self.assertFalse((self.repo / "CLAUDE.md").is_symlink(), "a symlink breaks on Windows")
        self.assertTrue(os.access(self.repo / "tools" / "gonk.py", os.X_OK))

    def test_the_worker_loop_comes_along(self):
        """A generated project can run a worker: the loop, its sample configuration and its
        service units, unchanged, and the justfile recipes that run it. The machine's setup
        (accounts, boundary probe) stays in the starter."""
        units = sorted(p.name for p in (ROOT / "worker" / "units").iterdir())
        self.assertEqual(
            units,
            ["dev.ikigai-rs.gonk-worker.plist", "gonk-worker.service", "gonk-worker@.service"],
        )
        for rel in [
            "tools/worker.py",
            "tools/worker_repos.py",
            "worker/worker.sample.toml",
            *(f"worker/units/{name}" for name in units),
        ]:
            self.assertEqual((self.repo / rel).read_bytes(), (ROOT / rel).read_bytes(), rel)
            self.assertIn(Path(rel), self.written)
            self.assertEqual((self.repo / rel).stat().st_mode, (ROOT / rel).stat().st_mode, rel)
        names = [n for n, _ in new.recipe_blocks((self.repo / "justfile").read_text()) if n]
        for recipe in ["worker", "worker-once", "worker-plan", "worker-forget"]:
            self.assertIn(recipe, names)
        for rel in [
            "worker/README.md",
            "worker/worker-setup.sh",
            "worker/probe.sh",
            "worker/tests",
        ]:
            self.assertFalse((self.repo / rel).exists(), rel)

    def test_the_generated_worker_runs_from_the_generated_tree(self):
        """Its `plan`, as a separate process in the generated repository: worker.py finds
        gonk.py beside it, reads the sample configuration copied into place and the gonk
        wiring init writes, and gets as far as asking gonk (a socket nobody listens on), once
        its operator has named who may route work to it (the sample's router is a placeholder,
        refused until then)."""
        repo = scratch(self)
        new.generate(repo, "python")
        sample = (repo / "worker" / "worker.sample.toml").read_text()
        [line] = [w for w in sample.splitlines() if w.startswith("routers = ")]
        router = 'routers = ["urn:iki:gonk:passkey:dispatcher-test"]'
        (repo / ".gonk" / "worker.toml").write_text(sample.replace(line, router))
        (repo / ".gonk" / "gonk.toml").write_text('ledger = "library"\n')
        (repo / ".gonk" / "local.toml").write_text(
            f'transport = "socket"\nsocket = "{repo / "none.sock"}"\nholder = "w1"\n'
        )
        done = subprocess.run(
            [sys.executable, "tools/worker.py", "plan"], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("gonk did not answer next", done.stderr)

    def test_the_sample_and_the_target_are_both_there(self):
        for rel in [
            "README.md",
            "openspec/vectors.json",
            "openspec/specs/loans/spec.md",
            "openspec/changes/add-holds/tasks.md",
            ".gonk/queries/open-items-for-artifact.rq",
            ".gonk/scripts/next-for-artifact.plan.ttl",
            "artifacts/service/lending/domain.py",
            "artifacts/cli/library.py",
            "scripts/test.py",
            ".github/workflows/ci.yml",
        ]:
            self.assertTrue((self.repo / rel).is_file(), rel)
        # The sample's README replaces nothing of the starter's: the starter's is not copied.
        self.assertIn("A lending library", (self.repo / "README.md").read_text())
        # What belongs to the starter alone stays behind.
        for rel in [
            "tools/new.py",
            "templates",
            "scripts/gates.sh",
            "LICENSE-MIT",
            "justfile.recipes",
        ]:
            self.assertFalse((self.repo / rel).exists(), rel)

    def test_the_justfile_keeps_the_gonk_recipes_and_swaps_the_starters_own(self):
        names = [n for n, _ in new.recipe_blocks((self.repo / "justfile").read_text()) if n]
        for recipe in ["init", "seed", "next", "claim", "report", "close", "gates", "serve"]:
            self.assertIn(recipe, names)
        self.assertNotIn("new", names)
        self.assertEqual(names.count("gates"), 1)
        self.assertIn("python3 scripts/test.py", (self.repo / "justfile").read_text())

    def test_the_generated_layout_and_artifact_lines_pass_init_checks(self):
        layout = gonk.read_toml(self.repo / ".gonk" / "layout.toml")
        _, bad = gonk.layout_problems(self.repo, layout)
        self.assertEqual(bad, [])
        self.assertEqual(gonk.artifact_problems(self.repo, layout), [])
        names = {name for _, name in gonk.named_artifacts(self.repo, layout)}
        self.assertEqual(names, {"service", "cli"})

    def test_a_directory_that_is_not_empty_is_refused(self):
        with self.assertRaises(SystemExit) as refused:
            new.generate(self.repo, "python")
        self.assertIn("not empty", str(refused.exception))

    def test_an_unknown_target_is_refused_naming_the_known_ones(self):
        with self.assertRaises(SystemExit) as refused:
            new.generate(scratch(self), "cobol")
        self.assertIn("this starter has deno, go, python, rust", str(refused.exception))


class OutsideTheStarter(unittest.TestCase):
    def test_a_copy_outside_the_starter_refuses_in_one_sentence(self):
        """A hand copy of new.py in a generated repository has no profiles/ to read."""
        repo = scratch(self)
        (repo / "tools").mkdir(parents=True)
        shutil.copy(ROOT / "tools" / "new.py", repo / "tools" / "new.py")
        done = subprocess.run(
            [sys.executable, "tools/new.py", "--help"], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("new.py: runs only in ikigai-gonk-starter", done.stderr)
        self.assertIn("no profiles/profiles.toml here", done.stderr)
        self.assertNotIn("Traceback", done.stderr)


class GenerateRust(unittest.TestCase):
    """`--target rust`: the same sample, the same vectors, in one cargo workspace.

    That the generated repository's tests PASS is checked by scripts/gates.sh (and so by CI),
    which generates every target into a scratch directory and runs its suite. These tests
    check what a generated Rust repository carries, and that the target left the Python
    target's output alone.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
        cls.repo = cls.tmp / "library"
        cls.written = new.generate(cls.repo, "rust")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_targets_crate_files_are_there(self):
        for rel in [
            "Cargo.toml",
            "artifacts/service/Cargo.toml",
            "artifacts/service/src/domain.rs",
            "artifacts/service/src/http.rs",
            "artifacts/service/src/json.rs",
            "artifacts/service/src/date.rs",
            "artifacts/service/tests/vectors.rs",
            "artifacts/service/tests/http.rs",
            "artifacts/service/tests/common/mod.rs",
            "artifacts/cli/Cargo.toml",
            "artifacts/cli/src/lib.rs",
            "artifacts/cli/tests/cli.rs",
            "scripts/test.py",
            ".github/workflows/ci.yml",
            "README.md",
            ".gitignore",
        ]:
            self.assertTrue((self.repo / rel).is_file(), rel)

    def test_the_workspace_holds_both_artifacts_and_no_third_party_dependency(self):
        """Standard library only, so `cargo test` needs no network and no lock file to resolve."""
        root = gonk.read_toml(self.repo / "Cargo.toml")
        self.assertEqual(
            sorted(root["workspace"]["members"]), ["artifacts/cli", "artifacts/service"]
        )
        for crate in ["artifacts/service", "artifacts/cli"]:
            manifest = gonk.read_toml(self.repo / crate / "Cargo.toml")
            for section in ["dependencies", "dev-dependencies", "build-dependencies"]:
                for name, spec in manifest.get(section, {}).items():
                    # The sample's own crates, by path, and nothing else: a version would be
                    # a fetch, and this sample fetches nothing.
                    self.assertIn("path", spec, f"{crate} {section} {name} {spec}")

    def test_the_vectors_the_target_runs_are_the_samples_own(self):
        """One vectors.json, the language-neutral half: a Rust test that ran another file
        would not be the same scenarios."""
        for rel in ["openspec/vectors.json", "tests/test_specs_and_vectors.py"]:
            self.assertEqual(
                (self.repo / rel).read_bytes(), (ROOT / "templates" / "library" / rel).read_bytes()
            )
        # The service's vectors test reads that file and runs every scenario twice: against
        # the rules, and over HTTP.
        vectors = (self.repo / "artifacts/service/tests/vectors.rs").read_text()
        for wanted in [
            "openspec",
            "vectors.json",
            "every_scenario_against_the_rules",
            "every_scenario_over_http",
        ]:
            self.assertIn(wanted, vectors, wanted)
        # The CLI's tests drive the real service, so they exercise the same rules the
        # vectors do, through the one client a person would use.
        cli = (self.repo / "artifacts/cli/tests/cli.rs").read_text()
        self.assertIn("Server::start", cli)

    def test_the_rust_recipes_swap_the_starters_own(self):
        text = (self.repo / "justfile").read_text()
        names = [n for n, _ in new.recipe_blocks(text) if n]
        for recipe in ["init", "seed", "next", "claim", "report", "close", "gates"]:
            self.assertIn(recipe, names)
        self.assertNotIn("new", names)
        self.assertEqual(names.count("gates"), 1)
        self.assertIn("python3 scripts/test.py", text)
        self.assertNotIn("python3 artifacts/service/serve.py", text)

    def test_the_target_did_not_change_the_python_targets_output(self):
        """A target replaces files in its own layer only; python/ is untouched."""
        python = scratch(self)
        new.generate(python, "python")
        rust = scratch(self)
        new.generate(rust, "rust")
        shared = ["openspec/vectors.json", "AGENTS.md", "tools/gonk.py", "README.md"]
        # README.md is the target's, so it differs; the rest is the sample's or the
        # starter's and is byte-for-byte the same in both.
        for rel in [r for r in shared if r != "README.md"]:
            self.assertEqual((python / rel).read_bytes(), (rust / rel).read_bytes(), rel)
        # The Python target's artifacts and entry point are exactly what python/ holds.
        for rel in ["artifacts/service/lending/domain.py", "artifacts/cli/library.py"]:
            self.assertEqual(
                (python / rel).read_bytes(),
                (ROOT / "templates" / "python" / rel).read_bytes(),
                rel,
            )
            self.assertFalse((rust / rel).exists(), rel)


class GenerateDeno(unittest.TestCase):
    """`--target deno`: the same sample, the same vectors, in TypeScript on Deno's standard
    library alone — no npm, no imports from a registry.

    That the generated repository's tests PASS is checked by scripts/gates.sh (and so by CI),
    which generates every target into a scratch directory and runs its suite. These tests
    check what a generated Deno repository carries, and that the target left the Python and
    Rust targets' output alone.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
        cls.repo = cls.tmp / "library"
        cls.written = new.generate(cls.repo, "deno")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_targets_files_are_there(self):
        for rel in [
            "deno.json",
            "artifacts/service/serve.ts",
            "artifacts/service/src/date.ts",
            "artifacts/service/src/domain.ts",
            "artifacts/service/src/http.ts",
            "artifacts/service/src/json.ts",
            "artifacts/service/tests/vectors_test.ts",
            "artifacts/service/tests/http_test.ts",
            "artifacts/service/tests/common.ts",
            "artifacts/cli/library.ts",
            "artifacts/cli/src/lib.ts",
            "artifacts/cli/tests/cli_test.ts",
            "scripts/test.py",
            ".github/workflows/ci.yml",
            "README.md",
            ".gitignore",
        ]:
            self.assertTrue((self.repo / rel).is_file(), rel)

    def test_the_target_imports_nothing_from_a_registry(self):
        """Standard library only: every import is relative or a Deno global, so `deno test`
        needs no network and no lock file to resolve. A bare specifier (`npm:`, `jsr:`, an
        `http(s)://` URL, or a package name) would be a fetch."""
        specifiers = re.compile(r'from\s+"([^"]+)"')
        for rel in [
            "artifacts/service/serve.ts",
            "artifacts/cli/library.ts",
            *(f"artifacts/service/src/{name}.ts" for name in ["date", "domain", "http", "json"]),
            *(
                f"artifacts/service/tests/{name}.ts"
                for name in ["common", "vectors_test", "http_test"]
            ),
            "artifacts/cli/src/lib.ts",
            "artifacts/cli/tests/cli_test.ts",
        ]:
            text = (self.repo / rel).read_text()
            for specifier in specifiers.findall(text):
                self.assertTrue(
                    specifier.startswith("./") or specifier.startswith("../"),
                    f"{rel}: {specifier!r} is not a relative import",
                )

    def test_the_vectors_the_target_runs_are_the_samples_own(self):
        """One vectors.json, the language-neutral half: a Deno test that ran another file
        would not be the same scenarios."""
        for rel in ["openspec/vectors.json", "tests/test_specs_and_vectors.py"]:
            self.assertEqual(
                (self.repo / rel).read_bytes(), (ROOT / "templates" / "library" / rel).read_bytes()
            )
        # The service's vectors test reads that file and runs every scenario twice: against
        # the rules, and over HTTP.
        vectors = (self.repo / "artifacts/service/tests/vectors_test.ts").read_text()
        for wanted in [
            "openspec",
            "vectors.json",
            "every scenario against the rules",
            "every scenario over HTTP",
        ]:
            self.assertIn(wanted, vectors, wanted)
        # The CLI's tests drive the real service, so they exercise the same rules the
        # vectors do, through the one client a person would use.
        cli = (self.repo / "artifacts/cli/tests/cli_test.ts").read_text()
        self.assertIn("serve(", cli)

    def test_the_entry_point_fails_closed_without_deno(self):
        """A missing toolchain must not read as a passing suite: `scripts/test.py` exits
        non-zero, naming what is missing, when `deno` is not on PATH."""
        script = (self.repo / "scripts/test.py").read_text()
        self.assertIn("shutil.which", script)
        self.assertIn("NOTHING WAS TESTED", script)

    def test_the_gate_provisions_deno_when_the_runner_has_none(self):
        """CI's ubuntu runner carries no Deno, and the starter's CI is `scripts/gates.sh`
        alone, so the gate fetches the pinned release itself — checksum first, into a scratch
        directory — rather than depending on a workflow setup step (a worker's token may not
        push a `.github/workflows/` change). A machine that has `deno` keeps its own."""
        gate = (ROOT / "scripts" / "gates.sh").read_text()
        self.assertIn("command -v deno", gate)
        self.assertIn("sha256", gate)
        self.assertIn("denoland/deno/releases/download", gate)
        # Every digest the gate names is a 64-hex SHA-256, and each asset has one.
        assets = re.findall(r"echo \"(deno-[a-z0-9_-]+)\"", gate)
        self.assertEqual(len(assets), len(set(assets)), assets)
        for digest in re.findall(r"echo \"([0-9a-f]{64})\"", gate):
            self.assertEqual(len(digest), 64)

    def test_the_deno_recipes_swap_the_starters_own(self):
        text = (self.repo / "justfile").read_text()
        names = [n for n, _ in new.recipe_blocks(text) if n]
        for recipe in ["init", "seed", "next", "claim", "report", "close", "gates"]:
            self.assertIn(recipe, names)
        self.assertNotIn("new", names)
        self.assertEqual(names.count("gates"), 1)
        self.assertIn("python3 scripts/test.py", text)
        self.assertNotIn("python3 artifacts/service/serve.py", text)
        self.assertNotIn("cargo run", text)

    def test_the_target_did_not_change_the_python_or_rust_targets_output(self):
        """A target replaces files in its own layer only; python/ and rust/ are untouched."""
        deno = scratch(self)
        new.generate(deno, "deno")
        python = scratch(self)
        new.generate(python, "python")
        rust = scratch(self)
        new.generate(rust, "rust")
        # The shared, language-neutral half is byte-for-byte the same in all three.
        for rel in ["openspec/vectors.json", "AGENTS.md", "tools/gonk.py"]:
            self.assertEqual((python / rel).read_bytes(), (deno / rel).read_bytes(), rel)
            self.assertEqual((rust / rel).read_bytes(), (deno / rel).read_bytes(), rel)
        # The Python target's artifacts and entry point are exactly what python/ holds, and
        # the Rust target's what rust/ holds: neither came through the Deno target.
        for rel in ["artifacts/service/lending/domain.py", "artifacts/cli/library.py"]:
            self.assertFalse((deno / rel).exists(), rel)
        for rel in ["artifacts/service/src/domain.rs", "artifacts/cli/src/lib.rs"]:
            self.assertFalse((deno / rel).exists(), rel)


class GenerateGo(unittest.TestCase):
    """`--target go`: the same sample, the same vectors, in one Go module on the standard
    library alone — no third-party module, no go.sum.

    That the generated repository's tests PASS is checked by scripts/gates.sh (and so by CI),
    which generates every target into a scratch directory and runs its suite. These tests
    check what a generated Go repository carries, and that the target left the other
    targets' output alone.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gonk-new-"))
        cls.repo = cls.tmp / "library"
        cls.written = new.generate(cls.repo, "go")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_targets_files_are_there(self):
        for rel in [
            "go.mod",
            "artifacts/service/serve.go",
            "artifacts/service/lending/date.go",
            "artifacts/service/lending/domain.go",
            "artifacts/service/lending/http.go",
            "artifacts/service/lending/vectors_test.go",
            "artifacts/service/lending/http_test.go",
            "artifacts/service/lending/common_test.go",
            "artifacts/cli/library.go",
            "artifacts/cli/client/client.go",
            "artifacts/cli/client/client_test.go",
            "scripts/test.py",
            "README.md",
            ".gitignore",
        ]:
            self.assertTrue((self.repo / rel).is_file(), rel)

    def test_the_target_imports_nothing_outside_the_standard_library(self):
        """Standard library only: go.mod requires nothing, and an import is either the
        standard library (no dot in its first path element) or this module's own."""
        mod = (self.repo / "go.mod").read_text()
        self.assertNotIn("require", mod)
        self.assertFalse((self.repo / "go.sum").exists())
        for path in sorted((self.repo / "artifacts").rglob("*.go")):
            text = path.read_text()
            blocks = re.findall(r"^import \((.*?)^\)", text, re.S | re.M)
            specifiers = re.findall(r'"([^"]+)"', "\n".join(blocks))
            specifiers += re.findall(r'^import "([^"]+)"', text, re.M)
            self.assertTrue(specifiers, path)
            for specifier in specifiers:
                first = specifier.split("/")[0]
                self.assertTrue(
                    "." not in first or specifier.startswith("library-lending/"),
                    f"{path.name}: {specifier!r} is not the standard library or this module",
                )

    def test_the_vectors_the_target_runs_are_the_samples_own(self):
        for rel in ["openspec/vectors.json", "tests/test_specs_and_vectors.py"]:
            self.assertEqual(
                (self.repo / rel).read_bytes(), (ROOT / "templates" / "library" / rel).read_bytes()
            )
        vectors = (self.repo / "artifacts/service/lending/vectors_test.go").read_text()
        for wanted in [
            "vectors.json",
            "TestEveryScenarioAgainstTheRules",
            "TestEveryScenarioOverHTTP",
        ]:
            self.assertIn(wanted, vectors, wanted)
        cli = (self.repo / "artifacts/cli/client/client_test.go").read_text()
        self.assertIn("lending.Serve(", cli)

    def test_the_entry_point_fails_closed_without_go(self):
        script = (self.repo / "scripts/test.py").read_text()
        self.assertIn("shutil.which", script)
        self.assertIn("NOTHING WAS TESTED", script)
        self.assertIn("go test", script)

    def test_the_gate_provisions_go_when_the_runner_has_none(self):
        """Like Deno: the gate fetches the pinned release itself, checksum first, rather than
        depending on a workflow setup step. A machine that has `go` keeps its own."""
        gate = (ROOT / "scripts" / "gates.sh").read_text()
        self.assertIn("command -v go ", gate)
        self.assertIn("https://go.dev/dl/", gate)
        digests = re.findall(r'echo "([0-9a-f]{64})"', gate)
        self.assertEqual(len(digests), len(set(digests)))
        self.assertGreaterEqual(
            len(re.findall(r'"go\$GO_VERSION\.[a-z0-9-]+\.tar\.gz"\) echo', gate)), 4
        )

    def test_the_go_recipes_swap_the_starters_own(self):
        text = (self.repo / "justfile").read_text()
        names = [n for n, _ in new.recipe_blocks(text) if n]
        for recipe in ["init", "seed", "next", "claim", "report", "close", "gates"]:
            self.assertIn(recipe, names)
        self.assertNotIn("new", names)
        self.assertEqual(names.count("gates"), 1)
        self.assertIn("go run ./artifacts/service", text)
        self.assertNotIn("deno run", text)
        self.assertNotIn("cargo run", text)

    def test_the_target_did_not_change_the_other_targets_output(self):
        go = scratch(self)
        new.generate(go, "go")
        for target, own in [
            ("python", "artifacts/service/lending/domain.py"),
            ("rust", "artifacts/service/src/domain.rs"),
            ("deno", "artifacts/service/src/domain.ts"),
        ]:
            other = scratch(self)
            new.generate(other, target)
            for rel in ["openspec/vectors.json", "AGENTS.md", "tools/gonk.py"]:
                self.assertEqual((other / rel).read_bytes(), (go / rel).read_bytes(), rel)
            self.assertTrue((other / own).is_file(), own)
            self.assertFalse((go / own).exists(), own)
        self.assertFalse((go / "deno.json").exists())
        self.assertFalse((go / "Cargo.toml").exists())


# The rule every generated AGENTS.md carries (profiles/sections/accessibility.md).
ACCESSIBILITY_RULE = "Read `ACCESSIBILITY.md` before any change to a user interface"


class Accessibility(unittest.TestCase):
    """Every target's repository carries an ACCESSIBILITY.md to grow and the AGENTS.md rule
    that points every agent at it (ledger item 1620)."""

    def test_every_target_carries_accessibility_md_and_the_agents_rule(self):
        template = ROOT / "templates" / "library" / "ACCESSIBILITY.md"
        for target in new.targets():
            repo = scratch(self).with_name(target)
            new.generate(repo, target)
            text = (repo / "ACCESSIBILITY.md").read_text(encoding="utf-8")
            self.assertEqual(text, template.read_text(encoding="utf-8"), target)
            self.assertIn("WCAG 2.2 Level AA", text, target)
            self.assertIn("Evaluation in progress. No conformance claim.", text, target)
            self.assertIn("**Nothing in this repository has been tested", text, target)
            self.assertIn("within 7 days", text, target)
            self.assertNotIn("{{", text, target)
            self.assertIn(ACCESSIBILITY_RULE, (repo / "AGENTS.md").read_text(), target)
            harness = (repo / ".gonk" / "harness" / "harness.ttl").read_text()
            self.assertIn("guide:read-accessibility-first", harness, target)

    def test_the_starter_states_the_rule_and_has_its_own_file(self):
        self.assertIn(ACCESSIBILITY_RULE, (ROOT / "AGENTS.md").read_text())
        own = (ROOT / "ACCESSIBILITY.md").read_text(encoding="utf-8")
        self.assertIn("Evaluation in progress. No conformance claim.", own)
        self.assertNotEqual(own, (ROOT / "templates" / "library" / "ACCESSIBILITY.md").read_text())


IKIGAI_STYLE_HEADING = "## Building in the ikigai style (optional)"
IKIGAI_STYLE_INVITATION = "Neither is required"


class ResourceFirst(unittest.TestCase):
    """Every generated repository, whatever its process, carries the resource-first method
    and an optional AGENTS.md pointer before `Spec first`."""

    def test_every_process_carries_the_method_and_optional_pointer(self):
        for process in [None, ["solo"], ["hub"], ["workers"], ["audits"]]:
            repo = scratch(self)
            new.generate(repo, "python", process)
            label = ",".join(process or ["default"])
            self.assertEqual(
                (repo / "docs" / "resource-design.md").read_bytes(),
                (ROOT / "docs" / "resource-design.md").read_bytes(),
                label,
            )
            agents = (repo / "AGENTS.md").read_text()
            self.assertIn(IKIGAI_STYLE_HEADING, agents, label)
            self.assertIn(IKIGAI_STYLE_INVITATION, agents, label)
            self.assertEqual(agents.count(IKIGAI_STYLE_HEADING), 1, label)
            self.assertNotIn("## Design resource-first", agents, label)
            self.assertNotIn("## Follow the UI conventions", agents, label)
            self.assertIn("docs/resource-design.md", agents, label)
            if "## Spec first" in agents:
                self.assertLess(
                    agents.index(IKIGAI_STYLE_HEADING), agents.index("## Spec first"), label
                )
            harness = (repo / ".gonk" / "harness" / "harness.ttl").read_text()
            self.assertIn("guide:building-ikigai-style", harness, label)
            self.assertNotIn("guide:design-resource-first", harness, label)
            self.assertNotIn("guide:ui-conventions", harness, label)
            self.assertIn("Neither is required", harness, label)

    def test_the_method_names_its_steps_and_its_sources(self):
        doc = (ROOT / "docs" / "resource-design.md").read_text(encoding="utf-8")
        for step in ["Sets.", "Atoms.", "Compositions.", "Spaces:", "Views."]:
            self.assertIn(f"**{step}", doc, step)
        self.assertIn("names first, code last", doc)
        self.assertIn("## Resource map", doc)
        self.assertIn("Peter Rodgers", doc)
        self.assertIn("Tony Butterfield", doc)
        self.assertIn("https://ikigai-rs.github.io/ikigai-tutorial/", doc)

    def test_the_change_folder_skeleton_offers_a_resource_map(self):
        text = (ROOT / "openspec" / "changes" / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Resource map", text)
        self.assertIn("docs/resource-design.md", text)
        self.assertIn("if you design resource-first", text)


class UiConventions(unittest.TestCase):
    """Every generated repository, whatever its process, carries the UI decision record and the
    optional AGENTS.md pointer after the accessibility rule."""

    def test_every_process_carries_the_record_and_optional_pointer(self):
        for process in [None, ["solo"], ["hub"], ["workers"], ["audits"]]:
            repo = scratch(self)
            new.generate(repo, "python", process)
            label = ",".join(process or ["default"])
            self.assertEqual(
                (repo / "docs" / "ui-conventions.md").read_bytes(),
                (ROOT / "docs" / "ui-conventions.md").read_bytes(),
                label,
            )
            agents = (repo / "AGENTS.md").read_text()
            self.assertIn(IKIGAI_STYLE_HEADING, agents, label)
            self.assertIn(IKIGAI_STYLE_INVITATION, agents, label)
            self.assertEqual(agents.count(IKIGAI_STYLE_HEADING), 1, label)
            self.assertNotIn("## Design resource-first", agents, label)
            self.assertNotIn("## Follow the UI conventions", agents, label)
            self.assertIn("`ACCESSIBILITY.md` stays the floor", agents, label)
            self.assertIn("not built yet", agents, label)
            self.assertLess(
                agents.index("Read ACCESSIBILITY.md before changing what people see"),
                agents.index(IKIGAI_STYLE_HEADING),
                label,
            )
            harness = (repo / ".gonk" / "harness" / "harness.ttl").read_text()
            self.assertIn("guide:building-ikigai-style", harness, label)
            self.assertNotIn("guide:design-resource-first", harness, label)
            self.assertNotIn("guide:ui-conventions", harness, label)
            self.assertIn("Neither is required", harness, label)

    def test_the_record_is_the_public_copy(self):
        doc = (ROOT / "docs" / "ui-conventions.md").read_text(encoding="utf-8")
        self.assertIn("**a view is a resource**", doc)
        for n in range(1, 20):
            self.assertIn(f"| D{n} |", doc, f"D{n}")
        for private in ["localhost", "devtools", "git-personal", "ledger #"]:
            self.assertNotIn(private, doc, private)


SKIMMABLE_HEADING = "## Writing text people skim (optional)"


class SkimmableText(unittest.TestCase):
    """Every generated repository, whatever its process, carries the skimmable-text skill and
    its optional AGENTS.md pointer after the ikigai-style guides."""

    def test_every_process_carries_the_skill_and_optional_pointer(self):
        for process in [None, ["solo"], ["hub"], ["workers"], ["audits"]]:
            repo = scratch(self)
            new.generate(repo, "python", process)
            label = ",".join(process or ["default"])
            for name in ["SKILL.md", "sets.py"]:
                rel = Path("skills") / "skimmable-text" / name
                self.assertEqual((repo / rel).read_bytes(), (ROOT / rel).read_bytes(), label)
            agents = (repo / "AGENTS.md").read_text()
            self.assertEqual(agents.count(SKIMMABLE_HEADING), 1, label)
            self.assertIn("skills/skimmable-text/SKILL.md", agents, label)
            self.assertIn("Nothing requires it.", agents, label)
            self.assertLess(
                agents.index(IKIGAI_STYLE_HEADING), agents.index(SKIMMABLE_HEADING), label
            )
            harness = (repo / ".gonk" / "harness" / "harness.ttl").read_text()
            self.assertIn("guide:skimmable-text", harness, label)

    def test_the_skill_names_itself_and_its_source(self):
        skill = (ROOT / "skills" / "skimmable-text" / "SKILL.md").read_text(encoding="utf-8")
        front = skill.split("---")[1]
        self.assertIn("\nname: skimmable-text\n", front)
        self.assertIn("\ndescription: ", front)
        self.assertIn("Shreya Shankar", skill)
        self.assertIn("https://fsdatalab.github.io/blog/agent-text-at-scale/", skill)
        for heading in ["## The five principles", "## Write mode", "## Review mode"]:
            self.assertIn(heading, skill, heading)

    def test_the_skill_fixes_one_label_per_principle(self):
        # Reports are compared and counted by these labels, so they must not drift.
        skill = (ROOT / "skills" / "skimmable-text" / "SKILL.md").read_text(encoding="utf-8")
        labels = {
            "P1": "expected order",
            "P2": "distinguishing first",
            "P3": "one vocabulary",
            "P4": "meaningful variation",
            "P5": "matching tone",
        }
        for number, label in labels.items():
            self.assertIn(f"| {number} | {label} |", skill, number)
        self.assertIn("P<n> <label>", skill)


if __name__ == "__main__":
    unittest.main()
