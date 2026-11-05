"""A review's findings: their format, their validation, and filing them (ledger item 3065).

Two tools share this. The worker (tools/worker.py) validates the agent's report and carries the
findings on its hand-back comment, because its grant reads, claims and comments and cannot file.
The dispatcher (`gonk.py findings N`) reads that comment, validates it by the same rules, and files
the items. Standard library only. Filing goes through whatever `ikigai` and `cfg` the caller holds
(`gonk.Ikigai` and `gonk.Config`), so this module imports neither tool.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

# The item classes of the review mode: the worker tells a review job from a change job by this,
# the item's `kind`, and nothing else. The ledger accepts any subclass of its Item as a `kind`.
REVIEW_JOB = "https://ikigai-rs.dev/ns/ledger#ReviewJob"
FINDING = "https://ikigai-rs.dev/ns/ledger#Finding"
SEVERITIES = ("critical", "major", "minor")
SHA_SHAPE = re.compile(r"^[0-9a-f]{7,64}$")
FINDING_FIELDS = ("severity", "title", "problem", "fix", "path")
BLOCK_FIELDS = {"base", "revision", "findings"}
MAX_REPORT_PROBLEMS = 20

# What one hand-back comment may come to. The ledger documents no limit, so this is this tool's
# own bound; a larger comment is said to be too large, never truncated.
MAX_HAND_BACK_BYTES = 64 * 1024
FENCE_OPEN, FENCE_CLOSE = "```json", "```"
# Written into a command's quoted value, so it holds nothing that could end the quotes.
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class Finding:
    severity: str
    title: str
    problem: str
    fix: str
    path: str  # normalized, relative to the repository's root
    line: int | None = None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass
class HandBack:
    """What a review's hand-back says: the range it reviewed, and what it found there."""

    base: str
    head: str
    findings: list[Finding]


@dataclass
class Filed:
    number: int
    iri: str
    outcome: str  # the ledger's word: `filed`, or `existing` for a key it already holds


def is_review_job(item: dict) -> bool:
    return item.get("kind") == REVIEW_JOB


def repo_labels(item: dict) -> list[str]:
    return [label[5:] for label in item.get("labels") or [] if label.startswith("repo:")]


def said(problems: list[str]) -> str:
    """Problems as one clause, bounded: a report may have a fault per finding."""
    more = len(problems) - MAX_REPORT_PROBLEMS
    return "; ".join(problems[:MAX_REPORT_PROBLEMS] + ([f"and {more} more"] if more > 0 else []))


# --------------------------------------------------------------------------- validation


def path_problem(path: str, tree: Path | None) -> str | None:
    """Why a finding's `path` is not inside the repository, or None. It need not exist at the
    head: a finding may be about a file the range removed. Only a `tree` can show a symbolic link
    leading out: the dispatcher has none, and to it the path is only text in an IRI."""
    if not path or "\x00" in path or path.startswith("/"):
        return "is not a path relative to the repository's root"
    parts = PurePosixPath(path).parts
    if ".." in parts or ".git" in parts:
        return "is outside the repository"
    if tree is not None:
        root = tree.resolve()
        if not (root / path).resolve().is_relative_to(root):
            return "leads outside the repository through a symbolic link"
    return None


def encodable(text: str) -> bool:
    """JSON can spell a lone surrogate, which cannot be digested or sent to the ledger."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def findings_in(doc: object, tree: Path | None) -> tuple[list[Finding], list[str]]:
    """The findings of a parsed report, and what is wrong with it: one problem anywhere refuses
    it. The one validator: of the agent's report in the worker, of the block in the dispatcher."""
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        return [], ['it is not an object with a list under "findings"']
    findings, problems = [], []
    for at, entry in enumerate(doc["findings"]):
        where = f"findings[{at}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not an object")
            continue
        wrong = []
        for name in FINDING_FIELDS:
            value = entry.get(name)
            if not isinstance(value, str) or not value.strip():
                wrong.append(f"{name} is not a non-empty string")
            elif not encodable(value):
                wrong.append(f"{name} is not valid Unicode")
        if not wrong:
            if entry["severity"] not in SEVERITIES:
                wrong.append(f"severity is not one of {', '.join(SEVERITIES)}")
            if len(entry["title"].strip().splitlines()) != 1:
                wrong.append("title is not one line")
            problem = path_problem(entry["path"], tree)
            if problem:
                wrong.append(f"path {problem}")
        line = entry.get("line")
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            wrong.append("line is not a positive whole number")
        unknown = sorted(set(entry) - {*FINDING_FIELDS, "line"})
        if unknown:
            wrong.append(f"it has fields the format does not: {', '.join(unknown)}")
        if wrong:
            problems += [f"{where}: {w}" for w in wrong]
            continue
        findings.append(
            Finding(
                entry["severity"],
                entry["title"].strip(),
                entry["problem"].strip(),
                entry["fix"].strip(),
                str(PurePosixPath(entry["path"])),
                line,
            )
        )
    return findings, problems


def parse_report(text: str, tree: Path | None = None) -> tuple[list[Finding], list[str]]:
    """The findings of a report as the agent wrote it."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        return [], [f"it is not well-formed JSON: {e}"]
    return findings_in(doc, tree)


# --------------------------------------------------------------------------- the hand-back


def findings_block(findings: list[Finding], base: str, head: str) -> str:
    """The findings as the hand-back carries them: the report's format, with the range. Each line
    of the JSON starts with a space or a brace, so nothing in a finding can read as a fence, and
    it is all ASCII, so nothing in a finding can read as a line break."""
    entries = []
    for f in findings:
        entry = {
            "severity": f.severity,
            "title": f.title,
            "problem": f.problem,
            "fix": f.fix,
            "path": f.path,
        }
        entries.append({**entry, "line": f.line} if f.line else entry)
    doc = {"base": base, "revision": head, "findings": entries}
    return "\n".join([FENCE_OPEN, json.dumps(doc, indent=2), FENCE_CLOSE])


def latest_hand_back(item: dict) -> dict | None:
    """The comment that is this job's latest hand-back, or None. A hand-back starts `Evidence:`,
    and so does the note of a close, so on a closed job only a comment carrying a block counts.
    On an open job the newest one is the answer, block or not: an older block is a run the newer
    one replaced, and is never fallen back on."""
    comments = sorted(item.get("comments") or [], key=lambda c: c.get("time") or "")
    found = [c for c in comments if (c.get("text") or "").lstrip().startswith("Evidence:")]
    if item.get("status", "open") != "open":
        found = [c for c in found if FENCE_OPEN in (c.get("text") or "").splitlines()]
    return found[-1] if found else None


def parse_hand_back(text: str, revision: object = None) -> tuple[HandBack | None, list[str]]:
    """The review in a hand-back comment, or what is wrong with it. `revision` is the job's own:
    when it is a sha the block's head must be that commit. A branch or a tag names no one commit
    once it has moved, so the job's pinned range, not this, is what a dispatcher checks there."""
    lines = text.splitlines()
    opens = [at for at, line in enumerate(lines) if line.strip() == FENCE_OPEN]
    if not opens:
        return None, ["it has no ```json block of findings"]
    if len(opens) > 1:
        return None, [f"it has {len(opens)} ```json blocks, and a hand-back carries one"]
    body = lines[opens[0] + 1 :]
    if FENCE_CLOSE not in [line.strip() for line in body]:
        return None, ["its ```json block is never closed"]
    block = "\n".join(body[: [line.strip() for line in body].index(FENCE_CLOSE)])
    try:
        doc = json.loads(block)
    except (ValueError, RecursionError) as e:
        return None, [f"its block is not well-formed JSON: {e}"]
    findings, problems = findings_in(doc, None)
    if not isinstance(doc, dict):
        return None, problems
    unknown = sorted(set(doc) - BLOCK_FIELDS)
    if unknown:
        problems.append(f"its block has fields the format does not: {', '.join(unknown)}")
    base, head = doc.get("base"), doc.get("revision")
    for name, value in (("base", base), ("revision", head)):
        if not isinstance(value, str) or not SHA_SHAPE.match(value):
            problems.append(f"its block's {name} is not a commit sha")
    if isinstance(head, str) and isinstance(revision, str) and SHA_SHAPE.match(revision):
        if not head.startswith(revision):
            problems.append(f"its block's revision {head} is not the job's revision {revision}")
    if problems:
        return None, problems
    return HandBack(base, head, findings), []


# --------------------------------------------------------------------------- filing


def finding_key(job_number: int, finding: Finding) -> str:
    """The finding's key: its job and a digest of where and what, so a rerun finds its item."""
    text = "\n".join([finding.path, str(finding.line or ""), finding.title])
    return f"review:{job_number}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def finding_about(repo: str, path: str) -> str:
    """The IRI of a file in a repository: `about=` takes only absolute IRIs, so a path is
    refused as it stands, and a space in one would divide it into two."""
    return f"urn:repo:file:{quote(repo, safe='')}/{quote(path, safe='/')}"


def finding_content(finding: Finding, base: str, head: str, repo: str, display: str) -> str:
    """What is appended: the first line is the item's title, the rest its body."""
    return "\n".join(
        [
            finding.title,
            "",
            "Problem:",
            finding.problem,
            "",
            "Fix:",
            finding.fix,
            "",
            f"Location: {finding.location} in repo:{repo} at {head}",
            f"Found by the review job {display}, reviewing {base}..{head}.",
        ]
    )


def append_finding(ikigai, cfg, content: str, **words: str) -> tuple[dict | None, str | None]:
    """A keyed append's JSON answer. Every value is quoted: the engine reads an unquoted IRI as a
    reference to resolve, not as text."""
    quoted = " ".join(f'{name}="{value}"' for name, value in words.items())
    command = f"sink {cfg.resource('append')} {quoted} as=application/json"
    answer = ikigai.run(command, stdin=content)
    if not answer.ok:
        return None, answer.text() or f"exit {answer.code}"
    try:
        return json.loads(answer.out), None
    except ValueError as e:
        return None, f"the JSON face did not parse: {e}"


def link_to_job(ikigai, cfg, child: int, job_number: int) -> str | None:
    """Link a finding to its job unless it is linked already; what went wrong, or None. An edge,
    not an argument of `append`, which ignores a `parent=` it does not declare."""
    answer = ikigai.run(f"source {cfg.resource(f'item:{child}')} as=application/json")
    if not answer.ok:
        return answer.text() or f"exit {answer.code}"
    try:
        links = json.loads(answer.out)["item"].get("links") or []
    except (ValueError, KeyError) as e:
        return f"the item's JSON face did not parse: {e}"
    if any(
        link.get("type") == "parent" and (link.get("target") or {}).get("number") == job_number
        for link in links
    ):
        return None
    command = f"sink {cfg.resource('link')} item={child} type=parent"
    linked = ikigai.run(command, stdin=str(job_number))
    return None if linked.ok else linked.text() or f"exit {linked.code}"


def file_findings(ikigai, cfg, job: dict, review: HandBack) -> tuple[list[Filed], str | None]:
    """One item per finding, and what stopped the filing, if anything did. The key makes the
    append and so the whole of this safe to repeat, and two findings with one key are one item.
    The job is linked here on a rerun too, when a run before this one stopped between the append
    and the link."""
    number, display = job["number"], job.get("display")
    repos = repo_labels(job)
    if len(repos) != 1 or not REPO_NAME.match(repos[0]):
        return [], f"it needs one usable repo: label, and has {', '.join(repos) or 'none'}"
    repo = repos[0]
    filed: dict[str, Filed] = {}
    for finding in review.findings:
        answer, err = append_finding(
            ikigai,
            cfg,
            finding_content(finding, review.base, review.head, repo, display),
            kind=FINDING,
            revision=review.head,
            about=finding_about(repo, finding.path),
            labels=f"severity:{finding.severity},repo:{repo}",
            key=finding_key(number, finding),
        )
        if answer is None:
            return list(filed.values()), err
        ref = answer.get("item") or {}
        iri, child = ref.get("iri"), ref.get("number")
        if not iri or not isinstance(child, int):
            return list(filed.values()), f"its answer names no item: {answer}"
        if answer.get("status") != "deleted":
            problem = link_to_job(ikigai, cfg, child, number)
            if problem:
                return list(filed.values()), problem
        filed.setdefault(iri, Filed(child, iri, answer.get("outcome") or "filed"))
    return list(filed.values()), None
