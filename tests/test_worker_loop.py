"""The worker loop (tools/worker.py), hermetic: an in-memory ledger, a real git origin in a
scratch directory, the stub gh (tests/fakes/gh.py) and a fake agent (tests/fakes/fake_agent.py).
No gonk, no network, no real coding agent. tests/test_worker_gonk.py runs the same loop against
a scratch gonk where one is installed.
"""

from __future__ import annotations

import contextlib
import io
import json
import plistlib
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from support import ROOT, gonk, scratch_repo
from worker_support import FakeLedger, Rig, worker


def evidence_comments(item: dict) -> list[str]:
    return [c["text"] for c in item["comments"] if c["text"].startswith("Evidence:")]


class Durations(unittest.TestCase):
    def test_the_lease_forms_are_seconds(self):
        for text, want in [("90s", 90), ("30m", 1800), ("2h", 7200), ("1d", 86400)]:
            self.assertEqual(worker.seconds(text, "x"), want, text)
        self.assertEqual(worker.seconds("PT1H30M", "x"), 5400)
        self.assertEqual(worker.seconds("P1DT1S", "x"), 86401)
        for bad in ["", "30", "PT", "P", "1w", "-1m", "30 m"]:
            with self.assertRaises(gonk.ConfigError, msg=bad):
                worker.seconds(bad, "x")


class TheWorkRoot(unittest.TestCase):
    def setUp(self):
        self.home = scratch_repo(self) / "home"
        self.home.mkdir()

    def test_under_the_accounts_own_home_only(self):
        self.assertEqual(worker.work_root("~/work", self.home), self.home.resolve() / "work")
        inside = str(self.home / "a" / "b")
        self.assertEqual(worker.work_root(inside, self.home), Path(inside).resolve())

    def test_the_home_itself_elsewhere_and_another_accounts_home_are_refused(self):
        for bad in ["~", str(self.home), "/tmp/work", str(self.home.parent), "~other/work", "work"]:
            with self.assertRaises(gonk.ConfigError, msg=bad):
                worker.work_root(bad, self.home)

    def test_a_symlink_out_of_the_home_is_refused(self):
        outside = self.home.parent / "outside"
        outside.mkdir()
        (self.home / "work").symlink_to(outside)
        with self.assertRaises(gonk.ConfigError):
            worker.work_root("~/work", self.home)


class Configuration(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.path = self.rig.checkout / ".gonk" / "worker.toml"

    def load(self, text: str):
        self.path.write_text(text)
        return worker.load_worker(gonk.load(self.rig.checkout))

    REPO = '\n[repos.lib]\nremote = "https://github.com/acme/lib.git"\n'

    def test_a_preset_and_a_github_remote(self):
        wc = self.load('labels = ["worker:w1"]\n[agent]\npreset = "codex"\n' + self.REPO)
        self.assertEqual(wc.command, ["codex", "exec", "{prompt}"])
        self.assertEqual(wc.repos["lib"].github, "acme/lib")
        self.assertEqual(wc.repos["lib"].base, "main")
        self.assertEqual(wc.renew_every, 600)  # a third of the default 30-minute lease
        self.assertEqual(wc.budget, 45 * 60)
        self.assertEqual(wc.stop_file, self.rig.checkout / ".gonk" / "state" / "worker" / "stop")

    def test_the_sample_loads_as_shipped(self):
        sample = (ROOT / "worker" / "worker.sample.toml").read_text()
        wc = self.load(sample)
        self.assertEqual(wc.labels, ["worker:my-worker"])
        self.assertEqual(wc.command, ["claude", "-p", "{prompt}"])
        self.assertEqual(wc.repos["library"].github, "acme/library")

    def test_hermes_with_a_profile(self):
        text = 'labels = ["worker:w1"]\n[agent]\npreset = "hermes-profile"\nprofile = "coder"\n'
        wc = self.load(text + self.REPO)
        self.assertEqual(wc.command, ["hermes", "--profile", "{profile}", "-z", "{prompt}"])
        self.assertEqual(wc.profile, "coder")
        with self.assertRaises(gonk.ConfigError):
            self.load('labels = ["worker:w1"]\n[agent]\npreset = "hermes-profile"\n' + self.REPO)

    def test_refusals_name_what_is_wrong(self):
        agent = '[agent]\npreset = "claude"\n'
        cases = {
            "no labels": "labels = []\n" + agent + self.REPO,
            "a command that never passes the item": (
                'labels = ["w"]\n[agent]\ncommand = ["claude", "-p", "hi"]\n' + self.REPO
            ),
            "no repositories": 'labels = ["w"]\n' + agent,
            "a remote with no github slug": (
                'labels = ["w"]\n' + agent + '[repos.lib]\nremote = "/srv/lib.git"\n'
            ),
            "a renewal as long as the lease": (
                'labels = ["w"]\nlease = "30m"\nrenew_every = "30m"\n' + agent + self.REPO
            ),
            "an unknown preset": 'labels = ["w"]\n[agent]\npreset = "nope"\n' + self.REPO,
            "a repository name that is a path": (
                'labels = ["w"]\n' + agent + '[repos.".."]\nremote = "x"\ngithub = "a/b"\n'
            ),
        }
        for why, text in cases.items():
            with self.assertRaises(gonk.ConfigError, msg=why):
                self.load(text)

    def test_no_worker_toml_is_a_configuration_error_with_the_fix(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = worker.main(["--repo", str(self.rig.checkout), "plan"], make_ikigai=FakeLedger())
        self.assertEqual(code, worker.CONFIG)
        self.assertIn("worker.sample.toml", err.getvalue())


class TheLoop(unittest.TestCase):
    """One item at a time, the way `run --once` works it."""

    def setUp(self):
        self.ledger = FakeLedger()
        self.rig = Rig(self)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def run_once(self, **config):
        self.rig.configure(**config)
        return self.rig.worker(self.ledger).run_once()

    def test_an_item_is_claimed_worked_and_handed_back_with_its_pull_request(self):
        n = self.ledger.file("Fix the due dates", ["worker:w1", "repo:lib"], body="They are off.")
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        item = self.ledger.items[n]

        # The evidence is a comment carrying the pull request and its CI, read from the Actions
        # runs for the head's FULL sha because this token may not read checks.
        [comment] = evidence_comments(item)
        self.assertTrue(comment.startswith("Evidence: https://github.com/acme/lib/pull/1\n"))
        self.assertIn("CI green", comment)
        sha = self.rig.gh()["prs"][0]["headRefOid"]
        self.assertEqual(len(sha), 40)
        self.assertIn(f"GitHub Actions runs for {sha} (gh pr checks was refused", comment)
        self.assertIn("never closes and never merges", comment)

        # Released, still open: the dispatcher closes. Nothing was closed or merged.
        self.assertIsNone(item["claim"])
        self.assertEqual(item["status"], "open")
        self.assertEqual(self.ledger.closes, [])
        self.assertFalse(any(":close " in c for c, _ in self.ledger.calls))
        self.assertEqual(self.rig.gh()["forbidden"], [])

        # The agent ran in a fresh worktree under the work root, on a branch of its own that
        # reached the origin, with the fixed preamble ahead of the item; the worktree is gone.
        cwd = Path((self.rig.record / f"cwd-{n}").read_text())
        self.assertEqual(cwd, self.rig.home / "work" / "lib" / f"item-{n}")
        self.assertFalse(cwd.exists())
        self.assertTrue((cwd.parent / f"item-{n}.log").is_file())
        [branch] = [b for b in self.rig.branches() if b.startswith(f"worker/item-{n}-")]
        self.assertIn(branch, comment)
        prompt = self.rig.prompt(n)
        self.assertTrue(prompt.startswith("You are an unattended worker."))
        for rule in [
            "Reproduce before you fix",
            "NEVER merge",
            f"acme#{n}",
            f"on the branch {branch}",
        ]:
            self.assertIn(rule, prompt)
        self.assertTrue(prompt.rstrip().endswith("# Fix the due dates\n\nThey are off."))

    def test_the_lease_is_renewed_while_the_agent_runs(self):
        self.rig.agent_flags = ["--sleep", "3.5"]
        n = self.ledger.file("Slow work", ["worker:w1", "repo:lib"])
        outcome = self.run_once(lease='"5s"', renew_every='"1s"')
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertGreaterEqual(outcome.renewals, 2)
        claims = [
            c for c, holder in self.ledger.calls if ":claim item=" in c and c.startswith("sink")
        ]
        self.assertGreaterEqual(len(claims), 3)  # the claim, then the renewals
        self.assertTrue(all(holder == "w1" for c, holder in self.ledger.calls if c in claims))
        self.assertEqual(len(evidence_comments(self.ledger.items[n])), 1)

    def test_a_claim_lost_mid_run_stops_the_agent_and_releases_nothing(self):
        self.rig.agent_flags = ["--sleep", "60"]
        n = self.ledger.file("Taken over", ["worker:w1", "repo:lib"])
        started = time.monotonic()

        def take_it():
            # Wait until the agent is running, then do what a dispatcher's release and another
            # holder's claim leave behind.
            while not (self.rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            self.ledger.force_claim(n, "someone-else")

        thread = threading.Thread(target=take_it)
        thread.start()
        outcome = self.run_once()
        thread.join()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertLess(time.monotonic() - started, 20, "the agent was not stopped")
        self.assertFalse((self.rig.record / f"finished-{n}").exists())
        item = self.ledger.items[n]
        self.assertEqual(item["claim"]["holder"], "someone-else")  # untouched
        self.assertFalse(any(c.startswith("delete") for c, _ in self.ledger.calls))
        [note] = [c["text"] for c in item["comments"]]
        self.assertIn("lost its claim", note)
        self.assertIn("held by `someone-else`", note)
        self.assertTrue((self.rig.home / "work" / "lib" / f"item-{n}").is_dir())  # kept

    def test_a_refused_renewal_is_a_lost_claim(self):
        self.rig.agent_flags = ["--sleep", "60"]
        n = self.ledger.file("Refused", ["worker:w1", "repo:lib"])

        def refuse():
            while not (self.rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            self.ledger.refuse_renewals = True

        thread = threading.Thread(target=refuse)
        thread.start()
        outcome = self.run_once()
        thread.join()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertIn("the renewal was refused", outcome.summary)

    def test_the_budget_stops_the_agent_and_the_item_goes_back_without_evidence(self):
        self.rig.agent_flags = ["--sleep", "60"]
        n = self.ledger.file("Too big", ["worker:w1", "repo:lib"])
        started = time.monotonic()
        outcome = self.run_once(budget='"2s"')
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertLess(time.monotonic() - started, 20)
        item = self.ledger.items[n]
        self.assertIsNone(item["claim"])
        self.assertEqual(evidence_comments(item), [])
        self.released_with_one_note(n, "its budget of 0m02s ran out", "worktree is kept")

    def test_one_agent_per_repository(self):
        busy = self.ledger.file("Someone is on lib", ["repo:lib"])
        self.ledger.force_claim(busy, "chris")
        lib = self.ledger.file("Also lib", ["worker:w1", "repo:lib"])
        other = self.ledger.file("In other", ["worker:w1", "repo:other"])
        self.rig.configure()
        w = self.rig.worker(self.ledger)
        take, skip, err = w.candidates()
        self.assertIsNone(err)
        self.assertEqual([i["number"] for i, _ in take], [other])
        self.assertIn(
            (
                f"acme#{lib}",
                f"repo:lib is busy: acme#{busy} is claimed by `chris` (one agent per repository)",
            ),
            skip,
        )

        # An expired lease is a holder that stopped renewing: it does not fence the repository.
        self.ledger.force_claim(busy, "chris", expires=datetime.now(UTC) - timedelta(minutes=1))
        take, _, _ = w.candidates()
        self.assertEqual([i["number"] for i, _ in take], [lib, other])

    def test_never_two_items_of_one_repository_on_this_machine(self):
        self.ledger.file("lib", ["worker:w1", "repo:lib"])
        self.rig.configure()
        w = self.rig.worker(self.ledger)
        held = worker.RepoLock(w.wc.state_dir, "lib")
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)
        outcome = w.run_once()
        self.assertEqual(outcome.code, worker.NOTHING)
        self.assertFalse(any(c.startswith("sink") for c, _ in self.ledger.calls))

    def test_what_a_worker_never_takes(self):
        cases = {
            "two repos": ["worker:w1", "repo:lib", "repo:other"],
            "no repo": ["worker:w1"],
            "a repo it does not know": ["worker:w1", "repo:elsewhere"],
        }
        numbers = {why: self.ledger.file(why, labels) for why, labels in cases.items()}
        waiting = self.ledger.file("done, waiting", ["worker:w1", "repo:lib"])
        self.ledger.add_comment(waiting, "Evidence: https://github.com/acme/lib/pull/9")
        self.ledger.file("not for this worker", ["repo:lib"])
        self.rig.configure()
        take, skip, _ = self.rig.worker(self.ledger).candidates()
        self.assertEqual(take, [])
        reasons = dict(skip)
        self.assertIn("exactly one repository", reasons[f"acme#{numbers['two repos']}"])
        self.assertIn("are none", reasons[f"acme#{numbers['no repo']}"])
        self.assertIn(
            "not one of this worker's", reasons[f"acme#{numbers['a repo it does not know']}"]
        )
        self.assertIn("waits for its dispatcher", reasons[f"acme#{waiting}"])
        self.assertEqual(len(reasons), 4)  # the unlabeled item is not offered at all

        # A later comment (the dispatcher saying why the evidence did not hold) frees it.
        self.ledger.add_comment(waiting, "That PR does not fix it: the test still fails.")
        take, _, _ = self.rig.worker(self.ledger).candidates()
        self.assertEqual([i["number"] for i, _ in take], [waiting])

    def test_no_pull_request_and_no_evidence_is_handed_back_once_and_remembered(self):
        self.rig.agent_flags = ["--no-pr", "--exit", "1"]
        n = self.ledger.file("Gives up", ["worker:w1", "repo:lib"])
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.released_with_one_note(n, "without evidence", "exited 1")

        # Not taken again by this worker, until forgotten.
        self.assertEqual(self.rig.worker(self.ledger).run_once().code, worker.NOTHING)
        code = worker.main(
            ["--repo", str(self.rig.checkout), "forget", str(n)],
            make_ikigai=self.ledger,
            home=self.rig.home,
        )
        self.assertEqual(code, 0)
        take, _, _ = self.rig.worker(self.ledger).candidates()
        self.assertEqual([i["number"] for i, _ in take], [n])

    # Every way an item goes back WITHOUT evidence leaves exactly one comment saying why, and
    # a release that carries no content: the cli gives a batch's stdin only to a content-less
    # SINK, never to a DELETE, so a note handed to the release never reached the item (ledger
    # item 1229). The release is a compare-and-set on this worker's holder (`from=`).
    def released_with_one_note(self, n: int, *says: str) -> str:
        item = self.ledger.items[n]
        self.assertIsNone(item["claim"])
        self.assertEqual(item["status"], "open")
        texts = [c["text"] for c in item["comments"]]
        self.assertEqual(len(texts), 1, texts)
        for words in says:
            self.assertIn(words, texts[0])
        deletes = [(c, stdin) for c, stdin in self.ledger.calls if c.startswith("delete")]
        self.assertEqual(deletes, [(f"delete urn:gk:iki:ledger:acme:claim item={n} from=w1", "")])
        return texts[0]

    def test_a_stopped_worker_hands_the_item_back_with_a_note(self):
        self.rig.agent_flags = ["--sleep", "60"]
        n = self.ledger.file("Interrupted", ["worker:w1", "repo:lib"])
        self.rig.configure()
        w = self.rig.worker(self.ledger)

        def stop():
            while not (self.rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            w.shutdown.set()

        thread = threading.Thread(target=stop)
        thread.start()
        outcome = w.run_once()
        thread.join()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.released_with_one_note(n, "the worker was stopped", "without evidence")

    def test_a_failed_step_hands_the_item_back_with_a_note(self):
        n = self.ledger.file("No origin", ["worker:w1", "repo:lib"])
        self.rig.configure()
        self.rig.origin.rename(self.rig.origin.with_name("moved.git"))
        outcome = self.rig.worker(self.ledger).run_once()
        self.assertEqual(outcome.code, worker.ERROR, outcome.summary)
        self.released_with_one_note(n, "failed on acme#", "and hands it back", "git clone")

    def test_evidence_the_ledger_refuses_is_named_in_the_note(self):
        n = self.ledger.file("Refused evidence", ["worker:w1", "repo:lib"])
        self.ledger.refuse_comments = 1  # the evidence; the note after it lands
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        note = self.released_with_one_note(n, "could not post its evidence", "the store refused")
        self.assertIn("https://github.com/acme/lib/pull/1", note)

    def test_a_note_on_a_claim_no_longer_ours_is_posted_and_nothing_released(self):
        n = self.ledger.file("Changed hands", ["worker:w1", "repo:lib"])
        self.rig.configure()
        w = self.rig.worker(self.ledger)
        self.ledger.force_claim(n, "someone-else")
        self.assertFalse(w.release_if_ours(n, "gonk worker `w1` gives up"))
        item = self.ledger.items[n]
        self.assertEqual(item["claim"]["holder"], "someone-else")
        self.assertEqual([c["text"] for c in item["comments"]], ["gonk worker `w1` gives up"])
        self.assertFalse(any(c.startswith("delete") for c, _ in self.ledger.calls))

    def test_the_release_is_a_compare_and_set_on_the_holder(self):
        n = self.ledger.file("Raced", ["worker:w1", "repo:lib"])
        self.rig.configure()
        w = self.rig.worker(self.ledger)
        self.ledger.force_claim(n, "someone-else")
        self.assertFalse(w.ledger.release(n, "w1").ok)  # the race the pre-read cannot close
        self.assertEqual(self.ledger.items[n]["claim"]["holder"], "someone-else")

    def test_not_a_defect_is_reported_with_the_agents_evidence(self):
        url = "https://github.com/acme/lib/actions/runs/7"
        self.rig.agent_flags = ["--no-pr", "--evidence", url]
        n = self.ledger.file("Claimed bug", ["worker:w1", "repo:lib"])
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        [comment] = evidence_comments(self.ledger.items[n])
        self.assertTrue(comment.startswith(f"Evidence: {url}\n"))
        self.assertIn("No pull request", comment)
        self.assertIn("It does not reproduce", comment)

    def test_checks_are_read_directly_when_the_token_may(self):
        state = self.rig.gh()
        state["checks"] = "ok"
        self.rig.gh_state.write_text(json.dumps(state))
        n = self.ledger.file("Classic token", ["worker:w1", "repo:lib"])
        self.assertEqual(self.run_once().code, worker.HANDED_BACK)
        [comment] = evidence_comments(self.ledger.items[n])
        self.assertIn("from gh pr checks: gates: pass", comment)
        self.assertFalse(any(c[:2] == ["run", "list"] for c in self.rig.gh()["calls"]))

    def merged_by(self, at: str) -> str:
        state = self.rig.gh()
        state["merge"] = {"at": at, "by": "dispatcher-login"}
        self.rig.gh_state.write_text(json.dumps(state))
        n = self.ledger.file("Merged", ["worker:w1", "repo:lib"])
        self.assertEqual(self.run_once().code, worker.HANDED_BACK)
        [comment] = evidence_comments(self.ledger.items[n])
        return comment

    def test_a_pull_request_merged_while_the_agent_ran_is_called_out(self):
        comment = self.merged_by("2026-01-01T00:00:00Z")
        self.assertIn(
            "- WARNING: merged at 2026-01-01T00:00:00Z by dispatcher-login before the agent "
            "exited: by the agent, against its rules, unless someone else merged it while it ran",
            comment,
        )
        self.assertNotIn("after the agent exited", comment)

    def test_a_pull_request_merged_after_the_agent_exited_is_not_the_agents(self):
        # Ledger item 1232: a merge by the dispatcher after the agent exited read as the agent's.
        comment = self.merged_by("now")
        self.assertNotIn("WARNING", comment)
        self.assertRegex(
            comment,
            r"- https://github.com/acme/lib/pull/1 was merged by dispatcher-login at \S+, after "
            r"the agent exited",
        )

    def test_keep_leaves_the_worktree(self):
        n = self.ledger.file("Keep it", ["worker:w1", "repo:lib"])
        self.assertEqual(self.run_once(keep="true").code, worker.HANDED_BACK)
        self.assertTrue((self.rig.home / "work" / "lib" / f"item-{n}").is_dir())

    def test_the_brief_carries_the_items_comments_from_the_dispatcher(self):
        n = self.ledger.file("Fix the due dates", ["worker:w1", "repo:lib"], body="They are off.")
        self.ledger.add_comment(
            n, "Actually, the premise was wrong: see the spec at openspec/specs."
        )
        self.ledger.add_comment(n, "Correction: the issue is in the timezone handling.")
        self.ledger.add_comment(n, "Evidence: https://github.com/acme/lib/pull/99")
        self.ledger.add_comment(n, "gonk worker `w1` handed this back: ...")
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        prompt = self.rig.prompt(n)
        self.assertIn("Later notes from the dispatcher and others", prompt)
        self.assertIn("Actually, the premise was wrong", prompt)
        self.assertIn("Correction: the issue is in the timezone handling", prompt)
        # The worker's own hand-back comments are NOT in the brief
        self.assertNotIn("Evidence: https://github.com/acme/lib/pull/99", prompt)
        self.assertNotIn("gonk worker `w1` handed this back", prompt)

    def test_the_brief_bounds_the_comments_it_carries(self):
        n = self.ledger.file("Fix the due dates", ["worker:w1", "repo:lib"], body="They are off.")
        long_text = "x" * 5000
        self.ledger.add_comment(n, long_text)
        outcome = self.run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        prompt = self.rig.prompt(n)
        self.assertIn("Later notes from the dispatcher and others", prompt)
        self.assertIn("[...]", prompt)  # truncated
        self.assertNotIn(long_text, prompt)

    def test_a_second_run_of_one_item_gets_a_fresh_directory(self):
        n = self.ledger.file("Again", ["worker:w1", "repo:lib"])
        self.assertEqual(self.run_once(keep="true").code, worker.HANDED_BACK)
        self.ledger.items[n]["comments"].append(
            {"author": "d", "time": "9999-01-01T00:00:00.000Z", "text": "Not yet: try again."}
        )
        worker.Attempts(self.rig.checkout / ".gonk" / "state" / "worker").forget("acme", n)
        self.assertEqual(self.run_once().code, worker.HANDED_BACK)
        cwd = Path((self.rig.record / f"cwd-{n}").read_text())
        self.assertEqual(cwd.name, f"item-{n}.2")
        self.assertTrue((cwd.parent / f"item-{n}").is_dir())  # the first, untouched


class ShortSha(unittest.TestCase):
    def test_a_short_sha_is_never_asked_about(self):
        """`gh run list --commit` with a short sha answers [] silently; asking would report
        "no runs" for a PR that has them."""
        rig = Rig(self)
        gh = worker.GitHub(rig.gh_argv, "acme/lib")
        pr = worker.PullRequest(1, "https://github.com/acme/lib/pull/1", "OPEN", "abc1234")
        gh.read_ci(pr)
        self.assertEqual(pr.ci, "unknown")
        self.assertIn("is not full", pr.ci_detail)
        self.assertFalse(any(c[:2] == ["run", "list"] for c in rig.gh()["calls"]))


class Units(unittest.TestCase):
    """A unit reads no .profile, so each names its PATH, with the agent's own tools first."""

    UNITS = ROOT / "worker" / "units"

    def test_the_systemd_user_unit(self):
        text = (self.UNITS / "gonk-worker.service").read_text()
        [path] = [line for line in text.splitlines() if line.startswith("Environment=PATH=")]
        self.assertTrue(path.startswith("Environment=PATH=%h/.cargo/bin:"), path)
        self.assertIn("tools/worker.py run", text)
        self.assertIn("WantedBy=default.target", text)

    def test_the_launchd_daemon(self):
        with (self.UNITS / "dev.ikigai-rs.gonk-worker.plist").open("rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["UserName"], "agent")
        self.assertTrue(
            plist["EnvironmentVariables"]["PATH"].startswith("/Users/agent/.cargo/bin:")
        )
        self.assertIn("run", plist["ProgramArguments"])
        self.assertTrue(plist["ProgramArguments"][-2].endswith("tools/worker.py"))
        self.assertTrue(plist["StandardOutPath"])


if __name__ == "__main__":
    unittest.main()
