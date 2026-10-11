"""`seed` and the saved scripts: what tools/gonk.py reads from openspec/ and .gonk/, and what
it sends gonk. No gonk, no `ikigai`: a fake runner, over the generated sample's own files."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import unittest
from pathlib import Path

from support import ROOT, FakeIkigai, gonk, ok, scratch_repo

SAMPLE = ROOT / "templates" / "library"


def run(repo: Path, argv: list[str], fake=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gonk.main(["--repo", str(repo), *argv], make_ikigai=fake or FakeIkigai())
    return code, out.getvalue(), err.getvalue()


def with_sample(test) -> Path:
    """A scratch repository holding the sample's openspec/ and .gonk/ trees."""
    repo = scratch_repo(test)
    for rel in ["openspec", ".gonk/queries", ".gonk/scripts"]:
        shutil.copytree(SAMPLE / rel, repo / rel, dirs_exist_ok=True)
    for name in ["service", "cli"]:
        (repo / "artifacts" / name).mkdir()
    return repo


def wire(repo: Path) -> None:
    (repo / ".gonk" / "gonk.toml").write_text('ledger = "library"\n')
    (repo / ".gonk" / "local.toml").write_text(
        f'transport = "socket"\nsocket = "{repo / "g.sock"}"\nholder = "me"\n'
    )
    (repo / "g.sock").touch()


class Parsing(unittest.TestCase):
    def test_the_slug_is_the_mappings(self):
        self.assertEqual(gonk.slug("A member's loans are listed"), "a-member-s-loans-are-listed")
        self.assertEqual(
            gonk.slug("  Returning late owes 25 cents a day! "),
            "returning-late-owes-25-cents-a-day",
        )

    def test_the_samples_changes_tasks_and_requirements(self):
        repo = with_sample(self)
        layout = gonk.read_toml(repo / ".gonk" / "layout.toml")
        changes = {c.id: c for c in gonk.open_changes(repo, layout)}
        self.assertEqual(sorted(changes), ["add-holds", "cap-late-fees"])
        holds = changes["add-holds"]
        self.assertEqual(holds.artifacts, ["service", "cli"])
        self.assertEqual(holds.title, "Holds: a place in line for a title that is out")
        self.assertEqual(
            [t.number for t in holds.tasks], ["1.1", "1.2", "1.3", "1.4", "2.1", "2.2"]
        )
        first = holds.tasks[0]
        self.assertEqual(first.iri, "urn:openspec:change:add-holds:task:1.1")
        self.assertEqual(
            first.requirement,
            "urn:openspec:spec:holds:requirement:a-member-may-hold-a-title-with-no-copy-on-the-shelf",
        )
        self.assertIsNone(holds.tasks[3].cites)  # 1.4 names no requirement
        renew = holds.tasks[2]
        self.assertEqual(
            renew.requirement,
            "urn:openspec:spec:renewals:requirement:a-loan-of-a-held-title-is-not-renewed",
        )
        fees = changes["cap-late-fees"]
        self.assertEqual(fees.artifacts, ["service"])
        self.assertEqual(
            fees.tasks[0].requirement,
            "urn:openspec:spec:overdue:requirement:a-late-return-owes-a-fee",
        )

    def test_a_typo_in_a_citation_cites_nothing(self):
        repo = with_sample(self)
        tasks = repo / "openspec" / "changes" / "cap-late-fees" / "tasks.md"
        tasks.write_text("- [ ] 1.1 Cap it (A late return owes a fe)\n- [x] 1.2 Done already\n")
        layout = gonk.read_toml(repo / ".gonk" / "layout.toml")
        fees = {c.id: c for c in gonk.open_changes(repo, layout)}["cap-late-fees"]
        self.assertEqual(
            (fees.tasks[0].cites, fees.tasks[0].requirement), ("A late return owes a fe", None)
        )
        self.assertTrue(fees.tasks[1].done)

    def test_an_archived_change_is_not_in_flight(self):
        repo = with_sample(self)
        archive = repo / "openspec" / "changes" / "archive"
        archive.mkdir()
        (repo / "openspec" / "changes" / "cap-late-fees").rename(
            archive / "2026-10-09-cap-late-fees"
        )
        layout = gonk.read_toml(repo / ".gonk" / "layout.toml")
        self.assertEqual([c.id for c in gonk.open_changes(repo, layout)], ["add-holds"])


class FakeLedger(FakeIkigai):
    """Just enough of a ledger for seed: keyed appends that answer `existing` for a key filed
    before, `blocks` links, and an item's JSON face carrying them."""

    def __init__(self):
        super().__init__()
        self.keys: dict[str, int] = {}
        self.links: set[tuple[int, int]] = set()

    def iri(self, number: int) -> str:
        return f"urn:iki:ledger:library:item:0{number}"

    def run(self, command, stdin=None):
        self.calls.append((command, stdin))
        if ":append " in command:
            key = re.search(r"key=(\S+)", command).group(1)
            if key in self.keys:
                n = self.keys[key]
                return ok(f"library#{n} {self.iri(n)} existing open\n")
            n = self.keys[key] = len(self.keys) + 1
            return ok(f"library#{n} {self.iri(n)}\n")
        if ":link " in command:
            subject = int(re.search(r"item=urn:iki:ledger:library:item:0(\d+)", command).group(1))
            target = int(stdin.rsplit(":0", 1)[1])
            self.links.add((subject, target))
            return ok(f"linked library#{subject} blocks library#{target}\n")
        m = re.search(r":item:(\d+) as=application/json", command)
        if m:
            n = int(m.group(1))
            links = [
                {"type": "blocks", "target": {"number": t, "iri": self.iri(t)}}
                for s, t in sorted(self.links)
                if s == n
            ]
            return ok(json.dumps({"item": {"number": n, "links": links}}))
        return gonk.Answer(1, "", f"no fake answer for {command!r}")


def task_order(fake: FakeLedger) -> dict[str, int]:
    return {key.rsplit(":", 1)[1] + "@" + key.split(":")[3]: n for key, n in fake.keys.items()}


class Seed(unittest.TestCase):
    def test_a_dry_run_asks_gonk_nothing(self):
        repo = with_sample(self)
        fake = FakeIkigai()
        code, out, _ = run(repo, ["seed", "--dry-run"], fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.calls, [])
        self.assertIn("8 open task(s) would be filed, in 6 order link(s)", out)
        self.assertIn("would file  add-holds 2.1: Add `library.py hold MEMBER ISBN`", out)
        self.assertIn("would link  add-holds 1.4 blocks 2.1", out)
        self.assertIn("would link  cap-late-fees 1.1 blocks 1.2", out)

    def test_each_task_blocks_the_next_one_of_its_change(self):
        """OpenSpec task order is a dependency: 1.1 blocks 1.2 blocks 1.3, within a change
        and never across two, so `next` offers each change's tasks in the order written."""
        repo = with_sample(self)
        wire(repo)
        fake = FakeLedger()
        code, out, err = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out + err)
        n = task_order(fake)
        holds = [n[f"{t}@add-holds"] for t in ["1.1", "1.2", "1.3", "1.4", "2.1", "2.2"]]
        fees = [n[f"{t}@cap-late-fees"] for t in ["1.1", "1.2"]]
        self.assertEqual(fake.links, set(zip(holds, holds[1:], strict=False)) | {tuple(fees)})
        link = next(c for c in fake.calls if ":link " in c[0])
        self.assertEqual(
            link,
            (
                "sink urn:gk:iki:ledger:library:link item=urn:iki:ledger:library:item:01 "
                "type=blocks",
                "urn:iki:ledger:library:item:02",
            ),
        )
        self.assertIn("ordered     library#1 blocks library#2 (add-holds 1.1 before 1.2)", out)
        self.assertIn("8 filed, 0 already filed, 0 checked off (not filed), 6 order link(s)", out)

    def test_a_rerun_links_nothing_and_writes_nothing(self):
        repo = with_sample(self)
        wire(repo)
        fake = FakeLedger()
        run(repo, ["seed"], fake)
        before = set(fake.links)
        fake.calls.clear()
        code, out, err = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(fake.links, before)
        self.assertEqual([c for c, _ in fake.calls if c.startswith("sink") and ":link" in c], [])
        self.assertIn("0 filed, 8 already filed, 0 checked off (not filed), 0 order link(s)", out)

    def test_a_link_an_earlier_seed_did_not_make_is_made(self):
        repo = with_sample(self)
        wire(repo)
        fake = FakeLedger()
        run(repo, ["seed"], fake)
        fake.links.discard((1, 2))  # as if that seed stopped between filing and linking
        code, out, _ = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out)
        self.assertIn((1, 2), fake.links)
        self.assertIn("1 order link(s) added", out)

    def test_a_checked_off_task_is_skipped_and_the_order_holds_across_it(self):
        repo = with_sample(self)
        wire(repo)
        tasks = repo / "openspec" / "changes" / "add-holds" / "tasks.md"
        tasks.write_text(tasks.read_text().replace("- [ ] 1.2", "- [x] 1.2"))
        fake = FakeLedger()
        code, out, _ = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out)
        n = task_order(fake)
        self.assertNotIn("1.2@add-holds", n)
        self.assertIn((n["1.1@add-holds"], n["1.3@add-holds"]), fake.links)
        self.assertIn("1 checked off (not filed), 5 order link(s)", out)

    def test_a_task_added_later_is_linked_in_after_the_one_before_it(self):
        repo = with_sample(self)
        wire(repo)
        fake = FakeLedger()
        run(repo, ["seed"], fake)
        tasks = repo / "openspec" / "changes" / "cap-late-fees" / "tasks.md"
        tasks.write_text(tasks.read_text() + "- [ ] 1.3 Say so in the README\n")
        code, out, _ = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out)
        n = task_order(fake)
        self.assertIn((n["1.2@cap-late-fees"], n["1.3@cap-late-fees"]), fake.links)
        self.assertIn("1 filed, 8 already filed, 0 checked off (not filed), 1 order link(s)", out)

    def test_each_open_task_is_one_keyed_append_about_its_task_and_requirement(self):
        repo = with_sample(self)
        wire(repo)
        fake = FakeLedger()
        code, out, err = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len([c for c, _ in fake.calls if ":append " in c]), 8)
        command, content = fake.calls[0]
        self.assertEqual(
            command,
            "sink urn:gk:iki:ledger:library:append labels=change:add-holds,artifact:service,"
            "artifact:cli key=urn:openspec:change:add-holds:task:1.1 "
            'about="urn:openspec:change:add-holds:task:1.1 '
            'urn:openspec:spec:holds:requirement:a-member-may-hold-a-title-with-no-copy-on-the-shelf"',
        )
        self.assertTrue(
            content.startswith(
                "add-holds 1.1: Accept a hold on a title whose copies are all out\n\n"
                "Task 1.1 of the change `add-holds`"
            ),
            content,
        )
        # A task that cites nothing is about itself alone.
        self.assertTrue(fake.calls[3][0].endswith('about="urn:openspec:change:add-holds:task:1.4"'))
        self.assertIn("seed: 8 filed, 0 already filed, 0 checked off (not filed)", out)

    def test_a_second_seed_files_nothing_and_says_so(self):
        repo = with_sample(self)
        wire(repo)
        fake = FakeLedger()
        run(repo, ["seed"], fake)
        code, out, _ = run(repo, ["seed"], fake)
        self.assertEqual(code, 0, out)
        self.assertIn("existing    library#3", out)
        self.assertIn("seed: 0 filed, 8 already filed", out)

    def test_a_refused_append_fails_the_seed_and_names_the_task(self):
        repo = with_sample(self)
        wire(repo)
        code, out, err = run(
            repo, ["seed"], FakeIkigai([(":append", gonk.Answer(1, "", "denied"))])
        )
        self.assertEqual(code, 1)
        self.assertIn("failed      add-holds 1.1", err)
        self.assertIn("8 FAILED", out)

    def test_seed_needs_a_ledger_and_a_way_to_gonk(self):
        repo = with_sample(self)
        code, _, err = run(repo, ["seed"])
        self.assertEqual(code, 2)
        self.assertIn("no ledger is named", err)


class SavedScripts(unittest.TestCase):
    def test_the_samples_queries_and_script_are_found_with_their_languages(self):
        repo = with_sample(self)
        saved, bad = gonk.saved_scripts(repo, gonk.read_toml(repo / ".gonk" / "layout.toml"))
        self.assertEqual(bad, [])
        self.assertEqual(
            [(s.stem, s.language) for s in saved],
            [
                ("open-items-for-artifact", "sparql"),
                ("open-work-by-change", "sparql"),
                ("requirements-with-open-work", "sparql"),
                ("next-for-artifact", "plan"),
            ],
        )
        self.assertEqual(saved[0].published_name("library"), "library-open-items-for-artifact")
        text = saved[0].text_for("library")
        self.assertIn("<urn:iki:ledger:graph:library>", text)
        self.assertNotIn("LEDGER", text)
        self.assertIn("<urn:iki:ledger:library:next>", saved[3].text_for("library"))

    def test_an_unknown_file_is_named(self):
        repo = with_sample(self)
        (repo / ".gonk" / "queries" / "notes.txt").write_text("x")
        _, bad = gonk.saved_scripts(repo, gonk.read_toml(repo / ".gonk" / "layout.toml"))
        self.assertEqual(len(bad), 1)
        self.assertIn(".gonk/queries/notes.txt: not a script this tool knows", bad[0])

    def init_with(self, scripts_answer):
        repo = with_sample(self)
        wire(repo)
        fake = FakeIkigai(
            [
                ("ledgers", ok("library  8 open  8 total  urn:iki:ledger:graph:library\n")),
                ("describe urn:gk:script:catalog", scripts_answer),
                ("describe urn:gk:nl:sparql", ok('ik:title "contract unavailable"')),
                ("sink urn:gk:script:", ok("published\n")),
            ]
        )
        return run(repo, ["init"], fake), fake

    def test_without_urn_script_they_are_ready_but_unpublished(self):
        (code, out, _), fake = self.init_with(ok('ik:title "contract unavailable"'))
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"absent\s+scripts\s+gonk does not offer `urn:script:catalog`")
        self.assertIn("4 saved here are ready but unpublished", out)
        self.assertFalse([c for c, _ in fake.calls if c.startswith("sink urn:gk:script:")])
        self.assertIn("8 open task(s) in 2 change(s) in flight", out)

    def test_with_urn_script_init_publishes_each_for_this_ledger(self):
        (code, out, _), fake = self.init_with(ok("<urn:ikigai:endpoint:script> a ik:Endpoint ."))
        self.assertEqual(code, 0, out)
        sinks = [(c, s) for c, s in fake.calls if c.startswith("sink urn:gk:script:")]
        self.assertEqual(
            [c for c, _ in sinks],
            [
                "sink urn:gk:script:library-open-items-for-artifact language=sparql",
                "sink urn:gk:script:library-open-work-by-change language=sparql",
                "sink urn:gk:script:library-requirements-with-open-work language=sparql",
                "sink urn:gk:script:library-next-for-artifact language=plan",
            ],
        )
        self.assertTrue(all("LEDGER" not in s for _, s in sinks))
        self.assertIn("published 4: urn:script:library-open-items-for-artifact", out)


if __name__ == "__main__":
    unittest.main()
