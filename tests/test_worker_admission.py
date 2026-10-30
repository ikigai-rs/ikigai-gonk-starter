"""Organization admission and dispatch chores (ledger item 2647), without network access."""

import contextlib
import dataclasses
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from support import gonk
from worker_support import DISPATCHER, FakeLedger, Rig, worker


class Admission(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        path = self.rig.configure()
        with path.open("a") as f:
            f.write('\n[repos_from]\ngithub = "acme"\nexcept = ["secret"]\n')
        self.w = self.rig.worker(self.ledger)

    def candidates(self, name, response=None, code=0):
        self.n = self.ledger.file("Fix", ["worker:w1", f"repo:{name}"])
        if response is None:
            response = {"full_name": f"acme/{name}", "permissions": {"push": True}}
        with patch.object(
            worker.GitHub,
            "run",
            return_value=subprocess.CompletedProcess(
                [], code, json.dumps(response), "not found" if code else ""
            ),
        ) as api:
            result = self.w.candidates()
        return result, api

    def test_unlisted_repo_is_taken_and_verified(self):
        (take, skip, err), api = self.candidates("new")
        self.assertIsNone(err)
        self.assertEqual(skip, [])
        self.assertEqual(
            take[0][1], worker.Repo("new", "https://github.com/acme/new.git", "acme/new")
        )
        api.assert_called_once_with("api", "repos/acme/new")

    def test_derived_item_is_claimed_and_worked_with_real_git_and_stub_gh(self):
        n = self.ledger.file("Fix", ["worker:w1", "repo:new"])
        state = self.rig.gh()
        state.update(
            slug="acme/new",
            repositories={"acme/new": {"full_name": "acme/new", "permissions": {"push": True}}},
        )
        self.rig.gh_state.write_text(json.dumps(state))
        real_workspace = worker.make_workspace

        def local_workspace(root, repo, number):
            self.assertEqual(repo.remote, "https://github.com/acme/new.git")
            return real_workspace(
                root, dataclasses.replace(repo, remote=str(self.rig.origin)), number
            )

        with patch.object(worker, "make_workspace", side_effect=local_workspace):
            with contextlib.redirect_stdout(io.StringIO()):
                result = self.w.run_once()
        self.assertEqual(result.code, worker.HANDED_BACK, result.summary)
        self.assertEqual(self.ledger.items[n]["awaiting"], "dispatcher")
        self.assertIsNone(self.ledger.items[n]["claim"])
        state = self.rig.gh()
        self.assertIn(["api", "repos/acme/new"], state["calls"])
        self.assertEqual(state["forbidden"], [])
        self.assertEqual(state["prs"][0]["state"], "OPEN")

    def test_excluded_repo_is_skipped_without_api(self):
        (take, skip, _), api = self.candidates("secret")
        self.assertEqual(take, [])
        self.assertIn("excluded", skip[0][1])
        api.assert_not_called()

    def test_explicit_override_wins(self):
        (take, skip, _), api = self.candidates("lib")
        self.assertEqual(skip, [])
        self.assertEqual(take[0][1].remote, str(self.rig.origin))
        api.assert_not_called()

    def test_no_push_permission_is_skipped(self):
        (take, skip, _), _ = self.candidates(
            "new", {"full_name": "acme/new", "permissions": {"push": False}}
        )
        self.assertEqual(take, [])
        self.assertIn("push", skip[0][1])

    def test_redirect_outside_org_is_skipped(self):
        (take, skip, _), _ = self.candidates(
            "new", {"full_name": "outside/new", "permissions": {"push": True}}
        )
        self.assertEqual(take, [])
        self.assertIn("outside", skip[0][1])

    def test_missing_or_malformed_permission_data_is_never_assumed(self):
        for response in (
            {},
            [],
            {"full_name": "acme/new"},
            {"full_name": "acme/new", "permissions": {"push": "true"}},
        ):
            with self.subTest(response=response):
                (take, skip, _), _ = self.candidates("new", response)
                self.assertEqual(take, [])
                self.assertTrue(skip)

    def test_missing_repo_is_skipped(self):
        (take, skip, _), _ = self.candidates("new", code=1)
        self.assertEqual(take, [])
        self.assertIn("not found", skip[0][1])

    def test_outside_org_name_is_skipped_without_api(self):
        (take, skip, _), api = self.candidates("outside/new")
        self.assertEqual(take, [])
        self.assertIn("allowed GitHub organization", skip[0][1])
        api.assert_not_called()

    def test_explicit_stanza_overrides_exclusion_org_remote_and_base(self):
        self.w.wc.repos_except.append("lib")
        self.w.wc.repos["lib"] = worker.Repo("lib", "ssh://mirror/lib", "other/lib", "develop")
        (take, skip, _), api = self.candidates("lib")
        self.assertEqual(skip, [])
        self.assertEqual(take[0][1], self.w.wc.repos["lib"])
        api.assert_not_called()

    def test_new_repositories_are_resolved_on_the_same_running_worker(self):
        (take, skip, _), _ = self.candidates("new")
        self.assertEqual([r.name for _, r in take], ["new"])
        self.assertEqual(skip, [])
        self.ledger.items[self.n]["status"] = "closed"
        (take, skip, _), _ = self.candidates("newer")
        self.assertEqual([r.name for _, r in take], ["newer"])
        self.assertEqual(skip, [])
        self.assertNotIn("newer", self.w.wc.repos)

    def test_router_authorization_still_required(self):
        n = self.ledger.file("Not dispatched", ["worker:w1", "repo:new"], dispatched_by=None)
        with patch.object(worker.GitHub, "run") as api:
            take, skip, _ = self.w.candidates()
        self.assertEqual(take, [])
        self.assertIn("no dispatch by a router", dict(skip)[f"acme#{n}"])
        api.assert_not_called()

    def test_derived_permission_failure_is_explained_once(self):
        n = self.ledger.file("Fix", ["worker:w1", "repo:new"])
        with patch.object(
            worker.GitHub,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", "HTTP 404: Not Found"),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                self.w.run_once()
                self.w.run_once()
                self.rig.worker(self.ledger).run_once()
        notes = self.ledger.human_comments(n)
        self.assertEqual(len(notes), 1)
        self.assertIn("HTTP 404", notes[0]["text"])
        self.assertIsNone(self.ledger.items[n]["claim"])


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        self.n = self.ledger.file("Fix", ["repo:new"], dispatched_by=None)

    def dispatch(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = gonk.main(
                [
                    "--repo",
                    str(self.rig.checkout),
                    "dispatch",
                    str(self.n),
                    "--labels",
                    "worker:w1",
                    "--as",
                    DISPATCHER,
                    *args,
                ],
                self.ledger,
            )
        return code, out.getvalue()

    def test_priority_and_rank(self):
        code, said = self.dispatch()
        self.assertEqual(code, 0, said)
        self.assertEqual(self.ledger.items[self.n].get("priority"), 3)
        self.assertIn("rank 1", said)

    def test_discover_coverage_by_label(self):
        status = self.ledger.file("Status", ["worker-status:w1"], dispatched_by=None)
        self.ledger.add_comment(
            status, "Worker: holder=w1 labels=worker:w1 repos= repos_from=acme except=secret"
        )
        code, said = self.dispatch()
        self.assertEqual(code, 0, said)
        self.assertNotIn("warning:", said)

    def test_existing_priority_is_preserved_even_with_override(self):
        self.ledger.items[self.n]["priority"] = 0
        code, said = self.dispatch("--priority", "7")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.ledger.items[self.n]["priority"], 0)
        self.assertNotIn("set priority", said)

    def test_custom_priority_keeps_the_dispatch_digest_valid(self):
        code, said = self.dispatch("--priority", "5")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.ledger.items[self.n]["priority"], 5)
        self.assertIsNone(
            worker.dispatch_problem(self.ledger.items[self.n], [DISPATCHER], ["worker:w1"])
        )
        self.assertTrue(any(":item:1 priority=5" in c for c, _ in self.ledger.calls))

    def test_keep_unprioritized_warns_and_prints_rank(self):
        code, said = self.dispatch("--keep-unprioritized")
        self.assertEqual(code, 0, said)
        self.assertIsNone(self.ledger.items[self.n].get("priority"))
        self.assertIn("remains unprioritized", said)
        self.assertIn("rank 1", said)

    def test_coverage_names_exclusion_reason(self):
        status = self.ledger.file("Status", ["worker-status:w1"], dispatched_by=None)
        self.ledger.add_comment(
            status, "Worker: holder=w1 labels=worker:w1 repos= repos_from=acme except=new"
        )
        code, said = self.dispatch()
        self.assertEqual(code, 0, said)
        self.assertIn("excluded by [repos_from] except", said)

    def test_explicit_coverage_wins_over_org_exclusion(self):
        status = self.ledger.file("Status", ["worker-status:w1"], dispatched_by=None)
        self.ledger.add_comment(
            status, "Worker: holder=w1 labels=worker:w1 repos=new repos_from=acme except=new"
        )
        code, said = self.dispatch()
        self.assertEqual(code, 0, said)
        self.assertNotIn("warning:", said)

    def test_latest_announcement_across_status_items_decides_coverage(self):
        old = self.ledger.file("Old", ["worker-status:w1"], dispatched_by=None)
        new = self.ledger.file("New", ["worker-status:w1"], dispatched_by=None)
        self.ledger.add_comment(old, "Worker: holder=w1 labels=worker:w1 repos=new")
        self.ledger.add_comment(new, "Worker: holder=w1 labels=worker:w1 repos=other")
        code, said = self.dispatch()
        self.assertEqual(code, 0, said)
        self.assertIn("not one of this worker's repositories", said)

    def test_blocked_override_prints_exclusion(self):
        before = self.ledger.file("Before", ["repo:new"], dispatched_by=None)
        self.ledger.block(before, self.n)
        code, said = self.dispatch("--allow-blocked", "--note", "explicit override")
        self.assertEqual(code, 0, said)
        self.assertIn("excluded: blocked", said)


class StatusDiscovery(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.ledger = FakeLedger()
        self.path = self.rig.configure()
        with self.path.open("a") as f:
            f.write('\n[repos_from]\ngithub = "acme"\nexcept = ["secret"]\n')
        self.status = self.ledger.file("Status", ["worker-status:w1"], dispatched_by=None)

    def test_worker_discovers_status_and_publishes_admission(self):
        w = self.rig.worker(self.ledger)
        with contextlib.redirect_stdout(io.StringIO()):
            w.announce()
        [note] = self.ledger.human_comments(self.status)
        parsed = gonk.parse_worker_status(note["text"])
        self.assertEqual(parsed["repos_from"], ["acme"])
        self.assertEqual(parsed["except"], ["secret"])
        self.assertEqual(w.usage.status_item, self.status)

    def test_existing_status_number_gets_discovery_label(self):
        self.ledger.items[self.status]["labels"] = []
        with self.path.open("a") as f:
            f.write(f"\n[usage]\nstatus_item = {self.status}\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.rig.worker(self.ledger).announce()
        self.assertIn("worker-status:w1", self.ledger.items[self.status]["labels"])

    def test_restricted_worker_grant_still_publishes_status_if_label_is_refused(self):
        self.ledger.items[self.status]["labels"] = []
        with self.path.open("a") as f:
            f.write(f"\n[usage]\nstatus_item = {self.status}\n")
        original = self.ledger.run

        def restricted(command, stdin=None):
            if command.startswith("sink ") and ":label " in command:
                return gonk.Answer(1, "", "worker grant refuses label")
            return original(command, stdin)

        with patch.object(self.ledger, "run", side_effect=restricted):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.rig.worker(self.ledger).announce()
        self.assertIn("ask the dispatcher to add worker-status:w1", out.getvalue())
        self.assertEqual(len(self.ledger.human_comments(self.status)), 1)

    def test_org_only_config_is_valid_and_invalid_org_is_rejected(self):
        text = self.path.read_text()
        start = text.index("[repos.lib]")
        end = text.index("[repos_from]")
        self.path.write_text(text[:start] + text[end:])
        wc = worker.load_worker(gonk.load(self.rig.checkout))
        self.assertEqual(wc.repos, {})
        self.assertEqual(wc.repository("new")[0].github, "acme/new")
        for invalid in ['"outside/acme"', '""', "123", '["acme"]']:
            self.path.write_text(
                (text[:start] + text[end:]).replace('github = "acme"', f"github = {invalid}")
            )
            with self.assertRaises(gonk.ConfigError):
                worker.load_worker(gonk.load(self.rig.checkout))


class Setup(unittest.TestCase):
    def test_setup_alias_writes_org_admission(self):
        rig = Rig(self)
        with contextlib.redirect_stdout(io.StringIO()):
            code = worker.main(
                ["--repo", str(rig.checkout), "setup", "--name", "w1", "--agents", "codex"]
            )
        self.assertEqual(code, 0)
        path = rig.checkout / ".gonk/worker-codex.toml"
        text = path.read_text()
        line = next(line for line in text.splitlines() if line.startswith("routers = "))
        text = text.replace(line, f"routers = {json.dumps([DISPATCHER])}")
        path.write_text(text)
        config = worker.load_worker(gonk.load(rig.checkout), path)
        self.assertEqual(config.repos_from, "ikigai-rs")
        self.assertEqual(config.repos, {})
