"""The harness (tools/harness.py): the graph, the firing log, the check that holds the graph to
the code and to AGENTS.md, and every sensor that records firings, fired.

`SensorsFire` drives each sensor through its real code path and asserts the firing landed in the
working tree's log, one test per sensor, named `test_<control id>`. `Proofs` reads this module
and holds the graph's `harness:firedBy` to those tests both ways: a sensor that records firings
names the tests that fire it, and a test names only sensors the graph describes. No gonk: the
questions are asked of a real one by tests/test_integration.py.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, FakeIkigai, gonk, harness, load_hook, ok, scratch_repo
from worker_support import FakeLedger, Rig, worker
from worker_support import git as support_git

spec = importlib.util.spec_from_file_location("new", ROOT / "tools" / "new.py")
new = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new)

H = harness.H
OWL = "http://www.w3.org/2002/07/owl#"
# The authors' private harness vocabulary, read only when a checkout of it sits beside this one.
PRIVATE_VOCAB = ROOT.parent / "ikigai-devtools" / "claude" / "harness" / "vocabulary.ttl"
PROFILES = new.catalog()["profiles"]
CONFLICTS = {("review-every-commit", "audits"), ("audits", "review-every-commit")}


def assert_fired(test: unittest.TestCase, repo: Path, control: str, kind: str = "refusal"):
    """The sensor `control` recorded a firing of `kind` in `repo`'s firing log."""
    segment = harness.read_firings(repo)
    test.assertIsNotNone(segment, f"nothing fired in {repo}; expected {control}")
    fired = [(f.control, f.kind) for f in segment.firings]
    test.assertIn((control, kind), fired)


def fired(repo: Path) -> list[str]:
    segment = harness.read_firings(repo)
    return [f.control for f in segment.firings] if segment else []


def wire(repo: Path, holder: str | None = "agent-1") -> None:
    (repo / ".gonk" / "gonk.toml").write_text('ledger = "acme"\n')
    lines = ['transport = "socket"', f'socket = "{repo / "g.sock"}"']
    if holder:
        lines.append(f'holder = "{holder}"')
    (repo / ".gonk" / "local.toml").write_text("\n".join(lines) + "\n")


def run(main, repo: Path, argv: list[str], fake=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--repo", str(repo), *argv], make_ikigai=fake or FakeIkigai())
    return code, out.getvalue(), err.getvalue()


def generated(test: unittest.TestCase, process: list[str] | None, name: str = "repo") -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="gonk-harness-")).resolve()
    test.addCleanup(shutil.rmtree, tmp, True)
    repo = tmp / name
    new.generate(repo, "python", process)
    return repo


def load_tool(repo: Path, rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, repo / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def every_fragment() -> harness.Graph:
    """Every control any profile can carry: all of profiles/harness/, as one graph."""
    prefixes = "\n".join(f"@prefix {p}: <{ns}> ." for p, ns in new.HARNESS_PREFIXES)
    body = "\n".join(p.read_text() for p in sorted((ROOT / "profiles" / "harness").glob("*.ttl")))
    return harness.Graph(harness.parse_turtle(prefixes + "\n" + body, "profiles/harness")[0])


# --------------------------------------------------------------------------- the Turtle subset


class TurtleSubset(unittest.TestCase):
    def test_statements_lists_and_literals(self):
        text = (
            "@prefix ex: <urn:ex:> .\n"
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
            'ex:a a ex:T ; ex:p "one \\"quoted\\" #not-a-comment" , "two"@en ;\n'
            '    ex:n 3 ; ex:t "x"^^xsd:token ; ex:b true .  # a comment\n'
            "<urn:ex:b> ex:p ex:c:d.e .\n"
        )
        triples, prefixes = harness.parse_turtle(text)
        self.assertEqual(prefixes["ex"], "urn:ex:")
        objects = {o for _, _, o in triples}
        self.assertIn(harness.L('one "quoted" #not-a-comment'), objects)
        self.assertIn(harness.L("two", lang="en"), objects)
        self.assertIn(harness.L("3", harness.XSD + "integer"), objects)
        self.assertIn(harness.L("x", harness.XSD + "token"), objects)
        self.assertIn(harness.I("urn:ex:c:d.e"), objects)
        typed = (harness.I("urn:ex:a"), harness.I(harness.RDF_TYPE), harness.I("urn:ex:T"))
        self.assertIn(typed, triples)

    def test_what_is_outside_the_subset_is_refused_with_its_line(self):
        head = "@prefix ex: <urn:ex:> .\n"
        for body, words in [
            ("ex:a ex:p [ ex:q 1 ] .", "blank node"),
            ("ex:a ex:p _:b .", "blank node"),
            ("ex:a ex:p ( 1 2 ) .", "collection"),
            ('ex:a ex:p """long""" .', "strings are outside"),
            ("ex:a ex:p 1.5 .", "only integers"),
            ("ex:a no:p 1 .", "undeclared prefix"),
            ("ex:a ex:p 1", "ends with `.`"),
        ]:
            with self.assertRaises(harness.TurtleError, msg=body) as refused:
                harness.parse_turtle(head + body, "t.ttl")
            self.assertIn(words, str(refused.exception))
            self.assertIn("t.ttl:2:", str(refused.exception))

    def test_n_triples_escapes_round_trip_through_the_parser(self):
        literal = harness.L('a "b" \\ c\nd')
        line = f"<urn:s> <urn:p> {literal.nt()} ."
        [(_, _, back)] = harness.parse_turtle(line)[0]
        self.assertEqual(back, literal)


# --------------------------------------------------------------------------- the firing log


class TheFiringLog(unittest.TestCase):
    def setUp(self):
        self.repo = scratch_repo(self)
        harness._reported = False
        self.addCleanup(setattr, harness, "_reported", False)

    def test_a_segment_ikigai_log_reads_with_one_line_per_firing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            harness.fire("evidence-required", "refusal", self.repo, command="gonk.py report 1")
            harness.fire("review-not-queued", "warning", self.repo, tool="Edit", session="ab-1/x")
        self.assertEqual(out.getvalue(), "")  # never a byte on standard output
        text = (self.repo / harness.FIRINGS_FILE).read_text()
        head, _, body = text.partition("\n\n")
        lines = head.splitlines()
        self.assertEqual(lines[0], "# ikigai-log v1")
        self.assertEqual(lines[1], "@prefix harness: <https://ikigai-rs.dev/ns/harness#> .")
        self.assertEqual(lines[2], "@prefix log:     <https://ikigai-rs.dev/ns/log#> .")
        self.assertRegex(lines[3], r"^@name     urn:log:harness:tree-[0-9a-f]{16}$")
        tree = lines[3].rsplit("tree-", 1)[1]
        self.assertEqual(lines[4], f"@instance urn:iki:harness:tree:{tree}")
        self.assertEqual(lines[5], "@level    log:info")
        self.assertRegex(lines[6], r"^@started  \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(lines[7], "@prev     genesis")
        first, second = body.splitlines()
        digest = harness.digest("gonk.py report 1")
        self.assertRegex(
            first,
            r"^\S+Z harness:Refusal urn:iki:harness:starter:control:evidence-required "
            rf"seq=1 cmd={digest}$",
        )
        self.assertRegex(
            second,
            r"^\S+Z harness:Warning urn:iki:harness:starter:control:review-not-queued seq=2 "
            r"session=urn:agents:session:ab-1x tool=Edit$",
        )
        self.assertNotIn("report 1", text)  # the command's digest, never its text

    def test_read_back_and_transrepted_as_ikigai_log_would(self):
        harness.fire("evidence-required", "refusal", self.repo, command="x")
        segment = harness.read_firings(self.repo)
        [f] = segment.firings
        self.assertEqual((f.control, f.kind, f.seq), ("evidence-required", "refusal", 1))
        triples = harness.firing_triples(segment)
        node = harness.I(f"{segment.name}:1")
        mine = {(p.value, o) for s, p, o in triples if s == node}
        control = harness.I(harness.CONTROL_NS + "evidence-required")
        self.assertIn((harness.RDF_TYPE, harness.I(H + "Refusal")), mine)
        self.assertIn((H + "firedControl", control), mine)
        self.assertIn((harness.LOG + "subject", control), mine)
        self.assertIn((harness.LOG + "sequence", harness.L("1", harness.XSD + "integer")), mine)
        self.assertIn((H + "commandDigest", harness.L(harness.digest("x"))), mine)
        self.assertTrue(all(isinstance(t, harness.I) for s, _, _ in triples for t in [s]))

    def test_an_unwritable_log_changes_no_decision_and_says_so_once(self):
        """The same refusal, the same exit code and message, with the log unwritable."""
        wire(self.repo)
        _, _, before = run(gonk.main, self.repo, ["report", "12"])
        state = self.repo / ".gonk" / "state" / "harness"
        shutil.rmtree(state)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("a file where the directory goes\n")
        code, out, err = run(gonk.main, self.repo, ["report", "12"])
        code2, _, err2 = run(gonk.main, self.repo, ["report", "12"])
        self.assertEqual((code, code2), (2, 2))
        self.assertEqual(out, "")
        lines = err.splitlines()
        self.assertTrue(lines[0].startswith("harness: could not record evidence-required"), err)
        self.assertEqual("\n".join(lines[1:]) + "\n", before)
        self.assertEqual(err2, before)  # said once per process, never again

    def test_a_failing_write_changes_no_decision(self):
        wire(self.repo)
        _, _, before = run(gonk.main, self.repo, ["reject", "12"])

        def full(fd, data):
            raise OSError(28, "No space left on device")

        with mock.patch.object(harness, "_write", full):
            code, _, err = run(gonk.main, self.repo, ["reject", "12"])
        self.assertEqual(code, 2)
        self.assertIn("No space left on device", err)
        self.assertTrue(err.endswith(before), err)
        self.assertEqual(len(fired(self.repo)), 1)  # the first one; the second never landed

    def test_a_corrupt_log_is_left_exactly_as_it_is(self):
        path = self.repo / harness.FIRINGS_FILE
        path.parent.mkdir(parents=True)
        for text in ["not a segment\n", "# ikigai-log v1\n@name x\n\nhalf a line"]:
            path.write_text(text)
            harness._reported = False
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                harness.fire("evidence-required", "refusal", self.repo)
            self.assertEqual(path.read_text(), text)
            self.assertIn("CorruptSegment", err.getvalue())

    def test_no_repository_records_nothing_and_an_unknown_id_is_never_raised(self):
        harness.fire("evidence-required", "refusal", None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            harness.fire("Not An Id", "refusal", self.repo)
        self.assertIn("ValueError", err.getvalue())
        self.assertFalse((self.repo / harness.FIRINGS_FILE).exists())

    def test_concurrent_writers_keep_the_sequence_dense(self):
        threads = [
            threading.Thread(target=harness.fire, args=("evidence-required", "refusal", self.repo))
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seqs = [f.seq for f in harness.read_firings(self.repo).firings]
        self.assertEqual(sorted(seqs), list(range(1, 21)))


# --------------------------------------------------------------------------- the graph


class TheGraphAgrees(unittest.TestCase):
    def test_the_starters_own_graph_is_the_default_process_and_agrees(self):
        names = new.resolve(None)
        fix = "rewrite it with `python3 tools/new.py --sync-starter`"
        self.assertEqual((ROOT / harness.GRAPH_FILE).read_text(), new.harness_ttl(names), fix)
        self.assertEqual(harness.check(ROOT), [])

    def test_every_profile_alone_and_in_combination_agrees(self):
        combos = [[n] for n in PROFILES] + [
            ["workers", "hub", "review-every-commit"],
            ["solo", "spec", "audits"],
            [n for n in PROFILES if n != "audits"],
        ]
        for names in combos:
            repo = generated(self, names, "-".join(names))
            self.assertEqual(harness.check(repo), [], names)
            self.assertEqual((repo / harness.GRAPH_FILE).read_text(), new.harness_ttl(names))

    def test_a_profile_carries_its_controls_and_no_others(self):
        solo = harness.read_graph(generated(self, ["solo"]) / harness.GRAPH_FILE)
        workers = harness.read_graph(generated(self, ["workers"]) / harness.GRAPH_FILE)
        lost = harness.I(harness.CONTROL_NS + "worker-claim-lost")
        self.assertIn(lost, workers.by_subject)
        self.assertNotIn(lost, solo.by_subject)
        hook = harness.I(harness.CONTROL_NS + "edit-without-live-claim")
        self.assertIn(hook, solo.by_subject)  # every process claims: the hook is everyone's

    def test_the_vocabulary_is_a_subset_of_one_namespace(self):
        vocab = harness.read_graph(ROOT / harness.VOCAB_FILE)
        terms = {s.value for s, _, _ in vocab.triples}
        self.assertTrue(terms)
        self.assertTrue(all(t.startswith(H) for t in terms), terms)

    def test_the_vocabulary_is_the_namespaces_public_definition(self):
        """The file parses in the subset (which refuses a blank node, a collection or a long
        string with its line, so parsing at all proves there are none), every term has a label
        and a comment, and the namespace states its one owl:versionInfo."""
        text = (ROOT / harness.VOCAB_FILE).read_text(encoding="utf-8")
        vocab = harness.Graph(harness.parse_turtle(text, str(harness.VOCAB_FILE))[0])
        for s in vocab.by_subject:
            self.assertEqual(len(vocab.values(s, harness.RDFS + "label")), 1, s.value)
            self.assertEqual(len(vocab.values(s, harness.RDFS + "comment")), 1, s.value)
        ontology = harness.I(H)
        self.assertIn(OWL + "Ontology", vocab.values(ontology, harness.RDF_TYPE))
        versions = vocab.values(ontology, OWL + "versionInfo")
        self.assertEqual(len(versions), 1, versions)
        self.assertRegex(versions[0], r"\A\d+\.\d+\.\d+\Z")

    def test_every_term_the_authors_tooling_defines_is_defined_here_the_same_way(self):
        """The authors' own tooling describes its harness in this namespace, in a private
        vocabulary file whose comments cannot be published. This file is the public definition,
        so it defines every term that one does, with the same structure: the same types,
        domains, ranges, subclasses and ikigai-log bindings. Labels and comments may differ.

        That file is read only when a checkout of the private repository sits beside this one
        (`../ikigai-devtools/claude/harness/vocabulary.ttl`). In CI, and for anyone else, it is
        not there and this test SKIPS, saying so: it holds the two together on the authors'
        machines, where both are edited."""
        if not PRIVATE_VOCAB.is_file():
            self.skipTest(f"no {PRIVATE_VOCAB.relative_to(ROOT.parent)} beside this checkout")
        public = harness.read_graph(ROOT / harness.VOCAB_FILE)
        private = harness.read_graph(PRIVATE_VOCAB)
        prose = {harness.RDFS + "label", harness.RDFS + "comment"}

        def structure(g: harness.Graph) -> set[tuple]:
            return {(s, p, o) for s, p, o in g.triples if p.value not in prose}

        missing = set(private.by_subject) - set(public.by_subject)
        self.assertFalse(missing, sorted(t.value for t in missing))
        differs = structure(private) - structure(public)
        self.assertFalse(differs, sorted((s.value, p.value, str(o)) for s, p, o in differs))

    def test_each_kind_of_disagreement_is_named(self):
        repo = generated(self, ["solo"])
        gonk_py = repo / "tools" / "gonk.py"
        graph = repo / harness.GRAPH_FILE
        agents = repo / "AGENTS.md"
        original = (gonk_py.read_text(), graph.read_text(), agents.read_text())

        def problems_after(code=None, ttl=None, md=None):
            gonk_py.write_text(code or original[0])
            graph.write_text(ttl or original[1])
            agents.write_text(md or original[2])
            return "\n".join(harness.check(repo))

        # The code fires an id the graph does not describe.
        code = original[0].replace('"reject-needs-reason"', '"reject-needs-a-reason"')
        out = problems_after(code=code)
        self.assertIn("fires `reject-needs-a-reason`, which", out)
        self.assertIn("tools/gonk.py#cmd_reject, which fires no `reject-needs-reason`", out)
        # A term the vocabulary does not define; a guide citing a heading AGENTS.md lacks.
        stage = original[1].replace("harness:stage harness:merge", "harness:stage harness:x", 1)
        out = problems_after(ttl=stage)
        self.assertIn("harness#x is not a term", out)
        heading = original[2].replace("## 3. Reproduce before you fix", "## 3. Reproduce first")
        out = problems_after(md=heading)
        self.assertIn("section '3. Reproduce before you fix' is not a heading", out)
        self.assertIn("`## 3. Reproduce first` states rules no guide", out)
        # A sensor that records firings with no proof, and a blank node.
        proof = "    harness:firedBy test:test_harness:SensorsFire.test_reject_needs_reason ."
        out = problems_after(ttl=original[1].replace(proof, "    ."))
        self.assertIn("reject-needs-reason: a sensor that writes firings names a test", out)
        out = problems_after(ttl=original[1] + '\nctl:x rdfs:label [ rdfs:label "y" ] .\n')
        self.assertIn("a blank node", out)
        self.assertEqual(problems_after(), "")

    def test_a_firing_with_an_expression_for_its_id_is_refused(self):
        tree = ast.parse("def cmd(x):\n    fire(x, 'refusal', None)\n")
        _, bad = harness._emitters_in(tree, "tools/x.py")
        self.assertEqual(len(bad), 1)
        tree = ast.parse("def fire_it(x):\n    fire(x, 'refusal', None)\n")
        self.assertEqual(harness._emitters_in(tree, "tools/x.py")[1], [])


class TheShapes(unittest.TestCase):
    """.gonk/harness/shapes.ttl: closed SHACL shapes for a guide and a control, which `check`
    validates the graph against, refusing a breach with a sentence naming the field."""

    GUIDE = "guide:reproduce-before-fix"
    SENSOR = "ctl:reject-needs-reason"

    def setUp(self):
        self.shapes = harness.read_graph(ROOT / harness.SHAPES_FILE)
        self.text = (ROOT / harness.GRAPH_FILE).read_text(encoding="utf-8")

    def entry(self, curie: str) -> tuple[int, int]:
        """The span of `curie`'s statement in the starter's graph: its first line to its `.`."""
        start = self.text.index(f"\n{curie} a ") + 1
        return start, self.text.index(" .\n", start) + 3

    def problems(self, text: str) -> list[str]:
        return harness.validate(self.shapes, harness.Graph(harness.parse_turtle(text)[0]))

    def edited(self, curie: str, old: str, new: str) -> list[str]:
        start, end = self.entry(curie)
        body = self.text[start:end]
        self.assertIn(old, body)
        return self.problems(self.text[:start] + body.replace(old, new, 1) + self.text[end:])

    def test_the_starters_own_harness_conforms(self):
        self.assertEqual(self.problems(self.text), [])
        self.assertEqual(harness.check(ROOT), [])

    def test_a_guide_missing_a_required_field_is_refused_naming_it(self):
        required = {
            'rdfs:label "reproduce before you fix" ;': "rdfs:label",
            'harness:statedIn "AGENTS.md" ;': "harness:statedIn",
            "harness:direction harness:feedforward ;": "harness:direction",
            "harness:execution harness:inferential ;": "harness:execution",
        }
        iri = harness.GUIDE_NS + "reproduce-before-fix"
        for field, name in required.items():
            with self.subTest(field=name):
                out = self.edited(self.GUIDE, field, "")
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith(f"{iri}: a guide "), out)
                self.assertIn(f"({name}: has none, needs at least 1)", out[0])
        out = self.edited(self.GUIDE, 'harness:statedIn "AGENTS.md" ;', "")
        self.assertEqual(
            out,
            [
                f"{iri}: a guide names the one file it is stated in "
                "(harness:statedIn: has none, needs at least 1)"
            ],
        )

    def test_the_shapes_are_closed(self):
        out = self.edited(
            self.GUIDE, "harness:statedIn", 'harness:enforcedAt "x" ; harness:statedIn'
        )
        self.assertEqual(len(out), 1, out)
        self.assertIn("harness:enforcedAt is not a property a guide has", out[0])
        out = self.edited(self.SENSOR, "harness:outcome", 'rdfs:seeAlso "x" ; harness:outcome')
        self.assertIn("rdfs:seeAlso is not a property a control has", "\n".join(out))

    def test_counts_kinds_and_values(self):
        out = self.edited(self.GUIDE, 'harness:statedIn "AGENTS.md"', 'harness:statedIn "A", "B"')
        self.assertIn("harness:statedIn: has 2, may have at most 1", "\n".join(out))
        out = self.edited(self.GUIDE, "harness:feedforward", "harness:feedback")
        self.assertIn("a guide is feedforward", "\n".join(out))
        out = self.edited(self.GUIDE, 'harness:statedIn "AGENTS.md"', "harness:statedIn harness:x")
        self.assertIn("is not of type string", "\n".join(out))

    def test_a_sensor_states_its_outcome_and_its_guide(self):
        iri = harness.CONTROL_NS + "reject-needs-reason"
        out = self.edited(self.SENSOR, "harness:outcome harness:refuse ;", "")
        self.assertEqual(len(out), 1, out)
        self.assertTrue(out[0].startswith(f"{iri}: a sensor states its outcome"), out)
        start, end = self.entry(self.SENSOR)
        body = re.sub(r"harness:statedBy [^;]*;", "", self.text[start:end])
        out = self.problems(self.text[:start] + body + self.text[end:])
        self.assertEqual(len(out), 1, out)
        self.assertIn("a sensor names the guide it enforces in harness:statedBy", out[0])
        # A control that is not a sensor needs neither.
        self.assertNotIn("seed-orders-tasks", "\n".join(self.problems(self.text)))

    def test_examples_belong_to_sensors(self):
        ok_ = self.edited(
            self.SENSOR,
            "harness:outcome",
            'harness:example "reject 12" ;\n'
            '    harness:counterExample "reject 12 --note why" ; harness:outcome',
        )
        self.assertEqual(ok_, [])
        out = self.edited(self.GUIDE, "harness:statedIn", 'harness:example "x" ; harness:statedIn')
        self.assertIn("harness:example is not a property a guide has", "\n".join(out))

    def test_check_refuses_a_guide_without_its_field(self):
        repo = generated(self, ["solo"])
        graph = repo / harness.GRAPH_FILE
        text = graph.read_text()
        start = text.index("\nguide:reproduce-before-fix a ")
        graph.write_text(text[:start] + text[start:].replace(' harness:section "', ' rdfs:x "', 1))
        out = "\n".join(harness.check(repo))
        self.assertIn("guide:reproduce-before-fix: a guide names the one section", out)
        self.assertIn("(harness:section: has none, needs at least 1)", out)
        self.assertIn("rdfs:x is not a property a guide has", out)

    def test_a_shape_saying_more_than_is_evaluated_is_refused(self):
        extra = harness.parse_turtle(
            "@prefix sh: <http://www.w3.org/ns/shacl#> .\n<urn:x> sh:or <urn:y> .\n"
        )[0]
        shapes = harness.Graph(self.shapes.triples + extra)
        out = harness.validate(shapes, harness.Graph([]))
        self.assertEqual(len(out), 1, out)
        self.assertIn("shacl#or, which tools/harness.py does not evaluate", out[0])

    def test_the_shapes_name_only_terms_the_vocabulary_defines(self):
        """The vocabulary is self-contained: every harness: IRI the shapes use is a term of it,
        and the shapes' own IRIs are outside its namespace."""
        vocab = harness.read_graph(ROOT / harness.VOCAB_FILE)
        terms = {s.value for s in vocab.by_subject}
        used = {
            t.value
            for triple in self.shapes.triples
            for t in triple
            if isinstance(t, harness.I) and t.value.startswith(H)
        }
        self.assertTrue(used)
        self.assertEqual(used - terms, set())
        self.assertFalse(any(s.value.startswith(H) for s in self.shapes.by_subject))
        for term in ("example", "counterExample"):
            self.assertIn(H + term, terms)
            self.assertEqual(
                vocab.values(harness.I(H + term), harness.RDFS + "domain"), [H + "Control"]
            )


class TheCommandLine(unittest.TestCase):
    def test_check_and_firings(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(harness.main(["check", "--repo", str(ROOT)]), 0)
        self.assertIn("agree with the code", out.getvalue())
        repo = scratch_repo(self)
        harness.fire("evidence-required", "refusal", repo)
        harness.fire("evidence-required", "refusal", repo)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(harness.main(["firings", "--repo", str(repo)]), 0)
        self.assertRegex(out.getvalue(), r"\s+2  refusal\s+evidence-required\s+last ")


# --------------------------------------------------------------------------- every sensor fires


LEDGER = {
    "held": (
        "Error: conflict: acme#12 is already claimed by one. Release it first (`delete "
        "urn:iki:ledger:acme:claim item=12`), or take it up with them — a claim is a fence"
    ),
    "expired": (
        "error: conflict: acme#12's lease for one expired at 2026-10-14T04:56:13.901Z, and an "
        "expired lease is never silently free: take it with `sink urn:iki:ledger:acme:claim "
        "item=12 takeover=true from=one`"
    ),
    "takeover": (
        "error: conflict: acme#12's lease for one runs until 2026-10-14T04:56:13.901Z; only an "
        "expired lease can be taken over"
    ),
}


class SensorsFire(unittest.TestCase):
    """One test per sensor that records firings, named for its control id (Proofs reads them)."""

    def setUp(self):
        self.repo = scratch_repo(self)
        wire(self.repo)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    # -- tools/gonk.py

    def test_one_claim_per_tree(self):
        fake = FakeIkigai([(":claim", ok("acme#12 claimed by agent-1\n"))])
        run(gonk.main, self.repo, ["claim", "12"], fake)
        code, _, _ = run(gonk.main, self.repo, ["claim", "13"], fake)
        self.assertEqual(code, 2)
        assert_fired(self, self.repo, "one-claim-per-tree")

    def test_evidence_required(self):
        code, _, err = run(gonk.main, self.repo, ["report", "12"])
        self.assertEqual(code, 2)
        self.assertIn("--evidence is a URL", err)
        code, _, _ = run(gonk.main, self.repo, ["close", "12", "--evidence", "merged"])
        self.assertEqual(code, 2)
        assert_fired(self, self.repo, "evidence-required")
        self.assertEqual(fired(self.repo), ["evidence-required", "evidence-required"])

    def test_reject_needs_reason(self):
        code, _, _ = run(gonk.main, self.repo, ["reject", "12"])
        self.assertEqual(code, 2)
        assert_fired(self, self.repo, "reject-needs-reason")

    def claim_refused(self, text: str, *flags: str) -> None:
        fake = FakeIkigai([(":claim", gonk.Answer(1, "", text))])
        code, _, _ = run(gonk.main, self.repo, ["claim", "12", *flags], fake)
        # The ledger's refusals are each a Conflict; gonk not answering is not.
        self.assertEqual(code, gonk.CONFLICT_EXIT if "conflict:" in text else 1)

    def test_claim_held_by_another(self):
        self.claim_refused(LEDGER["held"])
        assert_fired(self, self.repo, "claim-held-by-another")

    def test_claim_lease_expired(self):
        self.claim_refused(LEDGER["expired"])
        assert_fired(self, self.repo, "claim-lease-expired")

    def test_takeover_refused(self):
        self.claim_refused(LEDGER["takeover"], "--takeover-from", "one")
        assert_fired(self, self.repo, "takeover-refused")
        # A refusal for another reason (gonk did not answer) is not a rule's: nothing recorded.
        self.claim_refused("no answer within 30s from socket /x")
        self.assertEqual(fired(self.repo), ["takeover-refused"])

    # -- hooks/claude/require-claim.py

    def edit(self, rel: str = "README.md") -> dict:
        return {
            "tool_name": "Edit",
            "session_id": "s-1",
            "tool_input": {"file_path": str(self.repo / rel)},
        }

    def test_edit_without_live_claim(self):
        code, _ = load_hook().decide(self.edit(), FakeIkigai())
        self.assertEqual(code, 2)
        assert_fired(self, self.repo, "edit-without-live-claim")
        [f] = harness.read_firings(self.repo).firings
        self.assertEqual(f.fields, {"session": "urn:agents:session:s-1", "tool": "Edit"})

    def test_edit_hook_not_wired(self):
        (self.repo / ".gonk" / "local.toml").unlink()
        code, _ = load_hook().decide(self.edit(), FakeIkigai())
        self.assertEqual(code, 2)
        assert_fired(self, self.repo, "edit-hook-not-wired")

    def test_edit_hook_unreadable_input(self):
        (self.repo / "tools").mkdir()
        shutil.copy(ROOT / "tools" / "gonk.py", self.repo / "tools" / "gonk.py")
        shutil.copy(ROOT / "tools" / "harness.py", self.repo / "tools" / "harness.py")
        done = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "claude" / "require-claim.py")],
            input="not json",
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 2, done.stderr)
        assert_fired(self, self.repo, "edit-hook-unreadable-input")

    # -- hook rules (tools/harness.py#fire_hook_rules, through the hook)

    def bash(self, command: str) -> dict:
        return {
            "tool_name": "Bash",
            "session_id": "s-1",
            "cwd": str(self.repo),
            "tool_input": {"command": command},
        }

    def with_graph(self) -> None:
        (self.repo / harness.GRAPH_FILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / harness.GRAPH_FILE, self.repo / harness.GRAPH_FILE)

    def test_hook_pipe_hides_status(self):
        self.with_graph()
        code, message = load_hook().decide(self.bash("just gates | tail -n 40"), FakeIkigai())
        self.assertEqual(code, 2)
        self.assertIn("exit status is its last command's", message)
        assert_fired(self, self.repo, "hook-pipe-hides-status")
        [f] = harness.read_firings(self.repo).firings
        self.assertEqual(f.fields["cmd"], harness.digest("just gates | tail -n 40"))

    def test_hook_git_add_all(self):
        self.with_graph()
        code, message = load_hook().decide(self.bash("git add -A"), FakeIkigai())
        self.assertEqual(code, 2)
        self.assertIn("by name", message)
        assert_fired(self, self.repo, "hook-git-add-all")

    # -- tools/worker.py

    def worker_rig(self, **config) -> Rig:
        rig = Rig(self)
        rig.configure(**config)
        return rig

    def test_worker_refuses_root(self):
        rig = self.worker_rig()
        err = io.StringIO()
        with mock.patch.object(worker.os, "geteuid", lambda: 0), contextlib.redirect_stderr(err):
            code = worker.main(["--repo", str(rig.checkout), "run", "--once"], FakeLedger())
        self.assertEqual(code, worker.CONFIG)
        assert_fired(self, rig.checkout, "worker-refuses-root")

    def test_worker_needs_labels(self):
        rig = self.worker_rig(labels="[]")
        with self.assertRaises(gonk.ConfigError):
            worker.load_worker(gonk.load(rig.checkout))
        assert_fired(self, rig.checkout, "worker-needs-labels")

    def test_worker_needs_routers(self):
        rig = self.worker_rig(routers='["hub"]')
        with self.assertRaises(gonk.ConfigError):
            worker.load_worker(gonk.load(rig.checkout))
        assert_fired(self, rig.checkout, "worker-needs-routers")

    def test_worker_dispatched_by_a_router(self):
        rig, ledger = self.worker_rig(), FakeLedger()
        ledger.file("Labeled by anyone", ["worker:w1", "repo:lib"], dispatched_by=None)
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOTHING)
        assert_fired(self, rig.checkout, "worker-dispatched-by-a-router")
        self.assertEqual(fired(rig.checkout), ["worker-dispatched-by-a-router"])

    def test_worker_work_root_in_home(self):
        rig = self.worker_rig(work_root='"/tmp/elsewhere"')
        with self.assertRaises(gonk.ConfigError):
            rig.worker(FakeLedger())
        assert_fired(self, rig.checkout, "worker-work-root-in-home")

    def run_once(self, rig: Rig, ledger: FakeLedger, **patches):
        w = rig.worker(ledger)
        for name, value in patches.items():
            setattr(w.ledger, name, value)
        return w, w.run_once()

    def test_worker_skips_claimed(self):
        # The ledger's `next` leaves claimed items out of its ranking; the worker checks again,
        # for a ledger (or a race) that ranks one. A ranking with a claimed item drives it.
        rig, ledger = self.worker_rig(), FakeLedger()
        n = ledger.file("Held", ["worker:w1", "repo:lib"])
        ledger.force_claim(n, "chris")
        ranked = {"ranking": [{"rank": 1, "item": ledger.items[n]}]}
        w, outcome = self.run_once(rig, ledger, next=lambda *a, **k: (ranked, None))
        self.assertEqual(outcome.code, worker.NOTHING)
        w.run_once()  # the same skip at the next poll is not recorded again
        assert_fired(self, rig.checkout, "worker-skips-claimed")
        self.assertEqual(fired(rig.checkout), ["worker-skips-claimed"])

    def test_worker_one_repository_label(self):
        rig, ledger = self.worker_rig(), FakeLedger()
        ledger.file("No repository", ["worker:w1"])
        ledger.file("Not ours", ["worker:w1", "repo:elsewhere"])
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOTHING)
        assert_fired(self, rig.checkout, "worker-one-repository-label")
        self.assertEqual(fired(rig.checkout), ["worker-one-repository-label"] * 2)

    def test_worker_skips_awaiting_dispatcher(self):
        rig, ledger = self.worker_rig(), FakeLedger(older_comment=True)
        n = ledger.file("Handed back", ["worker:w1", "repo:lib"])
        ledger.add_comment(n, "Evidence: https://github.com/acme/lib/pull/1")
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOTHING)
        assert_fired(self, rig.checkout, "worker-skips-awaiting-dispatcher")

    def test_worker_skips_handed_back(self):
        rig, ledger = self.worker_rig(), FakeLedger()
        n = ledger.file("Tried", ["worker:w1", "repo:lib"])
        time.sleep(0.05)  # the hand-back comes after the dispatch, as it does in life
        worker.Attempts(rig.checkout / ".gonk" / "state" / "worker").record("acme", n, "x")
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOTHING)
        assert_fired(self, rig.checkout, "worker-skips-handed-back")

    def test_worker_one_agent_per_repository(self):
        rig, ledger = self.worker_rig(), FakeLedger()
        busy = ledger.file("Someone is on lib", ["repo:lib"])
        ledger.force_claim(busy, "chris")
        ledger.file("Also lib", ["worker:w1", "repo:lib"])
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOTHING)
        assert_fired(self, rig.checkout, "worker-one-agent-per-repository")

    def test_worker_claim_lost(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.agent_flags = ["--sleep", "60"]
        rig.configure()
        n = ledger.file("Taken over", ["worker:w1", "repo:lib"])

        def take_it():
            while not (rig.record / f"started-{n}").exists():
                time.sleep(0.05)
            ledger.force_claim(n, "someone-else")

        thread = threading.Thread(target=take_it)
        thread.start()
        _, outcome = self.run_once(rig, ledger)
        thread.join()
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        assert_fired(self, rig.checkout, "worker-claim-lost")

    def test_worker_budget_overrun(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.agent_flags = ["--sleep", "60"]
        rig.configure(budget='"2s"')
        ledger.file("Too big", ["worker:w1", "repo:lib"])
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        assert_fired(self, rig.checkout, "worker-budget-overrun")

    def test_worker_no_evidence(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.agent_flags = ["--no-pr", "--exit", "1"]
        rig.configure()
        ledger.file("Nothing to show", ["worker:w1", "repo:lib"])
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        assert_fired(self, rig.checkout, "worker-no-evidence")

    def review_job(self, ledger, rig) -> None:
        head = support_git("rev-parse", "main", cwd=rig.origin).strip()
        ledger.file(
            "Review",
            ["worker:w1", "repo:lib"],
            body=f"Review {head}..{head}.",
            kind=worker.REVIEW_JOB,
            revision=head,
        )

    def test_worker_review_changes_nothing(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.agent_flags = ["--no-pr", "--report", '{"findings": []}', "--dirty"]
        rig.configure()
        self.review_job(ledger, rig)
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        assert_fired(self, rig.checkout, "worker-review-changes-nothing")

    def test_worker_review_report_valid(self):
        rig, ledger = Rig(self), FakeLedger()
        rig.agent_flags = ["--no-pr", "--report", "not json"]
        rig.configure()
        self.review_job(ledger, rig)
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.NOT_HANDED_BACK, outcome.summary)
        assert_fired(self, rig.checkout, "worker-review-report-valid")

    def test_worker_merged_before_exit(self):
        rig, ledger = self.worker_rig(), FakeLedger()
        state = rig.gh()
        state["merge"] = {"at": "2026-01-01T00:00:00Z", "by": "dispatcher-login"}
        rig.gh_state.write_text(json.dumps(state))
        ledger.file("Merged", ["worker:w1", "repo:lib"])
        _, outcome = self.run_once(rig, ledger)
        self.assertEqual(outcome.code, worker.HANDED_BACK, outcome.summary)
        assert_fired(self, rig.checkout, "worker-merged-before-exit", "warning")

    # -- the hub, audits and review-every-commit profiles' tools

    def test_arc_needs_brief(self):
        repo = generated(self, ["hub"])
        wire(repo)
        hub = load_tool(repo, "tools/hub.py", "hub_for_harness")
        (repo / "brief.md").write_text("\n")
        code, _, _ = run(hub.main, repo, ["arc", "T", "--brief", str(repo / "brief.md")])
        self.assertEqual(code, 2)
        code, _, _ = run(hub.main, repo, ["arc", "T", "--brief", str(repo / "absent.md")])
        self.assertEqual(code, 2)
        self.assertEqual(fired(repo), ["arc-needs-brief"] * 2)
        assert_fired(self, repo, "arc-needs-brief")

    def test_dispatch_needs_new_tree(self):
        repo = generated(self, ["hub"])
        wire(repo)
        hub = load_tool(repo, "tools/hub.py", "hub_for_harness")
        dest = repo.parent / "arc-7"
        dest.mkdir()
        code, _, _ = run(hub.main, repo, ["dispatch", "7", str(dest)])
        self.assertEqual(code, 2)
        assert_fired(self, repo, "dispatch-needs-new-tree")

    def test_audit_files_exist(self):
        repo = generated(self, ["audits"])
        wire(repo)
        audit = load_tool(repo, "tools/audit.py", "audit_for_harness")
        code, _, _ = run(audit.main, repo, [str(repo / "nope.py")])
        self.assertEqual(code, 2)
        assert_fired(self, repo, "audit-files-exist")

    def test_audit_needs_budget(self):
        repo = generated(self, ["audits"])
        wire(repo)
        audit = load_tool(repo, "tools/audit.py", "audit_for_harness")
        code, _, _ = run(audit.main, repo, [str(repo / "README.md"), "--budget", "0"])
        self.assertEqual(code, 2)
        assert_fired(self, repo, "audit-needs-budget")

    def test_review_not_queued(self):
        repo = generated(self, ["review-every-commit"])
        wire(repo)
        hook = load_tool(repo, "hooks/git/review-on-commit.py", "review_for_harness")
        commands, note = hook.requests(repo, which=lambda name: None)
        self.assertEqual(commands, [])
        self.assertIn("not on PATH", note)
        assert_fired(self, repo, "review-not-queued", "warning")


# --------------------------------------------------------------------------- the proofs


RULE = """
ctl:{id} a harness:Control , harness:HookRule ;
    rdfs:label "a rule" ; rdfs:comment "A rule for a test." ;
    harness:direction harness:feedback ; harness:execution harness:computational ;
    harness:stage harness:toolCall ; harness:category harness:behavior ;
    harness:outcome harness:{outcome} ;
    harness:appliesTo "{tool}" ; harness:matchesOn harness:{over} ;
    harness:patternDialect harness:{dialect} ; harness:pattern "{pattern}" ;
    harness:says "{says}" ;
    harness:example "{example}" ;
    harness:statedBy guide:a-guide .
"""


def rule(id: str, **kw) -> str:
    fields = dict(
        outcome="refuse",
        tool="Bash",
        over="commandText",
        dialect="pythonRe",
        pattern="danger",
        says="not that",
        example="a danger here",
    )
    fields.update(kw)
    return RULE.format(id=id, **fields)


class HookRules(unittest.TestCase):
    """A hook rule is data: loaded from the graph when the hook starts, applied before each tool
    call, proved by its examples, and never applied when it is malformed."""

    def setUp(self):
        self.repo = scratch_repo(self)
        harness._reported = False
        self.addCleanup(setattr, harness, "_reported", False)

    def graph(self, *rules: str) -> None:
        prefixes = "\n".join(f"@prefix {p}: <{ns}> ." for p, ns in new.HARNESS_PREFIXES)
        path = self.repo / harness.GRAPH_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prefixes + "\n" + "".join(rules))

    def call(self, event: dict) -> tuple[int, str, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, message = load_hook().decide(event, FakeIkigai())
        return code, message, err.getvalue()

    def bash(self, command: str) -> dict:
        return {"tool_name": "Bash", "cwd": str(self.repo), "tool_input": {"command": command}}

    def test_each_shipped_rule_refuses_its_examples_and_passes_its_counter_examples(self):
        shutil.copytree(ROOT / ".gonk" / "harness", self.repo / ".gonk" / "harness")
        rules, problems = harness.load_hook_rules(self.repo)
        self.assertEqual(problems, [])
        self.assertEqual(
            sorted(r.id for r in rules), ["hook-git-add-all", "hook-pipe-hides-status"]
        )
        for r in rules:
            self.assertTrue(r.examples and r.counter_examples, r.id)
            for text in r.examples:
                code, message, _ = self.call(self.bash(text))
                self.assertEqual(code, 2, (r.id, text))
                self.assertIn(r.id, message)
            for text in r.counter_examples:
                self.assertEqual(self.call(self.bash(text))[:2], (0, ""), (r.id, text))
        self.assertEqual(
            {f.control for f in harness.read_firings(self.repo).firings},
            {"hook-git-add-all", "hook-pipe-hides-status"},
        )

    def test_a_rule_applies_only_to_the_tools_it_names(self):
        self.graph(rule("only-bash"))
        event = {"tool_name": "Grep", "cwd": str(self.repo), "tool_input": {"command": "danger"}}
        self.assertEqual(self.call(event)[:2], (0, ""))
        self.assertEqual(self.call(self.bash("danger"))[0], 2)

    def test_a_malformed_rule_is_reported_at_load_and_ignored(self):
        self.graph(
            rule("good", pattern="danger"),
            rule("bad-regex", pattern="(danger", example="(danger"),
            rule("bad-dialect", dialect="pythonRe2", pattern="rm", example="rm"),
            rule("example-misses", pattern="rm -rf", example="rm -r"),
            rule("bad-input", over="filePathz", pattern="rm", example="rm"),
        )
        rules, problems = harness.load_hook_rules(self.repo)
        self.assertEqual([r.id for r in rules], ["good"])
        said = "\n".join(problems)
        for cid, words in [
            ("bad-regex", "not a Python regular expression"),
            ("bad-dialect", "a dialect this hook does not read"),
            ("example-misses", "does not fire"),
            ("bad-input", "neither harness:commandText"),
        ]:
            self.assertIn(cid, said)
            self.assertIn(words, said)
        # Ignored, never silently applied: what the malformed rules would match passes, and
        # the hook says on standard error which rules it ignored.
        code, message, err = self.call(self.bash("(danger rm -r -rf"))
        self.assertEqual(code, 2)
        self.assertEqual(message, "good: not that")
        self.assertEqual(self.call(self.bash("rm -rf /tmp/x"))[:2], (0, ""))
        self.assertIn("hook rule ignored", err)
        self.assertIn("bad-regex", err)
        self.assertEqual(fired_ids(self.repo), ["good"])

    def test_a_counter_example_that_fires_makes_the_rule_malformed(self):
        text = rule("too-wide", pattern="add").replace(
            'harness:example "a danger here"',
            'harness:example "git add -A" ; harness:counterExample "git add README.md"',
        )
        self.graph(text)
        rules, problems = harness.load_hook_rules(self.repo)
        self.assertEqual(rules, [])
        self.assertIn("counter-example 'git add README.md' fires", problems[0])

    def test_a_missing_field_is_malformed(self):
        self.graph(rule("no-says").replace('harness:says "not that" ;', ""))
        rules, problems = harness.load_hook_rules(self.repo)
        self.assertEqual(rules, [])
        self.assertIn("no harness:says", problems[0])

    def test_a_graph_that_does_not_parse_applies_no_rule_and_says_so(self):
        self.graph(rule("good"), "ctl:x ctl:y [ ] .\n")
        rules, problems = harness.load_hook_rules(self.repo)
        self.assertEqual(rules, [])
        self.assertIn("does not load", problems[0])
        code, message, err = self.call(self.bash("danger"))
        self.assertEqual((code, message), (0, ""))
        self.assertIn("does not load", err)

    def test_no_graph_is_no_rules_and_nothing_said(self):
        self.assertEqual(harness.load_hook_rules(self.repo), ([], []))
        self.assertEqual(self.call(self.bash("just gates | tail")), (0, "", ""))

    def test_a_warning_rule_over_a_path_lets_the_call_through_and_tells_the_agent(self):
        self.graph(
            rule(
                "generated-file",
                outcome="warn",
                tool="Write",
                over="filePath",
                pattern="\\\\Agenerated/",
                says="this file is generated",
                example="generated/x.txt",
            )
        )
        rules, problems = harness.load_hook_rules(self.repo)
        self.assertEqual(problems, [])
        # The path is matched relative to the repository, so a relative and an absolute path
        # are the same; the firing names the tool and the session, never the path.
        event = {
            "tool_name": "Write",
            "session_id": "s-2",
            "cwd": str(self.repo),
            "tool_input": {"file_path": "generated/x.txt"},
        }
        self.assertEqual(harness.fire_hook_rules(rules, event, self.repo), ([], rules))
        [f] = harness.read_firings(self.repo).firings
        self.assertEqual((f.control, f.kind), ("generated-file", "warning"))
        self.assertEqual(f.fields, {"session": "urn:agents:session:s-2", "tool": "Write"})
        # Through the hook, in a tree not wired to gonk: the claim check refuses the edit, and the
        # warning is said beside the refusal.
        event["tool_input"]["file_path"] = str(self.repo / "generated" / "x.txt")
        code, message, _ = self.call(event)
        self.assertEqual(code, 2)
        self.assertIn("generated-file: this file is generated", message)
        self.assertIn("require-claim: this repository is not wired", message)
        # A path the claim check exempts goes through, with the warning alone.
        self.graph(
            rule(
                "state-file",
                outcome="warn",
                tool="Write",
                over="filePath",
                pattern="\\\\A\\\\.gonk/state/",
                says="state file",
                example=".gonk/state/x",
            )
        )
        state = {
            "tool_name": "Write",
            "cwd": str(self.repo),
            "tool_input": {"file_path": str(self.repo / ".gonk" / "state" / "x")},
        }
        self.assertEqual(self.call(state)[:2], (0, "state-file: state file"))

    def test_a_warning_reaches_the_agent_as_context_on_standard_output(self):
        (self.repo / "tools").mkdir()
        shutil.copy(ROOT / "tools" / "gonk.py", self.repo / "tools" / "gonk.py")
        shutil.copy(ROOT / "tools" / "harness.py", self.repo / "tools" / "harness.py")
        self.graph(rule("loud", outcome="warn", pattern="echo", example="echo hi"))
        done = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "claude" / "require-claim.py")],
            input=json.dumps(self.bash("echo hi")),
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)["hookSpecificOutput"]
        self.assertEqual(
            out, {"hookEventName": "PreToolUse", "additionalContext": "loud: not that"}
        )

    def test_check_runs_the_examples_as_proofs(self):
        shutil.copytree(ROOT / ".gonk" / "harness", self.repo / ".gonk" / "harness")
        path = self.repo / harness.GRAPH_FILE
        text = path.read_text()
        path.write_text(
            text.replace('harness:example "git add -A" ;', 'harness:example "git status" ;')
        )
        problems = harness.check(self.repo)
        self.assertIn(
            f"{harness.CONTROL_NS}hook-git-add-all: its example 'git status' does not fire",
            problems,
        )

    def test_the_shapes_hold_a_hook_rule_to_its_fields(self):
        shapes = harness.read_graph(ROOT / harness.SHAPES_FILE)
        prefixes = "\n".join(f"@prefix {p}: <{ns}> ." for p, ns in new.HARNESS_PREFIXES)
        bare = (
            rule("bare")
            .replace('harness:says "not that" ;', "")
            .replace('harness:example "a danger here" ;', "")
        )
        g = harness.Graph(harness.parse_turtle(prefixes + bare)[0])
        said = "\n".join(harness.validate(shapes, g))
        self.assertIn("one sentence the agent sees", said)
        self.assertIn("at least one example", said)


def fired_ids(repo: Path) -> list[str]:
    segment = harness.read_firings(repo)
    return [f.control for f in segment.firings] if segment else []


class Proofs(unittest.TestCase):
    """The graph's `harness:firedBy` and the tests above say the same thing, both ways."""

    def asserted(self) -> dict[str, set[str]]:
        """{control id: the SensorsFire tests that assert it fired}, read from this module."""
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        found: dict[str, set[str]] = {}
        for cls in tree.body:
            if not (isinstance(cls, ast.ClassDef) and cls.name == "SensorsFire"):
                continue
            for method in cls.body:
                if not (isinstance(method, ast.FunctionDef) and method.name.startswith("test_")):
                    continue
                for call in ast.walk(method):
                    if (
                        isinstance(call, ast.Call)
                        and getattr(call.func, "id", None) == "assert_fired"
                        and isinstance(call.args[2], ast.Constant)
                    ):
                        test = f"{harness.TEST_NS}test_harness:SensorsFire.{method.name}"
                        found.setdefault(call.args[2].value, set()).add(test)
        return found

    def test_every_sensor_that_records_firings_is_fired_by_the_tests_it_names(self):
        graph = every_fragment()
        asserted = self.asserted()
        controls = set(graph.of_type(H + "Control"))
        firing = {harness.control_id(c) for c in controls if harness.fires_into_log(graph, c)}
        self.assertTrue(firing)
        for cid in sorted(firing):
            named = set(graph.values(harness.I(harness.CONTROL_NS + cid), H + "firedBy"))
            self.assertTrue(named, f"{cid} names no test")
            self.assertEqual(named, asserted.get(cid, set()), cid)
        self.assertEqual(set(asserted) - firing, set(), "a test asserts a sensor the graph lacks")

    def test_each_sensor_has_its_own_test_named_for_it(self):
        for cid in self.asserted():
            self.assertTrue(
                hasattr(SensorsFire, "test_" + cid.replace("-", "_")), f"SensorsFire.test_{cid}"
            )


class Wiring(unittest.TestCase):
    """A sensor that is not wired is silent because nothing runs it, and the graph says which
    (harness:wiredBy on the fragment, harness:wired loaded by `gonk.py harness`): a coverage
    answer can then tell it from a sensor that is wired and quiet. Ledger item 1480."""

    def test_a_wiredby_names_a_part_the_process_carries(self):
        parts = harness.process_parts(ROOT)
        self.assertIn("claude:require-claim", parts)
        for repo in [generated(self, names, "-".join(names)) for names in [["solo"], ["workers"]]]:
            graph = harness.read_graph(repo / harness.GRAPH_FILE)
            carried = harness.process_parts(repo)
            stated = {
                part
                for s in graph.of_type(H + "Control")
                for part in graph.values(s, H + "wiredBy")
            }
            self.assertTrue(stated)
            self.assertLessEqual(stated, carried)

    def test_only_a_sensor_says_what_wires_it(self):
        repo = generated(self, ["solo"])
        graph = repo / harness.GRAPH_FILE
        text = graph.read_text().replace(
            'harness:section "1. Claim the item before you write" ;',
            'harness:section "1. Claim the item before you write" ;\n    harness:wiredBy "x" ;',
            1,
        )
        graph.write_text(text)
        self.assertIn("only a sensor says what wires it", "\n".join(harness.check(repo)))

    def test_a_wiredby_no_part_carries_is_named(self):
        repo = generated(self, ["solo"])
        graph = repo / harness.GRAPH_FILE
        graph.write_text(
            graph.read_text().replace(
                'harness:wiredBy "claude:require-claim"', 'harness:wiredBy "no:such"', 1
            )
        )
        out = "\n".join(harness.check(repo))
        self.assertIn("wiredBy 'no:such'", out)
        self.assertIn(".gonk/process.toml does not carry", out)

    def test_the_run_says_which_sensors_are_wired_here(self):
        """`gonk.py harness` decides wiring from the working tree's own files, the same checks
        `init` reports, and loads it as harness:wired beside the graph."""
        repo = generated(self, ["solo"])
        wire(repo)  # a claim needs gonk; the harness load does not, and the fake answers it
        fake = FakeIkigai(
            [
                ("graph-update", ok("updated <urn:iki:harness:graph:acme>: +9 -0 quads\n")),
                (":store:graph-select", ok("?control\n")),
            ]
        )
        code, out, err = run(gonk.main, repo, ["harness"], fake)
        self.assertEqual(code, 0, out + err)
        # The hook is off here (no .claude/settings*.json): its sensors are named not wired.
        self.assertIn("not wired: ctl:edit-without-live-claim", out)
        update = next(stdin for command, stdin in fake.calls if "graph-update" in command)
        self.assertIn("<urn:iki:harness:starter:control:edit-without-live-claim>", update)
        self.assertIn("https://ikigai-rs.dev/ns/harness#wired", update)
        self.assertIn('"false"^^<http://www.w3.org/2001/XMLSchema#boolean>', update)

    def test_the_same_tree_wired_says_so(self):
        repo = generated(self, ["solo"])
        wire(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text(
            '{"hooks": {"PreToolUse": [{"command": "python3 hooks/claude/require-claim.py"}]}}\n'
        )
        fake = FakeIkigai(
            [
                ("graph-update", ok("updated <urn:iki:harness:graph:acme>: +9 -0 quads\n")),
                (":store:graph-select", ok("?control\n")),
            ]
        )
        code, out, _ = run(gonk.main, repo, ["harness"], fake)
        self.assertEqual(code, 0, out)
        self.assertIn("wired: ctl:edit-without-live-claim", out)
        self.assertNotIn("not wired: ctl:edit-without-live-claim", out)


if __name__ == "__main__":
    unittest.main()
