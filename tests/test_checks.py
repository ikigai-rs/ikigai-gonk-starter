"""tools/checks.py: the commit-msg hook's bare-number check and the pull request's checks, each
with a passing and a failing case, run through the real entry points (ledger item 3785)."""

from __future__ import annotations

import contextlib
import doctest
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT, harness, load_hook

spec = importlib.util.spec_from_file_location("checks", ROOT / "tools" / "checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def scratch(test: unittest.TestCase) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gonk-checks-"))
    test.addCleanup(shutil.rmtree, tmp, True)
    (tmp / ".gonk").mkdir()
    return tmp


def run(argv: list[str]) -> tuple[int, str]:
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = checks.main(argv)
    return code, err.getvalue()


class TheDoctests(unittest.TestCase):
    def test_the_doctests(self):
        self.assertEqual(doctest.testmod(checks).failed, 0)


class CommitMessage(unittest.TestCase):
    def setUp(self):
        self.repo = scratch(self)

    def commit_msg(self, text: str) -> tuple[int, str]:
        msg = self.repo / "MSG"
        msg.write_text(text, encoding="utf-8")
        return run(["--repo", str(self.repo), "commit-msg", str(msg)])

    def test_a_bare_number_is_refused_in_plain_words(self):
        code, err = self.commit_msg("Fix the hook (#12)\n")
        self.assertEqual(code, 1)
        self.assertIn("#12 is a bare number", err)
        self.assertIn("ledger item 12", err)

    def test_qualified_numbers_pass(self):
        for text in [
            "Fix it (ledger item 12)",
            "See https://github.com/owner/repo/pull/12",
            "Follows owner/repo#12",
            "Follows ledger #12 and default#12",
            "Follows PR #12",
            "Subject\n\n# Please enter the commit message (#12 is in a comment line)\n",
        ]:
            self.assertEqual(self.commit_msg(text)[0], 0, text)

    def test_the_hook_script_runs_the_check(self):
        if shutil.which("git") is None or os.name == "nt":
            self.skipTest("needs git and a POSIX shell")
        repo = self.repo
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "tools").mkdir()
        (repo / "hooks" / "git").mkdir(parents=True)
        for rel in ["tools/checks.py", "tools/harness.py", "hooks/git/commit-msg"]:
            shutil.copy2(ROOT / rel, repo / rel)
        msg = repo / "MSG"
        msg.write_text("wip #3\n", encoding="utf-8")
        hook = subprocess.run(
            ["sh", str(repo / "hooks/git/commit-msg"), str(msg)],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(hook.returncode, 1, hook.stderr)
        msg.write_text("wip, ledger item 3\n", encoding="utf-8")
        hook = subprocess.run(["sh", str(repo / "hooks/git/commit-msg"), str(msg)], cwd=repo)
        self.assertEqual(hook.returncode, 0)


class PullRequest(unittest.TestCase):
    def setUp(self):
        self.repo = scratch(self)

    def pr(self, pr: dict, changed: list[str]) -> tuple[int, str]:
        event = self.repo / "event.json"
        event.write_text(json.dumps({"pull_request": pr}), encoding="utf-8")
        files = self.repo / "changed.txt"
        files.write_text("\n".join(changed), encoding="utf-8")
        return run(["--repo", str(self.repo), "pr", "--event", str(event), "--changed", str(files)])

    def test_outside_a_pull_request_nothing_is_checked(self):
        self.assertEqual(run(["--repo", str(self.repo), "pr", "--event", ""])[0], 0)

    def test_a_user_facing_change_needs_the_accessibility_section(self):
        code, err = self.pr({"title": "New page", "body": "A page."}, ["faces/x/index.html"])
        self.assertEqual(code, 1)
        self.assertIn("no `## Accessibility` section", err)
        self.assertIn("not whether anyone read", err)

    def test_with_the_section_it_passes(self):
        body = "A page.\n\n## Accessibility\n\n| Check | Result | Evidence |\n"
        self.assertEqual(self.pr({"title": "New page", "body": body}, ["faces/x/a.css"])[0], 0)

    def test_a_change_that_touches_no_user_facing_path_needs_no_section(self):
        self.assertEqual(self.pr({"title": "Tidy", "body": ""}, ["tools/gonk.py"])[0], 0)

    def test_the_user_facing_paths_are_configurable(self):
        (self.repo / ".gonk" / "checks.toml").write_text('[pr]\nuser_facing = ["ui/*"]\n')
        self.assertEqual(self.pr({"title": "t", "body": ""}, ["faces/x/a.html"])[0], 0)
        self.assertEqual(self.pr({"title": "t", "body": ""}, ["ui/view.txt"])[0], 1)

    def test_a_marked_behavior_change_needs_a_change_folder(self):
        code, err = self.pr({"title": "Behavior: claim twice", "body": ""}, ["tools/gonk.py"])
        self.assertEqual(code, 1)
        self.assertIn("marked as a behavior change", err)
        labeled = {"title": "x", "body": "", "labels": [{"name": "behavior-change"}]}
        self.assertEqual(self.pr(labeled, ["tools/gonk.py"])[0], 1)

    def test_with_a_change_folder_it_passes(self):
        changed = ["tools/gonk.py", "openspec/changes/claim-twice/proposal.md"]
        self.assertEqual(self.pr({"title": "behavior: claim twice", "body": ""}, changed)[0], 0)

    def test_an_unmarked_change_needs_no_folder(self):
        self.assertEqual(self.pr({"title": "Fix a typo", "body": ""}, ["tools/gonk.py"])[0], 0)

    def test_a_spec_that_does_not_validate_is_refused(self):
        (self.repo / "openspec" / "changes" / "half").mkdir(parents=True)
        (self.repo / "openspec" / "changes" / "half" / "proposal.md").write_text("x")
        code, err = self.pr({"title": "t", "body": ""}, ["README.md"])
        self.assertEqual(code, 1)
        self.assertIn("openspec/changes/half has no tasks.md", err)

    def test_this_repositorys_spec_validates(self):
        import spec_checks

        self.assertEqual(spec_checks.spec_problems(ROOT), [])


class ClaudeHookStaging(unittest.TestCase):
    """The optional Claude Code hook, through its data rules (tools/harness.py)."""

    def setUp(self):
        graph = harness.read_graph(ROOT / harness.GRAPH_FILE)
        rules, problems = harness.hook_rules(graph)
        self.assertEqual(problems, [])
        self.rules = {r.id: r for r in rules}

    def fires(self, cid: str, command: str) -> bool:
        [rule] = [r for c, r in self.rules.items() if c.endswith(cid)]
        return rule.fires(command)

    def test_staging_everything_is_refused_and_a_path_is_allowed(self):
        for cmd in [
            "git add -A",
            "git add .",
            "git add -u",
            "git commit -a -m x",
            "git commit -am x",
        ]:
            self.assertTrue(self.fires("hook-git-add-all", cmd), cmd)
        for cmd in ["git add tools/checks.py", "git commit -m x", "git commit --amend"]:
            self.assertFalse(self.fires("hook-git-add-all", cmd), cmd)

    def test_a_piped_gate_fires(self):
        self.assertTrue(self.fires("hook-pipe-hides-status", "just gates | tail"))
        self.assertFalse(self.fires("hook-pipe-hides-status", "just gates"))

    def test_the_hook_loads(self):
        self.assertTrue(load_hook())


class Coverage(unittest.TestCase):
    """The coverage question behind "guides without a check" returns the reasons."""

    def test_the_query_selects_the_reason(self):
        q = (ROOT / ".gonk" / "queries" / "harness-guides-without-sensor.rq").read_text()
        self.assertIn("SELECT ?guide ?label ?section ?reason", q)
        self.assertIn("OPTIONAL { ?guide harness:uncheckedBecause ?reason }", q)

    def test_the_guides_left_unchecked_are_the_three_with_reasons(self):
        g = harness.read_graph(ROOT / harness.GRAPH_FILE)
        H = harness.H
        checked = {
            o.value
            for s, p, o in g.triples
            if p.value == H + "statedBy" and H + "computational" in g.values(s, H + "execution")
        }
        unchecked = {
            s.value[len(harness.GUIDE_NS) :]: g.values(s, H + "uncheckedBecause")
            for s in g.of_type(H + "Guide")
            if s.value not in checked
        }
        self.assertEqual(
            set(unchecked), {"reproduce-before-fix", "building-ikigai-style", "skimmable-text"}
        )
        for gid, reasons in unchecked.items():
            self.assertEqual(len(reasons), 1, gid)


if __name__ == "__main__":
    unittest.main()
