"""The worker's review mode and `gonk.py findings` (tools/worker.py, tools/gonk.py,
tools/review_findings.py, worker/README.md "Review jobs"), hermetic: the in-memory ledger, a real
git origin, the stub gh and a fake reviewer. Its `append` and `link` are modelled on ikigai-ledger
0.18.1: `about` takes absolute IRIs, `parent=` is ignored, and a held key files nothing."""

from __future__ import annotations

import json
import re
import shlex
import unittest
from pathlib import Path

from support import gonk, scratch_repo
from test_gonk import run as run_gonk
from test_gonk import wire
from worker_support import FakeLedger, Rig, git, worker

rf = gonk.review_findings

WORKER = "urn:iki:host:gonk:client:" + "a" * 64
OTHER_WORKER = "urn:iki:host:gonk:client:" + "c" * 64
STRANGER = "urn:iki:host:gonk:client:" + "b" * 64

MAJOR = {
    "severity": "major",
    "title": "The lock is dropped too early",
    "problem": "It is released before the write.",
    "fix": "Hold it across the write.",
    "path": "src/my lib.py",
    "line": 12,
}
MINOR = {
    "severity": "minor",
    "title": "A stale comment",
    "problem": "It names the old function.",
    "fix": "Name the new one.",
    "path": "README.md",
}


def report(*findings: dict) -> str:
    return json.dumps({"findings": list(findings)})


class ReviewRig:
    """A rig with a second commit on a branch, and a review job for the range up to it."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.test = test
        self.rig = Rig(test)
        self.ledger = FakeLedger()
        self.ledger.handbacks_by = WORKER  # a worker over QUIC: the door names its client
        seed = self.rig.tmp / "seed"
        self.base = git("rev-parse", "HEAD", cwd=seed).strip()
        (seed / ".gitignore").write_text("*.tmp\n")
        (seed / "feature.py").write_text("print('new')\n")
        git("add", ".gitignore", "feature.py", cwd=seed)
        git("commit", "-q", "-m", "A feature", cwd=seed)
        git("push", "-q", str(self.rig.origin), "HEAD:refs/heads/feature", cwd=seed)
        self.head = git("rev-parse", "HEAD", cwd=seed).strip()
        self.dispatcher = None

    def job(self, **overrides) -> int:
        settings = {
            "body": f"Review the range {self.base}..{self.head}, please.",
            "kind": worker.REVIEW_JOB,
            "revision": self.head,
        }
        return self.ledger.file("Review it", ["worker:w1", "repo:lib"], **{**settings, **overrides})

    def run(self, *flags: str, **configure):
        self.rig.agent_flags = ["--no-pr", *flags]
        self.rig.configure(**configure)
        return self.rig.worker(self.ledger).run_once()

    def findings(self) -> list[dict]:
        return [i for i in self.ledger.items.values() if i["kind"] == rf.FINDING]

    def writes(self) -> list[str]:
        """The worker's calls that could file or link an item."""
        return [c for c, _ in self.ledger.calls if ":append" in c or ":link" in c]

    def findings_command(self, number: int, *flags: str) -> tuple[int, str, str]:
        """`gonk.py findings` from a dispatcher's checkout, on the same ledger."""
        if not self.dispatcher:
            self.dispatcher = scratch_repo(self.test)
            wire(self.dispatcher)
            self.trust(WORKER)
        return run_gonk(self.dispatcher, ["findings", str(number), *flags], self.ledger)

    def trust(self, *workers: str) -> None:
        """The dispatcher's own list of the workers it trusts."""
        if not self.dispatcher:
            self.dispatcher = scratch_repo(self.test)
            wire(self.dispatcher)
        (self.dispatcher / ".gonk" / "dispatcher.toml").write_text(
            f"workers = {json.dumps(list(workers))}\n"
        )

    def closed_with(self) -> list[str]:
        command = next(c for c, _ in self.ledger.calls if ":close " in c)
        return [w[len("evidence=") :] for w in shlex.split(command) if w.startswith("evidence=")]

    def report_file(self, number: int) -> Path:
        return self.rig.home / "work" / "lib" / f"item-{number}.report"

    def said(self, number: int) -> str:
        return "\n".join(c["text"] for c in self.ledger.human_comments(number))

    def worktrees(self) -> list[Path]:
        home = self.rig.home / "work" / "lib"
        return [p for p in home.iterdir() if p.is_dir() and p.name.startswith("item-")]


class ARun(unittest.TestCase):
    def setUp(self):
        self.r = ReviewRig(self)

    def test_it_runs_under_the_review_preamble_at_the_head(self):
        n = self.r.job()
        outcome = self.r.run("--report", report())
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        prompt = self.r.rig.prompt(n)
        self.assertIn(f"Review the range {self.r.base}..{self.r.head}", prompt)
        self.assertIn(f"git diff {self.r.base}..{self.r.head}", prompt)
        self.assertIn("Change nothing, commit nothing, push nothing", prompt)
        self.assertIn("file no items", prompt)
        self.assertIn(f"item-{n}.report", prompt)
        self.assertNotIn("gh pr create", prompt)
        self.assertEqual(re.findall(r"\{[a-z_]+\}", prompt), [], "every token is filled in")
        self.assertEqual((self.r.rig.record / f"head-{n}").read_text(), self.r.head)

    def test_nothing_found_is_handed_back_saying_so_with_no_pull_request_looked_for(self):
        n = self.r.job()
        outcome = self.r.run("--report", report())
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        item = self.r.ledger.items[n]
        self.assertEqual(self.r.findings(), [])
        self.assertEqual(item["awaiting"], "dispatcher")
        self.assertIsNone(item["claim"])
        self.assertEqual(self.r.ledger.closes, [])
        self.assertTrue(
            self.r.said(n).startswith(
                f"Evidence: the review of {self.r.base}..{self.r.head} found nothing."
            )
        )
        self.assertNotIn("calls", self.r.rig.gh())  # the stub gh was never run
        self.assertFalse(any(p.name == f"item-{n}" for p in self.r.worktrees()))

    def test_a_file_gitignore_covers_is_no_change(self):
        self.r.job()
        outcome = self.r.run("--report", report(), "--ignored")
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)

    def test_a_branch_names_the_remotes_commit(self):
        n = self.r.job(body="Review main..feature.", revision="feature")
        outcome = self.r.run("--report", report())
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertIn(f"git diff {self.r.base}..{self.r.head}", self.r.rig.prompt(n))
        self.assertEqual((self.r.rig.record / f"head-{n}").read_text(), self.r.head)

    def test_a_short_sha_for_either_end_means_the_same_commit(self):
        n = self.r.job(body=f"Range `{self.r.base[:10]}..{self.r.head[:10]}`.")
        outcome = self.r.run("--report", report())
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertIn(f"git diff {self.r.base}..{self.r.head}", self.r.rig.prompt(n))


class TheHandBack(unittest.TestCase):
    def setUp(self):
        self.r = ReviewRig(self)

    def test_it_carries_the_findings_block_and_the_worker_makes_no_ledger_write(self):
        n = self.r.job()
        outcome = self.r.run("--report", report(MAJOR, MINOR))
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertEqual(self.r.findings(), [])
        self.assertEqual(self.r.writes(), [])
        job = self.r.ledger.items[n]
        self.assertEqual(job["awaiting"], "dispatcher")
        self.assertIsNone(job["claim"])
        self.assertEqual(self.r.ledger.closes, [])
        said = self.r.said(n)
        range_ = f"{self.r.base}..{self.r.head}"
        self.assertTrue(said.startswith(f"Evidence: the review of {range_} found 2 findings.\n"))
        self.assertEqual(said.count("```json"), 1)
        review, problems = rf.parse_hand_back(said)
        self.assertEqual(problems, [])
        self.assertEqual((review.base, review.head), (self.r.base, self.r.head))
        self.assertEqual([f.title for f in review.findings], [MAJOR["title"], MINOR["title"]])

    def test_the_hand_back_goes_out_as_a_hand_back(self):
        self.r.job()
        self.r.run("--report", report(MINOR))
        sent = [c for c, _ in self.r.ledger.calls if ":comment" in c and "handback=true" in c]
        self.assertEqual(len(sent), 1)

    def test_nothing_found_carries_an_empty_block(self):
        n = self.r.job()
        self.r.run("--report", report())
        review, problems = rf.parse_hand_back(self.r.said(n))
        self.assertEqual(problems, [])
        self.assertEqual((review.head, review.findings), (self.r.head, []))
        self.assertEqual(self.r.writes(), [])

    def test_awkward_text_survives_the_block(self):
        tricky = {**MINOR, "problem": 'a\n```\n```json\n"quoted" \u2028 \u00e9', "title": "a ``` b"}
        n = self.r.job()
        self.assertEqual(self.r.run("--report", report(tricky)).code, worker.HANDED_BACK)
        review, problems = rf.parse_hand_back(self.r.said(n))
        self.assertEqual(problems, [])
        self.assertEqual(review.findings[0].problem, tricky["problem"])

    def test_too_large_for_one_comment_says_so_and_keeps_the_report(self):
        n = self.r.job()
        big = {**MINOR, "problem": "x" * rf.MAX_HAND_BACK_BYTES}
        outcome = self.r.run("--report", report(big))
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        item = self.r.ledger.items[n]
        self.assertIsNone(item["awaiting"])
        self.assertIsNone(item["claim"])
        said = self.r.said(n)
        self.assertIn("truncates nothing", said)
        self.assertNotIn("Evidence:", said)
        self.assertNotIn("xxxx", said)
        self.assertEqual(self.r.writes(), [])
        kept = self.r.report_file(n)
        self.assertIn(str(kept), said)
        self.assertEqual(rf.parse_report(kept.read_text())[0][0].problem, big["problem"])
        self.assertTrue(any(p.name == f"item-{n}" for p in self.r.worktrees()))

    def test_a_refused_comment_keeps_the_report_too(self):
        n = self.r.job()
        self.r.ledger.refuse_comments = 1
        outcome = self.r.run("--report", report(MINOR))
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertIsNone(self.r.ledger.items[n]["awaiting"])
        self.assertTrue(self.r.report_file(n).exists())


class TheDispatcherFiles(unittest.TestCase):
    def setUp(self):
        self.r = ReviewRig(self)

    def handed_back(self, *findings: dict) -> int:
        n = self.r.job()
        outcome = self.r.run("--report", report(*findings))
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        return n

    def test_one_item_per_finding_with_the_fields(self):
        n = self.handed_back(MAJOR, MINOR)
        code, out, err = self.r.findings_command(n)
        self.assertEqual(code, 0, err)
        major, minor = self.r.findings()
        self.assertEqual(major["kind"], "https://ikigai-rs.dev/ns/ledger#Finding")
        self.assertEqual(major["revision"], self.r.head)
        self.assertEqual(major["about"], ["urn:repo:file:lib/src/my%20lib.py"])
        self.assertEqual(major["labels"], ["repo:lib", "severity:major"])
        self.assertEqual(major["title"], MAJOR["title"])
        for text in (MAJOR["problem"], MAJOR["fix"], "src/my lib.py:12"):
            self.assertIn(text, major["body"])
        job = self.r.ledger.items[n]
        for found in (major, minor):
            self.assertEqual(
                found["links"],
                [{"type": "parent", "target": {"number": n, "iri": job["iri"]}}],
            )
            self.assertRegex(found["key"], rf"^review:{n}:[0-9a-f]{{16}}$")
            self.assertIn(f"{found['number']} {found['iri']} (filed)", out)
        self.assertEqual(minor["labels"], ["repo:lib", "severity:minor"])
        self.assertIn("README.md", minor["body"])
        self.assertEqual(job["status"], "open", "closing takes --close")

    def test_the_commands_quote_what_the_engine_would_read_as_a_reference(self):
        n = self.handed_back(MINOR)
        self.r.findings_command(n)
        sent = [c for c, _ in self.r.ledger.calls if ":append " in c]
        self.assertEqual(len(sent), 1)
        self.assertIn(f'kind="{rf.FINDING}"', sent[0])
        self.assertIn('about="urn:repo:file:lib/README.md"', sent[0])
        self.assertIn(f'revision="{self.r.head}"', sent[0])
        self.assertEqual(len(shlex.split(sent[0])), 8)

    def test_a_rerun_files_nothing_new(self):
        n = self.handed_back(MAJOR, MINOR)
        self.r.findings_command(n)
        filed = {i["number"] for i in self.r.findings()}
        code, out, err = self.r.findings_command(n)
        self.assertEqual(code, 0, err)
        self.assertEqual({i["number"] for i in self.r.findings()}, filed)
        for found in self.r.findings():
            self.assertEqual(len(found["links"]), 1)
            self.assertIn(f"{found['number']} {found['iri']} (existing)", out)

    def test_a_rerun_links_a_finding_a_stopped_run_left_without_its_parent(self):
        n = self.handed_back(MINOR)
        self.r.ledger.refuse_links = True
        code, _, err = self.r.findings_command(n, "--close")
        self.assertEqual(code, 1)
        self.assertIn("cannot link", err)
        self.assertEqual(self.r.findings()[0]["links"], [])
        self.assertEqual(self.r.ledger.closes, [])

        self.r.ledger.refuse_links = False
        code, _, err = self.r.findings_command(n)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.r.findings()), 1)
        self.assertEqual(self.r.findings()[0]["links"][0]["target"]["number"], n)

    def test_the_same_finding_twice_is_one_item(self):
        n = self.handed_back(MINOR, MINOR)
        code, out, _ = self.r.findings_command(n, "--close")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.r.findings()), 1)
        self.assertEqual(out.count("(filed)"), 1)
        evidence = self.r.closed_with()
        self.assertEqual(evidence, [self.r.findings()[0]["iri"]])

    def test_a_refusal_part_way_says_what_was_filed_and_does_not_close(self):
        n = self.handed_back(MAJOR, MINOR)
        self.r.ledger.refuse_appends = True
        code, _, err = self.r.findings_command(n, "--close")
        self.assertEqual(code, 1)
        self.assertIn("stopped filing: denied", err)
        self.assertEqual(self.r.ledger.closes, [])

    def test_a_refusal_after_one_is_filed_names_it(self):
        n = self.handed_back(MAJOR, MINOR)
        ledger = self.r.ledger
        real = ledger.append

        def once(stdin, args):
            answer = real(stdin, args)
            ledger.refuse_appends = True
            return answer

        ledger.append = once
        code, out, _ = self.r.findings_command(n)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.r.findings()), 1)
        self.assertIn(self.r.findings()[0]["iri"], out)

    def test_close_closes_with_the_finding_iris(self):
        n = self.handed_back(MAJOR, MINOR)
        code, _, err = self.r.findings_command(n, "--close")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.r.ledger.closes, [n])
        iris = [f["iri"] for f in self.r.findings()]
        self.assertEqual(self.r.closed_with(), iris)
        note = next(i for c, i in self.r.ledger.calls if ":close " in c)
        self.assertEqual(note, "\n".join(f"Evidence: {iri}" for iri in iris))

    def test_close_with_nothing_found_names_no_evidence_and_says_so(self):
        n = self.handed_back()
        code, out, err = self.r.findings_command(n, "--close")
        self.assertEqual(code, 0, err)
        self.assertIn("found nothing", out)
        self.assertEqual(self.r.findings(), [])
        self.assertEqual(self.r.ledger.closes, [n])
        self.assertEqual(self.r.closed_with(), [])
        note = next(i for c, i in self.r.ledger.calls if ":close " in c)
        self.assertIn("found nothing", note)

    def test_a_rerun_after_the_close_files_nothing_and_closes_nothing(self):
        n = self.handed_back(MINOR)
        self.r.findings_command(n, "--close")
        # The ledger records a close's note as a comment, which also starts `Evidence:`.
        self.r.ledger.add_comment(n, f"Evidence: {self.r.findings()[0]['iri']}", author="hub")
        self.r.ledger.items[n]["awaiting"] = None
        code, out, err = self.r.findings_command(n, "--close")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.r.findings()), 1)
        self.assertEqual(self.r.ledger.closes, [n])
        self.assertIn("already closed", out)

    def test_it_says_whose_hand_back_it_read(self):
        n = self.handed_back(MINOR)
        author = self.r.ledger.human_comments(n)[-1]["author"]
        _, out, _ = self.r.findings_command(n)
        self.assertIn(f"the hand-back by {author}", out)


class OnlyAWorkersHandBackIsFiled(unittest.TestCase):
    """Anyone who may comment can post a hand-back with a block, and `findings` files under the
    dispatcher's authority, so the comment's author decides (ledger item 3070)."""

    def setUp(self):
        self.r = ReviewRig(self)
        self.n = self.r.job()
        self.r.ledger.items[self.n]["awaiting"] = "dispatcher"

    def block(self, *findings: dict) -> str:
        doc = {"base": self.r.base, "revision": self.r.head, "findings": list(findings)}
        return f"Evidence: a review.\n\n```json\n{json.dumps(doc)}\n```\n"

    def post(self, author: str, status: str | None, *findings: dict) -> None:
        self.r.ledger.add_comment(self.n, self.block(*findings), author=author, status=status)

    def filed(self) -> list[str]:
        return [item["title"] for item in self.r.findings()]

    def refused(self, *says: str, flags=("--close",)) -> str:
        before = len(self.r.ledger.calls)
        code, _, err = self.r.findings_command(self.n, *flags)
        self.assertEqual(code, 2, err)
        self.assertIn("filed nothing", err)
        for text in says:
            self.assertIn(text, err)
        self.assertEqual(self.filed(), [])
        self.assertEqual(self.r.ledger.closes, [])
        calls = self.r.ledger.calls[before:]
        self.assertEqual([c for c, _ in calls if not c.startswith("source ")], [])
        return err

    def test_a_trusted_workers_block_is_filed(self):
        self.post(WORKER, "verified", MAJOR)
        code, out, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])
        self.assertIn(f"the hand-back by {WORKER}", out)

    def test_the_older_spelling_of_a_client_is_the_same_client(self):
        self.post("urn:iki:gonk:client:" + "a" * 64, "verified", MAJOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])

    def test_another_identity_files_nothing_and_gets_the_line_to_add(self):
        self.post(STRANGER, "verified", MAJOR)
        err = self.refused(STRANGER, "not trust", ".gonk/dispatcher.toml")
        self.assertIn("workers = " + json.dumps([WORKER, STRANGER]), err)

    def test_with_no_list_at_all_the_line_is_the_whole_file(self):
        self.r.trust(WORKER)
        (self.r.dispatcher / ".gonk" / "dispatcher.toml").unlink()
        self.post(WORKER, "verified", MAJOR)
        err = self.refused(WORKER, "not trust")
        self.assertIn("workers = " + json.dumps([WORKER]), err)

    def test_the_line_it_prints_works(self):
        self.post(STRANGER, "verified", MINOR)
        err = self.refused(STRANGER)
        line = re.search(r"^.*?(workers = \[.*?\])", err, re.S).group(1)
        (self.r.dispatcher / ".gonk" / "dispatcher.toml").write_text(line + "\n")
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MINOR["title"]])

    def test_an_unverified_author_is_never_trusted_even_when_listed(self):
        # Over the socket `author=` is free text, and it may spell a listed client's IRI.
        for status in ("claimed", "claimed by a delegate", None):
            with self.subTest(status=status):
                self.post(WORKER, status, MAJOR)
                err = self.refused(WORKER, "did not verify")
                self.assertNotIn("workers = ", err)

    def test_a_free_text_author_is_named_on_one_line(self):
        self.post("mallory\nfiled: all clear", "claimed", MAJOR)
        err = self.refused("did not verify", "mallory")
        self.assertNotIn("\nfiled: all clear", err)
        self.assertNotIn("workers = ", err)

    def test_an_authorless_comment_says_so(self):
        self.r.ledger.items[self.n]["comments"].append(
            {"time": "2026-11-04T00:00:00Z", "text": self.block(MAJOR)}
        )
        self.refused("did not record")

    def test_a_block_it_does_not_trust_is_not_even_validated(self):
        self.r.ledger.add_comment(
            self.n, "Evidence: nothing here", author=STRANGER, status="verified"
        )
        err = self.refused(STRANGER, "not trust")
        self.assertNotIn("malformed", err)

    def test_a_later_trusted_hand_back_is_filed(self):
        self.post(STRANGER, "verified", MINOR)
        self.post(WORKER, "verified", MAJOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])

    def test_a_later_untrusted_hand_back_is_passed_over_and_named(self):
        self.post(WORKER, "verified", MAJOR)
        self.post(STRANGER, "verified", MINOR)
        self.post("mallory", "claimed", MINOR)
        code, _, err = self.r.findings_command(self.n, "--close")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])
        self.assertEqual(self.r.ledger.closes, [self.n])
        self.assertIn(STRANGER, err)
        self.assertIn("mallory", err)

    def test_the_newest_trusted_hand_back_wins_and_an_older_block_is_not_fallen_back_on(self):
        self.r.trust(WORKER, OTHER_WORKER)
        self.post(WORKER, "verified", MAJOR)
        self.r.ledger.add_comment(self.n, "Evidence: none", author=OTHER_WORKER, status="verified")
        self.refused("malformed", "no ```json block")

    def test_a_closed_job_is_read_by_the_same_rule(self):
        self.post(STRANGER, "verified", MAJOR)
        self.r.ledger.items[self.n]["status"] = "closed"
        self.refused(STRANGER, "not trust")

    def test_a_bad_list_files_nothing_and_says_where(self):
        self.post(WORKER, "verified", MAJOR)
        for text, says in (
            ('workers = "x"', "a list"),
            ('workers = ["hub"]', "'hub'"),
            ('workers = ["urn:iki:host:gonk:client:<fingerprint>"]', "<fingerprint>"),
            ("workers = [", "dispatcher.toml"),
        ):
            with self.subTest(text=text):
                self.r.trust(WORKER)
                (self.r.dispatcher / ".gonk" / "dispatcher.toml").write_text(text + "\n")
                code, _, err = self.r.findings_command(self.n)
                self.assertEqual(code, 2, err)
                self.assertIn(says, err)
                self.assertEqual(self.filed(), [])


class ARejectedHandBackIsNotFiled(unittest.TestCase):
    """A reject records the hand-backs it turned down (`rejects`, ikigai-ledger PR 87), and a new
    hand-back makes the job read as awaiting again, whoever posts it (ledger item 3158)."""

    def setUp(self):
        self.r = ReviewRig(self)
        self.n = self.r.job()

    def hand_back(self, author: str, *findings: dict, status: str = "verified") -> str:
        doc = {"base": self.r.base, "revision": self.r.head, "findings": list(findings)}
        text = f"Evidence: a review.\n\n```json\n{json.dumps(doc)}\n```\n"
        self.r.ledger.items[self.n]["awaiting"] = "dispatcher"
        return self.r.ledger.add_comment(self.n, text, author=author, status=status)

    def reject(self, *named: str, author: str = "urn:dispatcher", status="verified") -> str:
        self.r.ledger.items[self.n]["awaiting"] = None
        return self.r.ledger.add_comment(
            self.n, "That is not it.", author=author, status=status, rejects=list(named)
        )

    def filed(self) -> list[str]:
        return [item["title"] for item in self.r.findings()]

    def refused(self, flags=("--close",)) -> str:
        before = len(self.r.ledger.calls)
        code, _, err = self.r.findings_command(self.n, *flags)
        self.assertEqual(code, 2, err)
        self.assertEqual(self.filed(), [])
        self.assertEqual(self.r.ledger.closes, [])
        calls = self.r.ledger.calls[before:]
        self.assertEqual([c for c, _ in calls if not c.startswith("source ")], [])
        return err

    def test_a_rejected_block_followed_by_someone_elses_hand_back_is_not_filed(self):
        rejecter = self.reject(self.hand_back(WORKER, MAJOR))
        self.hand_back(STRANGER, MINOR)
        err = self.refused()
        self.assertIn(rejecter, err)

    def test_a_rejected_block_followed_by_the_workers_new_hand_back_files_the_new_one(self):
        self.reject(self.hand_back(WORKER, MAJOR))
        self.hand_back(WORKER, MINOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MINOR["title"]])

    def test_a_reject_naming_two_hand_backs_skips_both(self):
        self.r.trust(WORKER, OTHER_WORKER)
        rejecter = self.reject(self.hand_back(WORKER, MAJOR), self.hand_back(OTHER_WORKER, MINOR))
        self.hand_back(STRANGER, MINOR)
        self.assertIn(rejecter, self.refused())

    def test_an_ordinary_comment_that_recorded_rejects_blocks_filing_until_a_new_hand_back(self):
        rejecter = self.reject(self.hand_back(WORKER, MAJOR))
        self.r.ledger.items[self.n]["comments"][-1]["text"] = "Looks fine, closing soon."
        self.assertIn(rejecter, self.refused())
        self.hand_back(WORKER, MAJOR)
        code, _, err = self.r.findings_command(self.n, "--close")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])
        self.assertEqual(self.r.ledger.closes, [self.n])

    def test_the_refusal_is_one_plain_sentence(self):
        rejecter = self.reject(self.hand_back(WORKER, MAJOR))
        when = self.r.ledger.items[self.n]["comments"][-1]["time"]
        self.hand_back(STRANGER, MINOR)
        display = self.r.ledger.items[self.n]["display"]
        self.assertEqual(
            self.refused().strip(),
            f"gonk.py: {display}: filed nothing: its latest trusted hand-back was rejected by the "
            f'comment {rejecter}, by "urn:dispatcher" at {when}; have the worker hand back again',
        )

    def test_it_says_the_same_with_no_new_hand_back(self):
        # The plainest case: rejected, and nothing since, so the job is not awaiting either.
        rejecter = self.reject(self.hand_back(WORKER, MAJOR))
        err = self.refused()
        self.assertIn(rejecter, err)
        self.assertIn("have the worker hand back again", err)

    def test_a_reject_counts_whoever_posted_it(self):
        # It can only stop a filing, so a stray one costs a re-hand-back and nothing more.
        for who, status in ((STRANGER, "verified"), ("mallory\nall clear", "claimed")):
            with self.subTest(who=who):
                self.r = ReviewRig(self)
                self.n = self.r.job()
                rejecter = self.reject(self.hand_back(WORKER, MAJOR), author=who, status=status)
                self.hand_back(STRANGER, MINOR)
                err = self.refused()
                self.assertIn(rejecter, err)
                self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_a_reject_of_someone_elses_hand_back_stops_nothing_of_the_workers(self):
        self.reject(self.hand_back(STRANGER, MINOR))
        self.hand_back(WORKER, MAJOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])

    def test_a_job_whose_only_hand_backs_are_rejected_and_untrusted_names_their_author(self):
        self.reject(self.hand_back(STRANGER, MINOR))
        self.hand_back(STRANGER, MINOR)
        self.assertIn("not trust", self.refused())

    def test_an_older_block_is_not_fallen_back_on_when_the_newer_one_was_rejected(self):
        # PR 87's gap: a hand-back nobody held, written before its mark, is one no reject names.
        self.hand_back(WORKER, MINOR)
        rejecter = self.reject(self.hand_back(WORKER, MAJOR))
        self.hand_back(STRANGER, MINOR)
        self.assertIn(rejecter, self.refused())

    def test_a_closed_job_is_read_by_the_same_rule(self):
        rejecter = self.reject(self.hand_back(WORKER, MAJOR))
        self.r.ledger.items[self.n]["status"] = "closed"
        self.assertIn(rejecter, self.refused())

    def test_the_warning_says_nothing_of_upgrading_once_the_ledger_has_shown_rejects(self):
        self.reject(self.hand_back(WORKER, MINOR))
        self.hand_back(WORKER, MAJOR)
        self.hand_back(STRANGER, MINOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.filed(), [MAJOR["title"]])
        self.assertIn("passed over a newer hand-back", err)
        self.assertNotIn("upgraded", err)


class ALedgerWithNoRejects(unittest.TestCase):
    """Older than ikigai-ledger PR 87, no comment has the key: as before, and the warning says so
    (ledger item 3158). Nothing is inferred from a comment's words."""

    def setUp(self):
        self.r = ReviewRig(self)
        self.n = self.r.job()

    def post(self, author: str, *findings: dict) -> None:
        doc = {"base": self.r.base, "revision": self.r.head, "findings": list(findings)}
        text = f"Evidence: a review.\n\n```json\n{json.dumps(doc)}\n```\n"
        self.r.ledger.add_comment(self.n, text, author=author, status="verified")
        self.r.ledger.items[self.n]["awaiting"] = "dispatcher"

    def test_the_newest_trusted_block_is_filed_and_the_warning_says_what_it_cannot_tell(self):
        self.post(WORKER, MAJOR)
        self.r.ledger.add_comment(self.n, "Rejected: that is not it.", author="urn:dispatcher")
        self.post(STRANGER, MINOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual([i["title"] for i in self.r.findings()], [MAJOR["title"]])
        self.assertIn("passed over a newer hand-back", err)
        self.assertIn(STRANGER, err)
        self.assertIn("cannot tell rejected hand-backs apart until it is upgraded", err)

    def test_with_nothing_passed_over_there_is_no_warning(self):
        self.post(WORKER, MAJOR)
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")

    def test_a_job_not_awaiting_is_refused_as_before(self):
        self.post(WORKER, MAJOR)
        self.r.ledger.items[self.n]["awaiting"] = None
        code, _, err = self.r.findings_command(self.n)
        self.assertEqual(code, 2, err)
        self.assertIn("not awaiting its dispatcher", err)


class WhatTheDispatcherRefuses(unittest.TestCase):
    def setUp(self):
        self.r = ReviewRig(self)

    def refused(self, n: int, *says: str, flags=("--close",)) -> None:
        before = len(self.r.ledger.calls)
        code, _, err = self.r.findings_command(n, *flags)
        self.assertEqual(code, 2, err)
        self.assertIn("filed nothing", err)
        for text in says:
            self.assertIn(text, err)
        self.assertEqual(self.r.findings(), [])
        self.assertEqual(self.r.ledger.closes, [])
        verbs = [c for c, _ in self.r.ledger.calls[before:] if not c.startswith("source ")]
        self.assertEqual(verbs, [], "a refusal writes nothing")

    def with_hand_back(self, text: str, **overrides) -> int:
        n = self.r.job(**overrides)
        self.r.ledger.add_comment(n, text, author=WORKER, status="verified")
        self.r.ledger.items[n]["awaiting"] = "dispatcher"
        return n

    def block(self, doc: dict | str) -> str:
        text = doc if isinstance(doc, str) else json.dumps(doc)
        return f"Evidence: the review found 1 finding.\n\n```json\n{text}\n```\n"

    def doc(self, *findings: dict, **fields) -> dict:
        settings = {"base": self.r.base, "revision": self.r.head, "findings": list(findings)}
        return {**settings, **fields}

    def test_a_missing_block(self):
        n = self.with_hand_back("Evidence: https://example.org/report")
        self.refused(n, "no ```json block")

    def test_a_malformed_block(self):
        bad = {**MAJOR, "severity": "blocker"}
        cases = [
            ("not json", "not well-formed JSON"),
            ("[]", 'a list under "findings"'),
            (self.doc(bad), "findings[0]: severity"),
            (self.doc(MINOR, {**MAJOR, "title": ""}), "findings[1]: title"),
            (self.doc({**MAJOR, "confidence": 3}), "confidence"),
            (self.doc({**MAJOR, "path": "a/../../etc"}), "path is outside"),
            (self.doc({**MAJOR, "title": "\ud800"}), "title is not valid Unicode"),
            ("[" * 200000, "not well-formed JSON"),
            (self.doc(MINOR, extra=1), "fields the format does not: extra"),
            (self.doc(MINOR, revision="main"), "revision is not a commit sha"),
            (self.doc(MINOR, base=None), "base is not a commit sha"),
            (self.doc(MINOR, revision="a" * 40), "is not the job's revision"),
        ]
        for text, says in cases:
            with self.subTest(text=text):
                self.r = ReviewRig(self)
                self.refused(self.with_hand_back(self.block(text)), says)

    def test_one_bad_finding_stops_the_good_one_too(self):
        bad = {**MAJOR, "severity": "blocker"}
        self.refused(self.with_hand_back(self.block(self.doc(MINOR, bad))), "findings[1]")

    def test_two_blocks_and_an_unclosed_one(self):
        one = self.block(self.doc(MINOR))
        self.refused(self.with_hand_back(one + one), "2 ```json blocks")
        self.refused(self.with_hand_back(one.replace("\n```\n", "\n")), "never closed")

    def test_a_newer_hand_back_without_a_block_is_not_passed_over(self):
        n = self.with_hand_back(self.block(self.doc(MINOR)))
        self.r.ledger.add_comment(
            n, "Evidence: https://example.org/report", author=WORKER, status="verified"
        )
        self.refused(n, "no ```json block")

    def test_a_job_with_no_hand_back(self):
        self.refused(self.r.job(), "not awaiting its dispatcher")

    def test_a_rejected_hand_back(self):
        n = self.with_hand_back(self.block(self.doc(MINOR)))
        self.r.ledger.items[n]["awaiting"] = None
        self.refused(n, "not awaiting its dispatcher")

    def test_a_change_job(self):
        n = self.with_hand_back(self.block(self.doc(MINOR)), kind=None)
        self.refused(n, "not a review job")

    def test_a_job_without_one_usable_repo(self):
        for labels in (["worker:w1"], ["repo:a", "repo:b"], ['repo:a" close="true']):
            with self.subTest(labels=labels):
                self.r = ReviewRig(self)
                n = self.with_hand_back(self.block(self.doc(MINOR)))
                self.r.ledger.items[n]["labels"] = labels
                code, _, err = self.r.findings_command(n, "--close")
                self.assertEqual(code, 1)
                self.assertIn("repo: label", err)
                self.assertEqual(self.r.findings(), [])
                self.assertEqual(self.r.ledger.closes, [])

    def test_an_unreadable_item(self):
        code, _, err = self.r.findings_command(999)
        self.assertEqual(code, 2)
        self.assertIn("could not read item 999", err)

    def test_a_branch_as_the_revision_is_not_compared(self):
        n = self.with_hand_back(self.block(self.doc(MINOR)), revision="feature")
        code, _, err = self.r.findings_command(n)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.r.findings()), 1)


class AnOlderLedger(unittest.TestCase):
    def test_with_no_awaiting_state_the_prefix_is_the_hand_back(self):
        r = ReviewRig(self)
        r.ledger = FakeLedger(older_comment=True)
        r.ledger.handbacks_by = WORKER
        n = r.job()
        outcome = r.run("--report", report(MINOR))
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        code, _, err = r.findings_command(n)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(r.findings()), 1)

    def test_with_no_attribution_nothing_is_verified_so_nothing_is_filed(self):
        r = ReviewRig(self)
        r.ledger = FakeLedger(older_comment=True)
        n = r.job()
        outcome = r.run("--report", report(MINOR))
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        code, _, err = r.findings_command(n)
        self.assertEqual(code, 2, err)
        self.assertIn("did not verify", err)
        self.assertEqual(r.findings(), [])


class Refusals(unittest.TestCase):
    def setUp(self):
        self.r = ReviewRig(self)

    def refused(self, n: int, outcome, *says: str) -> None:
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        self.assertEqual(self.r.findings(), [])
        item = self.r.ledger.items[n]
        self.assertIsNone(item["claim"])
        self.assertIsNone(item["awaiting"])
        said = self.r.said(n)
        self.assertNotIn("Evidence:", said)
        self.assertIn("files nothing", said)
        for text in says:
            self.assertIn(text, said)
        self.assertEqual(self.r.ledger.closes, [])

    def test_an_untracked_file_files_nothing(self):
        n = self.r.job()
        outcome = self.r.run("--report", report(MAJOR), "--dirty")
        self.refused(n, outcome, "changed the worktree", "LEFT-BEHIND.txt")
        self.assertTrue(self.r.worktrees(), "the worktree is kept")

    def test_a_commit_files_nothing(self):
        n = self.r.job()
        outcome = self.r.run("--report", report(MAJOR), "--commit-only")
        self.refused(n, outcome, "changed the worktree", "HEAD moved")

    def test_a_new_branch_files_nothing_though_head_and_tree_look_untouched(self):
        n = self.r.job()
        self.r.rig.configure()
        wk = self.r.rig.worker(self.r.ledger)
        script = f"git branch sneaked; echo '{report()}' > {{report_file}}"
        wk.wc.command = ["sh", "-c", script, "{prompt}"]
        self.refused(n, wk.run_once(), "a local branch or the stash moved")

    def test_an_invalid_report_files_nothing_and_says_what_was_wrong(self):
        cases = [
            ("not json", "not well-formed JSON"),
            ("[]", 'a list under "findings"'),
            ('{"findings": 1}', 'a list under "findings"'),
            (report({**MAJOR, "severity": "blocker"}), "findings[0]: severity"),
            (report(MINOR, {**MAJOR, "title": ""}), "findings[1]: title"),
            (report({**MAJOR, "title": "one\ntwo"}), "title is not one line"),
            (report({k: v for k, v in MAJOR.items() if k != "fix"}), "fix is not"),
            (report({**MAJOR, "line": "12"}), "line is not"),
            (report({**MAJOR, "line": True}), "line is not"),
            (report({**MAJOR, "line": 0}), "line is not"),
            (report({**MAJOR, "path": "/etc/passwd"}), "path is not a path relative"),
            (report({**MAJOR, "path": "a/../../etc"}), "path is outside"),
            (report({**MAJOR, "path": ".git/config"}), "path is outside"),
            (report({**MAJOR, "confidence": 3}), "confidence"),
        ]
        for text, says in cases:
            with self.subTest(text=text):
                self.r = ReviewRig(self)
                n = self.r.job()
                self.refused(n, self.r.run("--report", text), "report is invalid", says)

    def test_one_bad_finding_stops_the_good_one_too(self):
        n = self.r.job()
        bad = {**MAJOR, "severity": "blocker"}
        self.refused(n, self.r.run("--report", report(MINOR, bad)), "findings[1]")

    def test_no_report_files_nothing(self):
        n = self.r.job()
        self.refused(n, self.r.run(), "wrote no report")

    def test_a_failed_agent_files_nothing_whatever_it_wrote(self):
        n = self.r.job()
        self.refused(n, self.r.run("--report", report(), "--exit", "1"), "the agent failed")

    def test_a_budget_that_ran_out_files_nothing(self):
        n = self.r.job()
        outcome = self.r.run("--report", report(MAJOR), "--sleep", "60", budget='"1s"')
        self.refused(n, outcome, "budget")

    def test_a_job_with_no_revision_or_range_runs_no_agent(self):
        wrong_range = {"body": f"Review {self.r.base}..main."}
        cases = [
            ({"revision": None}, "revision is its head"),
            ({"revision": "--upload-pack=x"}, "revision is its head"),
            ({"revision": "0" * 40}, "is not a commit"),
            ({"body": "Review it, with no range."}, "names no range"),
            (wrong_range, "names no range"),
        ]
        for overrides, says in cases:
            with self.subTest(overrides=overrides):
                self.r = ReviewRig(self)
                n = self.r.job(**overrides)
                outcome = self.r.run("--report", report(MAJOR))
                self.assertEqual(outcome.code, worker.ERROR, outcome.summary)
                self.assertIn(says, self.r.said(n))
                self.assertFalse((self.r.rig.record / f"started-{n}").exists())
                self.assertEqual(self.r.findings(), [])
                self.assertIsNone(self.r.ledger.items[n]["claim"])


class AChangeJob(unittest.TestCase):
    def test_it_is_unaffected(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.agent_flags = ["--report", report(MAJOR)]  # a change job's report is not a review's
        rig.configure()
        n = ledger.file("Fix it", ["worker:w1", "repo:lib"], body="Fix it, please.")
        outcome = rig.worker(ledger).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        prompt = rig.prompt(n)
        self.assertIn("You are an unattended worker.", prompt)
        self.assertIn("gh pr create", prompt)
        self.assertNotIn("reviewer", prompt)
        self.assertEqual(len(ledger.items), 1)
        self.assertTrue(ledger.human_comments(n)[-1]["text"].startswith("Evidence: http"))
        self.assertFalse([c for c, _ in ledger.calls if ":append" in c or ":link" in c])

    def test_another_class_is_a_change_job(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.configure()
        n = ledger.file("Fix it", ["worker:w1", "repo:lib"], kind="https://example.org/Decision")
        outcome = rig.worker(ledger).run_once()
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        self.assertIn("You are an unattended worker.", rig.prompt(n))


class TheReport(unittest.TestCase):
    def test_a_symbolic_link_out_of_the_repository_is_refused(self):
        rig = Rig(self)
        tree = rig.tmp / "tree"
        tree.mkdir()
        (tree / "out").symlink_to(rig.tmp)
        _, problems = rf.parse_report(report({**MINOR, "path": "out/secret"}), tree)
        self.assertIn("symbolic link", problems[0])

    def test_a_path_is_normalized_so_its_key_is_stable(self):
        rig = Rig(self)
        one, _ = rf.parse_report(report({**MINOR, "path": "./a//b.py"}), rig.tmp)
        two, _ = rf.parse_report(report({**MINOR, "path": "a/b.py"}), rig.tmp)
        self.assertEqual(one[0].path, "a/b.py")
        self.assertEqual(rf.finding_key(9, one[0]), rf.finding_key(9, two[0]))

    def test_a_removed_file_may_be_reported(self):
        rig = Rig(self)
        found, problems = rf.parse_report(report({**MINOR, "path": "gone.py"}), rig.tmp)
        self.assertEqual((len(found), problems), (1, []))

    def test_the_key_is_what_the_ledger_accepts(self):
        rig = Rig(self)
        found, _ = rf.parse_report(report(MAJOR), rig.tmp)
        self.assertRegex(rf.finding_key(12, found[0]), r"^[A-Za-z0-9._~:-]{1,256}$")
        self.assertIsNone(
            gonk.parse_dispatch(rf.finding_content(found[0], "a", "b", "lib", "acme#1"))
        )


if __name__ == "__main__":
    unittest.main()
