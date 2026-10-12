"""tools/new.py: what a generated repository carries.

That the generated repository's own tests PASS is checked by scripts/gates.sh, which
generates every target into a scratch directory and runs its suite as that repository's CI
would, so a stale template fails `just gates` and CI alike.
"""

from __future__ import annotations

import importlib.util
import os
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
        self.assertEqual(units, ["dev.ikigai-rs.gonk-worker.plist", "gonk-worker.service"])
        for rel in [
            "tools/worker.py",
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
        wiring init writes, and gets as far as asking gonk (a socket nobody listens on)."""
        repo = scratch(self)
        new.generate(repo, "python")
        sample = (repo / "worker" / "worker.sample.toml").read_bytes()
        (repo / ".gonk" / "worker.toml").write_bytes(sample)
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
        self.assertIn("this starter has python, rust", str(refused.exception))


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


if __name__ == "__main__":
    unittest.main()
