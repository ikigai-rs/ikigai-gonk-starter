"""tools/worker_repos.py against a stub gh (tests/fakes/gh_repos.py): no network, no GitHub.

It appends a `[repos.<name>]` block for each of an owner's repositories missing from each
worker configuration, never edits or removes one, keeps every comment, and asks GitHub (the
stub) whether the worker may push to each enrolled repository.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
import tomllib
import unittest

from support import ROOT, scratch_repo

sys.path.insert(0, str(ROOT / "tools"))
import worker_repos  # noqa: E402

FAKES = ROOT / "tests" / "fakes"


def row(name: str, archived: bool = False, visibility: str = "PUBLIC") -> dict:
    url = f"https://github.com/acme/{name}"
    return {"name": name, "url": url, "isArchived": archived, "visibility": visibility}


ROWS = [
    row("lib"),
    row("app"),
    row("old", archived=True),
    row("secret", visibility="PRIVATE"),
    row("site.www"),
    row(".github"),
]

CLAUDE = """\
# The claude loop. A comment the tool must keep.
labels = ["worker:my-worker", "agent:claude"]   # and this one
holder = "my-worker-claude"

[agent]
preset = "claude"

# lib is here already, with a base of its own that must not change
[repos.lib]
remote = "https://github.com/acme/lib.git"
base = "develop"
"""

CODEX = """\
labels = ["worker:my-worker", "agent:codex"]
[agent]
preset = "codex"
# no newline at the end of this file"""


class Enrolling(unittest.TestCase):
    def setUp(self):
        self.repo = scratch_repo(self)
        self.state = self.repo / "gh.json"
        self.state.write_text(json.dumps({"repos": ROWS, "push": {}}))
        self.claude = self.repo / ".gonk" / "worker-claude.toml"
        self.codex = self.repo / ".gonk" / "worker-codex.toml"
        self.claude.write_text(CLAUDE)
        self.codex.write_text(CODEX)
        os.chmod(self.codex, 0o600)

    def run_tool(self, *argv: str) -> tuple[int, str]:
        gh = shlex.join([sys.executable, str(FAKES / "gh_repos.py"), "--state", str(self.state)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = worker_repos.main(["--repo", str(self.repo), "--gh", gh, *argv])
        return code, out.getvalue()

    def repos(self, path) -> dict:
        return tomllib.loads(path.read_text()).get("repos", {})

    def test_every_configuration_gets_each_missing_repository(self):
        code, out = self.run_tool("--owner", "acme")
        self.assertEqual(code, 0, out)
        claude, codex = self.repos(self.claude), self.repos(self.codex)
        self.assertEqual(sorted(claude), ["app", "lib", "site.www"])
        self.assertEqual(sorted(codex), ["app", "lib", "site.www"])
        self.assertEqual(
            codex["app"], {"remote": "https://github.com/acme/app.git", "github": "acme/app"}
        )
        # Archived, private (without the flag) and a name no `repo:` label can carry: skipped.
        self.assertIn("skip   acme/old: archived", out)
        self.assertIn("skip   acme/secret: private", out)
        self.assertIn("skip   acme/.github: not a name", out)
        self.assertIn('[repos."site.www"]', self.codex.read_text())  # a dotted name is quoted

    def test_an_existing_block_and_every_comment_stay_byte_for_byte(self):
        code, out = self.run_tool("--owner", "acme")
        self.assertEqual(code, 0, out)
        text = self.claude.read_text()
        self.assertTrue(text.startswith(CLAUDE), "the file's own text is a prefix, unchanged")
        self.assertEqual(self.repos(self.claude)["lib"]["base"], "develop")
        self.assertEqual(text.count("[repos.lib]"), 1)
        codex = self.codex.read_text()
        self.assertTrue(codex.startswith(CODEX + "\n"), "a missing final newline is supplied")
        self.assertIn("# no newline at the end of this file", codex)
        self.assertEqual(self.codex.stat().st_mode & 0o777, 0o600, "the file keeps its mode")

    def test_a_second_run_changes_nothing(self):
        self.assertEqual(self.run_tool("--owner", "acme")[0], 0)
        before = (self.claude.read_bytes(), self.codex.read_bytes())
        code, out = self.run_tool("--owner", "acme")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.claude.read_bytes(), self.codex.read_bytes()), before)
        self.assertIn("every one of the 3 is enrolled already", out)

    def test_the_same_repository_under_another_name_is_not_enrolled_twice(self):
        self.codex.write_text(CODEX + '\n[repos.application]\nremote = "x"\ngithub = "acme/app"\n')
        self.assertEqual(self.run_tool("--owner", "acme")[0], 0)
        self.assertNotIn("app", self.repos(self.codex))

    def test_a_dry_run_prints_the_diff_and_writes_nothing(self):
        before = (self.claude.read_bytes(), self.codex.read_bytes())
        code, out = self.run_tool("--owner", "acme", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.claude.read_bytes(), self.codex.read_bytes()), before)
        self.assertIn("--- a/.gonk/worker-claude.toml", out)
        self.assertIn("+[repos.app]", out)
        self.assertIn('+github = "acme/app"', out)
        self.assertIn("would  .gonk/worker-codex.toml: add 3: app, lib, site.www", out)
        self.assertEqual(list(self.repo.glob(".gonk/.*.tmp")), [])

    def test_private_only_and_exclude(self):
        code, out = self.run_tool(
            "--owner", "acme", "--include-private", "--only", "s*,app", "--exclude", "site.*"
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(self.repos(self.codex)), ["app", "secret"])

    def test_only_the_named_configuration(self):
        code, out = self.run_tool("--owner", "acme", "--config", str(self.codex))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.claude.read_text(), CLAUDE)
        self.assertIn("app", self.repos(self.codex))

    def test_no_configuration_is_a_configuration_error(self):
        self.claude.unlink()
        self.codex.unlink()
        code, out = self.run_tool("--owner", "acme")
        self.assertEqual(code, worker_repos.CONFIG)
        self.assertIn("init-agents", out)

    def test_a_failed_listing_writes_nothing(self):
        self.state.write_text("{}")
        os.chmod(self.state, 0o400)  # the stub cannot record the call, so it fails
        self.addCleanup(os.chmod, self.state, 0o600)
        code, out = self.run_tool("--owner", "acme")
        self.assertEqual(code, worker_repos.FAILED, out)
        self.assertEqual(self.claude.read_text(), CLAUDE)

    def test_check_push_lists_what_the_token_cannot_push_and_where_to_fix_it(self):
        state = json.loads(self.state.read_text())
        state["push"] = {"acme/lib": True, "acme/app": False}  # site.www: 404, not visible
        self.state.write_text(json.dumps(state))
        code, out = self.run_tool("--owner", "acme", "--check-push")
        self.assertEqual(code, worker_repos.CANNOT_PUSH, out)
        self.assertIn("cannot push to 2 of 3", out)
        self.assertIn("acme/app  (permissions.push is false)", out)
        self.assertIn("acme/site.www  (gh api: gh: Not Found (HTTP 404))", out)
        self.assertNotIn("acme/lib  (", out)
        self.assertIn("https://github.com/settings/personal-access-tokens", out)
        self.assertIn(
            "https://github.com/organizations/acme/settings/personal-access-token-requests", out
        )
        calls = json.loads(self.state.read_text())["calls"]
        asked = [c[1] for c in calls if c[0] == "api"]
        self.assertEqual(sorted(asked), ["repos/acme/app", "repos/acme/lib", "repos/acme/site.www"])

    def test_a_classic_token_is_pointed_at_the_collaborator_grants(self):
        state = json.loads(self.state.read_text())
        state["push"] = {"acme/lib": True}
        state["auth"] = "  - Token: ghp_****\n  - Token scopes: 'read:org', 'repo'"
        self.state.write_text(json.dumps(state))
        code, out = self.run_tool("--owner", "acme", "--check-push")
        self.assertEqual(code, worker_repos.CANNOT_PUSH, out)
        self.assertIn("CLASSIC token", out)
        self.assertIn("repo-protect --grant-worker", out)
        self.assertNotIn("settings/personal-access-tokens", out)

    def test_check_push_alone_asks_about_every_enrolled_repository(self):
        state = json.loads(self.state.read_text())
        state["push"] = {"acme/lib": True}
        self.state.write_text(json.dumps(state))
        code, out = self.run_tool("--check-push")
        self.assertEqual(code, 0, out)
        self.assertIn("may push to all 1 enrolled", out)
        calls = json.loads(self.state.read_text())["calls"]
        self.assertFalse(any(c[:2] == ["repo", "list"] for c in calls))


if __name__ == "__main__":
    unittest.main()
