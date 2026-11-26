"""The worker's upstream guard (tools/upstream_guard.py, ledger item 4449): an unattended agent
posts to no GitHub project outside ikigai-rs. Each spelling of a repository is tested, and each
control fires through its real entry point: the `gh` wrapper, the `git` wrapper and the Claude
Code hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import upstream_guard as g  # noqa: E402

import worker  # noqa: E402

BIN = ROOT / "worker" / "guard" / "bin"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Checkout(unittest.TestCase):
    """A scratch checkout whose origin is ikigai-rs/ikigai-store, with a fake `gh` and `git` on
    PATH behind the guard's wrappers that say what they were asked."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "remote", "add", "origin", "https://github.com/ikigai-rs/ikigai-store.git")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GH_")}

    def gh(self, *args: str, env: dict | None = None) -> str | None:
        return g.check_gh(list(args), {**self.env, **(env or {})}, str(self.repo))

    def push(self, *args: str) -> str | None:
        return g.check_git(["push", *args], self.env, str(self.repo))


class GhSpellings(Checkout):
    def test_reads_go_through(self):
        for args in [
            ["issue", "list", "-R", "oxigraph/oxigraph"],
            ["pr", "view", "https://github.com/oxigraph/oxigraph/pull/1"],
            ["repo", "view", "oxigraph/oxigraph"],
            ["api", "repos/oxigraph/oxigraph/issues"],
            ["api", "-X", "GET", "repos/oxigraph/oxigraph/issues", "-f", "state=open"],
            ["api", "graphql", "-f", "query={ viewer { login } }"],
            ["auth", "status"],
            [],
        ]:
            self.assertIsNone(self.gh(*args), args)

    def test_writes_under_ikigai_rs_go_through(self):
        for args in [
            ["pr", "create", "--title", "t", "--body", "b"],
            ["pr", "create", "-R", "ikigai-rs/ikigai-store", "-t", "t"],
            ["pr", "comment", "12", "--repo=ikigai-rs/ikigai-store", "-b", "x"],
            ["issue", "comment", "https://github.com/ikigai-rs/ikigai-store/issues/3", "-b", "x"],
            ["issue", "create", "-R", "github.com/Ikigai-RS/x", "-t", "t"],
            ["api", "-X", "POST", "repos/ikigai-rs/ikigai-store/issues", "-f", "title=t"],
            ["api", "repos/{owner}/{repo}/issues/1/comments", "-f", "body=b"],
            ["api", "--method", "PATCH", "/repos/ikigai-rs/x/pulls/2"],
            ["api", "-X", "POST", "orgs/ikigai-rs/teams"],
        ]:
            self.assertIsNone(self.gh(*args), args)

    def test_every_write_command_outside_is_refused(self):
        for cmd in [
            "issue create",
            "issue comment 1",
            "issue edit 1",
            "issue close 1",
            "issue reopen 1",
            "pr create",
            "pr comment 1",
            "pr review 1",
            "pr edit 1",
            "pr close 1",
            "pr merge 1",
            "release create v1",
            "discussion create",
            "discussion comment 4",
        ]:
            why = self.gh(*cmd.split(), "-R", "oxigraph/oxigraph")
            self.assertIsNotNone(why, cmd)
            self.assertIn("oxigraph/oxigraph", why)
            self.assertIn("pull request's description or your hand-back", why)

    def test_every_spelling_of_the_repository(self):
        for args, env in [
            (["issue", "create", "-R", "oxigraph/oxigraph", "-t", "t"], None),
            (["issue", "create", "-Roxigraph/oxigraph", "-t", "t"], None),
            (["issue", "create", "--repo", "oxigraph/oxigraph"], None),
            (["issue", "create", "--repo=oxigraph/oxigraph"], None),
            (["issue", "create", "-R", "github.com/oxigraph/oxigraph"], None),
            (["issue", "create", "-R", "https://github.com/oxigraph/oxigraph"], None),
            (["issue", "create", "-t", "t"], {"GH_REPO": "oxigraph/oxigraph"}),
            (
                ["issue", "comment", "https://github.com/oxigraph/oxigraph/issues/2050", "-b", "x"],
                None,
            ),
            (["pr", "comment", "https://github.com/rudof-project/rudof/pull/963", "-b", "x"], None),
            (["issue", "create", "-R", "ikigai-rs-evil/x"], None),
            (["issue", "create", "-R", "gitlab.com/ikigai-rs/x"], None),
        ]:
            self.assertIsNotNone(self.gh(*args, env=env), (args, env))

    def test_an_explicit_ikigai_rs_repo_does_not_cover_an_outside_url(self):
        why = self.gh(
            "issue", "comment", "-R", "ikigai-rs/x", "https://github.com/oxigraph/oxigraph/issues/1"
        )
        self.assertIn("oxigraph/oxigraph", why)

    def test_implicit_repository_with_an_upstream_remote_is_refused(self):
        git(self.repo, "remote", "add", "upstream", "git@github.com:oxigraph/oxigraph.git")
        why = self.gh("pr", "create", "-t", "t")
        self.assertIn("oxigraph/oxigraph", why)
        self.assertIn("--repo ikigai-rs/<name>", why)
        self.assertIsNone(self.gh("pr", "create", "-R", "ikigai-rs/ikigai-store"))

    def test_implicit_repository_without_a_github_remote_is_refused(self):
        git(self.repo, "remote", "remove", "origin")
        self.assertIn("cannot be known", self.gh("issue", "create", "-t", "t"))

    def test_api_writes_outside_are_refused(self):
        for args, env in [
            (["api", "-X", "POST", "repos/oxigraph/oxigraph/issues"], None),
            (["api", "--method=PUT", "repos/oxigraph/oxigraph/x"], None),
            (["api", "-XDELETE", "repos/oxigraph/oxigraph/x"], None),
            (["api", "repos/oxigraph/oxigraph/issues", "-f", "title=t"], None),
            (["api", "repos/oxigraph/oxigraph/issues", "-F", "title=t"], None),
            (["api", "repos/oxigraph/oxigraph/issues", "--raw-field", "title=t"], None),
            (["api", "repos/oxigraph/oxigraph/issues", "--field=title=t"], None),
            (["api", "https://api.github.com/repos/oxigraph/oxigraph/issues", "-f", "a=b"], None),
            (
                ["api", "repos/{owner}/ikigai-gonk-starter/issues", "-f", "a=b"],
                {"GH_REPO": "oxigraph/x"},
            ),
            (["api", "-X", "POST", "gists", "-f", "a=b"], None),
            (["api", "graphql", "-f", "query=mutation { addComment(input: {}) { x } }"], None),
        ]:
            self.assertIsNotNone(self.gh(*args, env=env), (args, env))

    def test_api_placeholder_path_uses_the_checkouts_remotes(self):
        git(self.repo, "remote", "add", "upstream", "https://github.com/oxigraph/oxigraph")
        self.assertIsNotNone(self.gh("api", "repos/{owner}/{repo}/issues", "-f", "title=t"))


class GitPush(Checkout):
    def test_push_to_ikigai_rs_goes_through(self):
        git(self.repo, "remote", "add", "ssh", "git@github.com:ikigai-rs/x.git")
        git(self.repo, "remote", "add", "tok", "https://x-access-token:t@github.com/ikigai-rs/x")
        for args in [
            [],
            ["-u", "origin", "HEAD"],
            ["origin", "main"],
            ["ssh", "main"],
            ["tok"],
            ["https://github.com/ikigai-rs/x.git", "main"],
            ["ssh://git@github.com/ikigai-rs/x", "main"],
            ["--force-with-lease", "origin"],
        ]:
            self.assertIsNone(self.push(*args), args)

    def test_push_to_a_local_path_goes_through(self):
        for args in [["../bare.git", "main"], ["/tmp/bare.git"], ["file:///tmp/bare.git"], ["."]]:
            self.assertIsNone(self.push(*args), args)

    def test_push_outside_is_refused_however_named(self):
        git(self.repo, "remote", "add", "upstream", "git@github.com:oxigraph/oxigraph.git")
        git(self.repo, "remote", "add", "lab", "https://gitlab.com/ikigai-rs/x.git")
        for args in [
            ["upstream", "main"],
            ["--repo=upstream"],
            ["https://github.com/oxigraph/oxigraph.git", "HEAD"],
            ["git@github.com:rudof-project/rudof.git"],
            ["https://github.com/ikigai-rs-evil/x"],
            ["lab", "main"],
        ]:
            why = self.push(*args)
            self.assertIsNotNone(why, args)
            self.assertIn("Refused", why)

    def test_the_default_remote_and_a_push_url_are_read(self):
        git(self.repo, "remote", "set-url", "--push", "origin", "git@github.com:oxigraph/oxigraph")
        self.assertIn("oxigraph/oxigraph", self.push())
        git(self.repo, "remote", "set-url", "--push", "origin", "git@github.com:ikigai-rs/x")
        git(self.repo, "remote", "add", "upstream", "git@github.com:oxigraph/oxigraph.git")
        git(self.repo, "config", "remote.pushDefault", "upstream")
        self.assertIn("oxigraph/oxigraph", self.push())

    def test_global_options_before_push(self):
        git(self.repo, "remote", "add", "upstream", "git@github.com:oxigraph/oxigraph.git")
        why = g.check_git(["-C", str(self.repo), "-c", "a.b=c", "push", "upstream"], self.env, "/")
        self.assertIn("oxigraph/oxigraph", why)
        self.assertIsNone(g.check_git(["-C", str(self.repo), "status"], self.env, "/"))

    def test_other_git_commands_go_through(self):
        for args in [["status"], ["fetch", "upstream"], ["log", "push"], ["remote", "-v"]]:
            self.assertIsNone(g.check_git(args, self.env, str(self.repo)), args)


class ShellText(Checkout):
    def shell(self, text: str) -> str | None:
        return g.check_shell(text, self.env, str(self.repo))

    def test_each_spelling_in_a_command_line(self):
        for text in [
            "gh issue create -R oxigraph/oxigraph -t t",
            "/opt/homebrew/bin/gh issue create -R oxigraph/oxigraph",
            "GH_REPO=oxigraph/oxigraph gh issue create -t t",
            "env GH_REPO=oxigraph/oxigraph gh issue create -t t",
            "cargo test && gh pr create -R oxigraph/oxigraph",
            "true; gh api -X POST repos/oxigraph/oxigraph/issues",
            "echo x | gh issue comment https://github.com/oxigraph/oxigraph/issues/1 -F -",
            "bash -c 'gh issue create -R oxigraph/oxigraph'",
            "command git push git@github.com:oxigraph/oxigraph.git HEAD",
            "(cd /tmp && gh issue create --repo=oxigraph/oxigraph)",
            "ls\ngh issue create -R oxigraph/oxigraph",
        ]:
            self.assertIsNotNone(self.shell(text), text)

    def test_ordinary_commands_go_through(self):
        for text in [
            "gh pr create -R ikigai-rs/ikigai-store -t 'a; b' -b \"x && gh issue create\"",
            "git push -u origin HEAD && gh pr checks",
            "echo gh issue create -R oxigraph/oxigraph",
            "cargo test 2>&1",
            "grep -n 'gh pr' README.md",
        ]:
            self.assertIsNone(self.shell(text), text)


class Fires(Checkout):
    """Each control refuses through its real entry point, and lets the real program run
    otherwise."""

    def setUp(self):
        super().setUp()
        self.real = self.tmp / "real"
        self.real.mkdir()
        for name in ("gh",):
            p = self.real / name
            p.write_text('#!/bin/sh\necho "REAL $(basename "$0") $*"\n')
            p.chmod(0o755)
        self.env["PATH"] = os.pathsep.join([str(BIN), str(self.real), os.environ["PATH"]])

    def run_(self, *argv: str, stdin: str | None = None):
        return subprocess.run(
            list(argv), cwd=self.repo, env=self.env, input=stdin, capture_output=True, text=True
        )

    def test_gh_wrapper_refuses_a_post_outside(self):
        done = self.run_("gh", "issue", "create", "-R", "oxigraph/oxigraph", "-t", "t")
        self.assertEqual(done.returncode, g.WRAPPER_REFUSED, done)
        self.assertIn("not under ikigai-rs/", done.stderr)
        self.assertNotIn("REAL", done.stdout)

    def test_gh_wrapper_runs_the_real_gh_otherwise(self):
        done = self.run_("gh", "pr", "create", "-R", "ikigai-rs/ikigai-store", "-t", "t")
        self.assertEqual(done.returncode, 0, done)
        self.assertEqual(done.stdout.strip(), "REAL gh pr create -R ikigai-rs/ikigai-store -t t")

    def test_gh_api_write_outside_is_refused(self):
        done = self.run_("gh", "api", "-X", "POST", "repos/oxigraph/oxigraph/issues")
        self.assertEqual(done.returncode, g.WRAPPER_REFUSED, done)

    def test_git_wrapper_refuses_a_push_outside(self):
        git(self.repo, "remote", "add", "upstream", "git@github.com:oxigraph/oxigraph.git")
        done = self.run_("git", "push", "upstream", "HEAD")
        self.assertEqual(done.returncode, g.WRAPPER_REFUSED, done)
        self.assertIn("oxigraph/oxigraph", done.stderr)

    def test_git_wrapper_runs_the_real_git_and_pushes_locally(self):
        bare = self.tmp / "bare.git"
        git(self.tmp, "init", "-q", "--bare", str(bare))
        git(
            self.repo,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        )
        done = self.run_("git", "push", "-q", str(bare), "HEAD:refs/heads/main")
        self.assertEqual(done.returncode, 0, done)
        self.assertEqual(
            self.run_("git", "rev-parse", "--is-inside-work-tree").stdout.strip(), "true"
        )

    def test_hook_refuses_a_bash_call(self):
        event = {
            "tool_name": "Bash",
            "cwd": str(self.repo),
            "tool_input": {"command": "gh issue create -R oxigraph/oxigraph"},
        }
        done = self.run_(
            sys.executable,
            str(ROOT / "tools" / "upstream_guard.py"),
            "hook",
            stdin=json.dumps(event),
        )
        self.assertEqual(done.returncode, g.HOOK_REFUSED, done)
        self.assertIn("oxigraph/oxigraph", done.stderr)

    def test_hook_lets_other_calls_through(self):
        for event in [
            {"tool_name": "Bash", "cwd": str(self.repo), "tool_input": {"command": "gh pr checks"}},
            {"tool_name": "Edit", "tool_input": {"file_path": "x"}},
        ]:
            done = self.run_(
                sys.executable,
                str(ROOT / "tools" / "upstream_guard.py"),
                "hook",
                stdin=json.dumps(event),
            )
            self.assertEqual(done.returncode, 0, done)

    def test_hook_fails_closed_on_input_it_cannot_read(self):
        done = self.run_(
            sys.executable, str(ROOT / "tools" / "upstream_guard.py"), "hook", stdin="not json"
        )
        self.assertEqual(done.returncode, g.HOOK_REFUSED)


class Wiring(unittest.TestCase):
    """The worker runs every agent with the wrappers first on PATH, and Claude Code with the
    hook in its settings."""

    def test_guard_env_puts_the_wrappers_first_once(self):
        env = worker.guard_env({"PATH": f"/usr/bin{os.pathsep}{BIN}", "X": "1"})
        self.assertEqual(env["PATH"].split(os.pathsep), [str(BIN), "/usr/bin"])
        self.assertEqual(env["X"], "1")

    def test_claude_gets_the_hook(self):
        argv = worker.with_guard("claude", ["claude", "-p", "brief"])
        self.assertEqual(argv[:2], ["claude", "--settings"])
        self.assertEqual(argv[3:], ["-p", "brief"])
        hook = json.loads(argv[2])["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "Bash")
        self.assertIn("upstream_guard.py", hook["hooks"][0]["command"])
        self.assertTrue(hook["hooks"][0]["command"].endswith(" hook"))

    def test_other_agents_are_left_to_the_wrappers(self):
        for harness, argv in [
            ("codex", ["codex", "exec", "x"]),
            (None, ["hermes", "-z", "x"]),
            ("claude", ["my-wrapper"]),
        ]:
            self.assertEqual(worker.with_guard(harness, argv), argv)


if __name__ == "__main__":
    unittest.main()
