#!/usr/bin/env python3
"""The worker's upstream guard: an unattended agent posts only to GitHub projects under
ikigai-rs, mechanically. Ledger item 4449.

A line in the brief was not enough: twice a worker opened an issue on a project outside
ikigai-rs (rudof-project/rudof#963 on 2026-11-22, oxigraph/oxigraph#2050 on 2026-11-26). This
module refuses such a post before it leaves the machine. tools/worker.py sets it up for the
agent it runs (`guard_env`, `with_guard`, `prepare_upstream_guard`) in three places:

- **`gh` and `git` wrappers first on PATH** (`worker/guard/bin/`), for every agent: Claude
  Code, Codex, Hermes or a command of one's own. Each wrapper runs `main(["gh", ...])` or
  `main(["git", ...])` here. That refuses (exit 3, the sentence on standard error) or execs the
  real program, the next one of that name on PATH.
- **A block at the end of the agent account's shell profiles** (`install_profiles`). A login
  shell (`bash -lc`, `zsh -lc`, the way Codex runs commands) runs macOS's path_helper and
  Homebrew's shellenv, which put /usr/bin and /opt/homebrew/bin first. The block puts the
  wrappers back in front, and only when `IKIGAI_UPSTREAM_GUARD_BIN` is set, which only the
  worker's agent has. `check_login_shells` proves it before any agent starts.
- **A Claude Code PreToolUse hook** (`main(["hook"])`). It reads each `Bash` command's text
  and refuses the same calls however the program is named (`/opt/homebrew/bin/gh`, `env gh`,
  `bash -c "gh ..."`, `eval`, `$(...)`, `xargs`, `find -exec`, a script file it runs). It exits
  2 with the sentence. A command it cannot read is refused, never let through.

What is refused:

- any `gh` command that is not a read (an allowlist: `view`, `list`, `status`, `diff`,
  `checks`, `search`, `gh api` GET, a GraphQL query, ...) whose repository is not under
  ikigai-rs. The repository can be named by `-R`, `--repo`, `GH_REPO`, a URL argument or a
  `repos/OWNER/REPO/...` path. When none is named, the checkout's GitHub remotes count, all of
  them, since gh may pick `upstream` over `origin`. Also refused: a command whose target is no
  repository (`gist create`, `repo fork` to the account, `ssh-key add`), `gh alias set|import`
  and `gh extension install`, and a command gh does not have built in (an alias or extension
  can run anything);
- a GraphQL mutation, and GraphQL the guard cannot read (`-F query=@-`, `--input -`);
- `git push` (and `send-pack`, `subtree push`, an alias that expands to one, a submodule push)
  to a remote whose URL is not under github.com/ikigai-rs/. A push to a local path or
  `file://` URL is not a post and goes through (the tests of this repository do it).

It is a guard against a mistake, not a boundary against a hostile agent: the agent runs as an
account that could call the real program by other means. The capability fix is a token that
cannot write outside ikigai-rs (a GitHub App installed only there; worker/README.md, "The
upstream guard").
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ORG = "ikigai-rs"

# Exit statuses: a wrapper's refusal is 3 (a conflict, as gonk.py's), the hook's is 2 (the
# status Claude Code reads as "refuse this tool call").
WRAPPER_REFUSED = 3
HOOK_REFUSED = 2

INSTEAD = (
    f"Post nothing outside {ORG}: write what you would have posted (the issue's title and body, "
    "the upstream repository and why) in your pull request's description or your hand-back, "
    "and the dispatcher decides whether a person files it."
)

GUARD_BIN = Path(__file__).resolve().parent.parent / "worker" / "guard" / "bin"
# Set in the agent's environment only (tools/worker.py, guard_env); the profile block reads it.
GUARD_ENV = "IKIGAI_UPSTREAM_GUARD_BIN"


class Unreadable(Exception):
    """A command, script or GraphQL document the guard cannot read: it is refused."""


class Refusal(str):
    """A refusal's sentence, and the harness control (profiles/harness/workers.ttl) it fires."""

    control: str

    def __new__(cls, control: str, text: str):
        self = super().__new__(cls, text)
        self.control = control
        return self


def tagged(why: str | None, control: str) -> Refusal | None:
    """`why` as a refusal of `control`, unless it is none or already says its control."""
    if why is None or isinstance(why, Refusal):
        return why
    return Refusal(control, why)


def sensor(control: str) -> str:
    """Names the harness control a refusal fires (tools/harness.py reads the literal)."""
    return control


def repo_of(cwd: str | None) -> Path | None:
    """The repository holding `cwd` (its `.gonk/layout.toml`), where the firing is recorded."""
    try:
        here = Path(cwd or ".").resolve()
    except OSError:
        return None
    for candidate in (here, *here.parents):
        if (candidate / ".gonk" / "layout.toml").is_file():
            return candidate
    return None


def fire_refusal(why: str, cwd: str | None, tool: str | None = None, session=None) -> None:
    """Record the refusal as a firing of its control in the repository's firing log. Never
    raises and never prints to standard output; the refusal stands either way."""
    control = getattr(why, "control", None)
    repo = repo_of(cwd)
    if not control or repo is None:
        return
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import harness  # the repository's own copy, beside this file

        harness.fire(control, "refusal", repo, tool=tool, session=session)
    except Exception:  # noqa: BLE001  (best effort: the refusal is the decision)
        pass


# --------------------------------------------------------------------------- repositories

REPO_URL = re.compile(
    r"^(?:https?://)?(?:[^@/]+@)?(?P<host>github\.com|www\.github\.com)/(?P<owner>[^/]+)/(?P<repo>[^/#?]+)",
    re.IGNORECASE,
)


def under_org(owner: str) -> bool:
    return owner.lower() == ORG


def odd_path(url: str) -> bool:
    """A URL or spec with a `.` or `..` segment, an empty one or a %-escape: it can name a place
    other than the one it seems to (`https://github.com/ikigai-rs/../outside/x`)."""
    path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", url.strip(), flags=re.I)
    path = re.sub(r"^[^/:]+@[^/:]+:", "", path)  # git@github.com:owner/repo
    path = path.split("?", 1)[0].split("#", 1)[0]
    if "%" in path or "\\" in path:
        return True
    segments = path.strip("/").split("/")
    return any(seg in (".", "..") for seg in segments) or "" in segments[:-1]


def slug_of(spec: str) -> tuple[str, str] | None:
    """(owner, repo) of a gh repository spec: `OWNER/REPO`, `HOST/OWNER/REPO` (github.com only)
    or a github.com URL; None when it is none of these (another host, or not a repository)."""
    spec = spec.strip()
    if odd_path(spec):
        return None
    m = REPO_URL.match(spec)
    if m:
        return m.group("owner"), m.group("repo").removesuffix(".git")
    parts = spec.split("/")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    if len(parts) == 3 and parts[0].lower() == "github.com" and parts[1] and parts[2]:
        return parts[1], parts[2]
    return None


def remote_url_target(url: str) -> tuple[str, str] | None | str:
    """Where a git URL points: "local" for a path or file:// URL, (owner, repo) for github.com,
    None for anything else (another host, or a URL this cannot read)."""
    url = url.strip()
    if not url or UNKNOWN in url:
        return None
    if url.startswith("file://") or url.startswith(("/", "./", "../", "~")) or url == ".":
        return "local"
    if odd_path(url):
        return None
    m = re.match(
        r"^(?:ssh://)?(?:[^@/]+@)?github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)/?$", url
    )
    if m:
        return m.group("owner"), m.group("repo").removesuffix(".git")
    m = REPO_URL.match(url)
    if m:
        return m.group("owner"), m.group("repo").removesuffix(".git")
    if "://" not in url and not re.match(r"^[^/]+:", url):
        return "local"  # a relative path with no scheme and no `host:` (git's own rule)
    return None


def run_git(args: list[str], cwd: str | None, env: dict[str, str]) -> list[str]:
    """The lines a read-only git command prints, or [] when it fails. Runs the real git."""
    git = real_program("git", env)
    if git is None:
        return []
    try:
        done = subprocess.run(
            [git, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return done.stdout.splitlines() if done.returncode == 0 else []


def github_remotes(cwd: str | None, env: dict[str, str]) -> list[str]:
    """Every remote URL (fetch and push) of the checkout at cwd."""
    urls = []
    for name in run_git(["remote"], cwd, env):
        urls += run_git(["remote", "get-url", "--all", name], cwd, env)
        urls += run_git(["remote", "get-url", "--push", "--all", name], cwd, env)
    return urls


def refuse_owner(what: str, owner: str, repo: str) -> str:
    return f"Refused: {what} targets {owner}/{repo}, which is not under {ORG}/. {INSTEAD}"


def read_file(path: str, cwd: str | None) -> str:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd or ".") / p
    try:
        return p.read_text(errors="replace")
    except OSError as e:
        raise Unreadable(f"cannot read {path} ({e.strerror or e})") from None


# --------------------------------------------------------------------------- gh

# gh's built-in commands. Any other first word is an alias or an extension, which can run
# anything, so it is refused.
GH_GROUPS = {
    "agent-task",
    "alias",
    "api",
    "attestation",
    "auth",
    "browse",
    "cache",
    "codespace",
    "completion",
    "config",
    "extension",
    "gist",
    "gpg-key",
    "help",
    "issue",
    "label",
    "org",
    "pr",
    "preview",
    "project",
    "release",
    "repo",
    "ruleset",
    "run",
    "search",
    "secret",
    "ssh-key",
    "status",
    "variable",
    "version",
    "workflow",
}
# THE ALLOWLIST. A subcommand of any group with one of these names only reads.
GH_READ_SUBS = {"view", "list", "status", "diff", "checks"}
# Whole commands that only read, whatever follows them.
GH_READ_GROUPS = {"search", "status", "browse", "help", "version", "completion"}
# Further reads, by group. `auth git-credential` is git's credential helper (`gh auth setup-git`
# installs it), so a push to ikigai-rs needs it.
GH_READS = {
    "auth": {"status", "git-credential"},
    "config": {"get"},
    "release": {"download", "verify", "verify-asset"},
    "run": {"watch", "download"},
    "repo": {"clone"},
    "pr": {"checkout"},
    "gist": {"clone"},
    "ruleset": {"check"},
    "attestation": {"verify", "download", "trusted-root"},
    "variable": {"get"},
    "extension": {"search", "browse"},
    "project": {"field-list", "item-list"},
    "workflow": set(),
    "cache": set(),
}
# Groups whose writes go to no repository: the account, or gh's own setup.
GH_NO_REPO = {
    "agent-task",
    "auth",
    "codespace",
    "config",
    "gist",
    "gpg-key",
    "org",
    "preview",
    "ssh-key",
}

# gh flags that take a value, so that the value is not read as a positional argument.
GH_VALUE_FLAGS = {
    "-R",
    "--repo",
    "-t",
    "--title",
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-B",
    "--base",
    "-H",
    "--head",
    "-a",
    "--assignee",
    "-l",
    "--label",
    "-m",
    "--milestone",
    "-p",
    "--project",
    "-r",
    "--reviewer",
    "-T",
    "--template",
    "-n",
    "--notes",
    "--notes-file",
    "--target",
    "-X",
    "--method",
    "-f",
    "--raw-field",
    "--field",
    "--header",
    "--input",
    "-q",
    "--jq",
    "--json",
    "--hostname",
    "--add-label",
    "--remove-label",
    "--add-assignee",
    "--remove-assignee",
    "--add-reviewer",
    "--remove-reviewer",
    "--add-project",
    "--remove-project",
    "-s",
    "--subject",
    "--reason",
    "--cache",
    "--preview",
    "-o",
    "--org",
    "--owner",
    "-e",
    "--env",
    "-L",
    "--limit",
    "-S",
    "--search",
    "-A",
    "--author",
    "--state",
}


# Short flags that GH_VALUE_FLAGS reads as taking a value but that are booleans in these
# commands (`gh pr review -a` approves; `gh pr merge -s` squashes), so that what follows them is
# read as the positional argument it is.
GH_BOOLEAN_FLAGS = {
    ("pr", "review"): {"-a", "-r", "-c"},
    ("pr", "merge"): {"-m", "-r", "-s", "-d"},
    ("pr", "comment"): {"-e", "-w"},
    ("issue", "comment"): {"-e", "-w"},
    ("pr", "create"): {"-d", "-e", "-f", "-w"},
    ("issue", "create"): {"-e", "-w"},
    ("pr", "close"): {"-d"},
    ("pr", "edit"): set(),
    ("release", "create"): {"-d", "-p"},
}
# The repository subcommands whose own subcommand (pos[2]) is the verb: `gh repo deploy-key
# list`. For any other `gh repo X`, pos[2] is a name (`gh repo create list` creates "list").
GH_REPO_NESTED = {"deploy-key", "gitignore", "license", "autolink"}


def gh_positionals(args: list[str]) -> list[str]:
    """gh's positional arguments, reading each flag as gh reads it for this command."""
    first = _gh_positionals(args, GH_VALUE_FLAGS)
    booleans = GH_BOOLEAN_FLAGS.get(tuple(first[:2]), set())
    if not booleans:
        return first
    return _gh_positionals(args, GH_VALUE_FLAGS - booleans)


def _gh_positionals(args: list[str], value_flags: set[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out += args[i + 1 :]
            break
        if a.startswith("--") and "=" in a:
            pass
        elif a in value_flags:
            i += 1
        elif (
            a.startswith("-") and len(a) > 2 and a[:2] in value_flags and not a.startswith("--")
        ):
            pass  # -Rowner/repo
        elif not a.startswith("-"):
            out.append(a)
        i += 1
    return out


def flag_pairs(args: list[str], names: set[str]) -> list[tuple[str, str]]:
    """(flag, value) for each value given to one of the flags `names`, in order: `-R x`,
    `--repo x`, `--repo=x`, `-Rx`, `-R=x`."""
    found, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a in names and i + 1 < len(args):
            found.append((a, args[i + 1]))
            i += 2
            continue
        for n in names:
            if n.startswith("--") and a.startswith(n + "="):
                found.append((n, a[len(n) + 1 :]))
            elif not n.startswith("--") and a.startswith(n) and len(a) > len(n):
                found.append((n, a[len(n) :].removeprefix("=")))
        i += 1
    return found


def flag_values(args: list[str], names: set[str]) -> list[str]:
    """Every value given to one of the flags `names`."""
    return [v for _, v in flag_pairs(args, names)]


def implicit_targets(what: str, cwd: str | None, env: dict[str, str]) -> str | None:
    """With no repository named, gh picks one from the checkout's remotes, `upstream` before
    `origin`: every GitHub remote must be under the organization, and one must be."""
    urls = github_remotes(cwd, env)
    targets = [remote_url_target(u) for u in urls]
    github = [t for t in targets if isinstance(t, tuple)]
    for owner, repo in github:
        if not under_org(owner):
            return (
                f"Refused: {what} names no repository, and this checkout has a remote at "
                f"{owner}/{repo}, outside {ORG}/, which gh may choose. Name the repository with "
                f"`--repo {ORG}/<name>`. {INSTEAD}"
            )
    if not github:
        return (
            f"Refused: {what} names no repository and this checkout has no github.com/{ORG}/ "
            f"remote, so its target cannot be known. Name it with `--repo {ORG}/<name>`."
        )
    return None


def check_named(what: str, specs: list[str]) -> str | None:
    for spec in specs:
        slug = slug_of(spec)
        if slug is None:
            return (
                f"Refused: {what} targets {spec.replace(UNKNOWN, '…')!r}, not a github.com "
                f"repository under {ORG}/ that the guard can read. {INSTEAD}"
            )
        if not under_org(slug[0]):
            return refuse_owner(what, *slug)
    return None


def check_owner(what: str, owners: list[str], default: str) -> str | None:
    """A command whose target is an owner (an organization or the account), not a repository."""
    if not owners:
        return f"Refused: {what} writes to {default}, not to {ORG}. {INSTEAD}"
    for o in owners:
        if not under_org(o):
            return f"Refused: {what} writes to {o.replace(UNKNOWN, '…')}, not to {ORG}. {INSTEAD}"
    return None


def gh_is_read(group: str, sub: str, pos: list[str]) -> bool:
    if group in GH_READ_GROUPS:
        return True
    if sub in GH_READ_SUBS or sub in GH_READS.get(group, ()):
        return True
    # `gh repo deploy-key list`, `gh repo gitignore view`, ... and never `gh repo create list`.
    return group == "repo" and sub in GH_REPO_NESTED and len(pos) > 2 and pos[2] in GH_READ_SUBS


def repo_urls_in(args: list[str]) -> list[str]:
    """Every argument, or `--flag=` value, that is a github.com URL, wherever it stands: a URL
    after a flag gh reads as a boolean is still the target."""
    found = []
    for a in args:
        value = a.partition("=")[2] if a.startswith("-") and "=" in a else a
        if REPO_URL.match(value) and len(value.split()) == 1:
            found.append(value)
    return found

def check_gh(args: list[str], env: dict[str, str], cwd: str | None) -> str | None:
    """Why this `gh` call (its arguments after `gh`) is refused, or None to let it run."""
    pos = gh_positionals(args)
    if pos[:2] == ["auth", "token"]:
        return Refusal(
            sensor("worker-upstream-gh-token"),
            "Refused: `gh auth token` prints the worker's GitHub token, and with it any HTTP "
            "client (curl, a script) can post anywhere, past this guard. Use the gh command for "
            f"what you need, with `--repo {ORG}/<name>`; gh reads the token itself. {INSTEAD}",
        )
    return tagged(gh_refusal(args, pos, env, cwd), sensor("worker-upstream-gh"))


def gh_refusal(args: list[str], pos: list[str], env: dict[str, str], cwd: str | None) -> str | None:
    if not pos:
        return None  # `gh`, `gh --version`, `gh --help`
    group = pos[0]
    sub = pos[1] if len(pos) > 1 else ""
    if group == "api":
        return check_gh_api(args, pos, env, cwd)
    if group not in GH_GROUPS:
        return (
            f"Refused: `gh {group.replace(UNKNOWN, '…')}` is not a gh command the upstream guard "
            "knows; an alias or an extension can post anywhere. Spell the built-in gh command "
            f"out, with `--repo {ORG}/<name>`. {INSTEAD}"
        )
    if gh_is_read(group, sub, pos):
        return None
    what = f"`gh {group} {sub}`" if sub else f"`gh {group}`"
    if group in ("alias", "extension"):
        return (
            f"Refused: {what}. An unattended agent sets no gh alias and installs no extension: "
            "either can run any command, past this guard. Spell the gh command out instead."
        )
    if group in GH_NO_REPO:
        return (
            f"Refused: {what} is not a read, and writes to the worker's own GitHub account or gh "
            f"setup, not to a repository under {ORG}/. {INSTEAD}"
        )
    if group == "project":
        return check_owner(what, flag_values(args, {"--owner"}), "the worker's own account")
    if group in ("secret", "variable"):
        if "-u" in args or "--user" in args:
            return check_owner(what, [], "the worker's own account")
        orgs = flag_values(args, {"-o", "--org"})
        if orgs:
            return check_owner(what, orgs, "")
    if group == "repo" and sub == "fork":
        return check_owner(what, flag_values(args, {"--org"}), "a fork in the worker's own account")
    if group == "repo" and sub == "create":
        if len(pos) < 3 or "/" not in pos[2]:
            return check_owner(what, [], "the worker's own account")
        return check_named(what, [pos[2]])
    named = flag_values(args, {"-R", "--repo"})
    named += repo_urls_in(args)
    if group == "repo" and len(pos) > 2 and not REPO_URL.match(pos[2]):
        if "/" not in pos[2]:  # `gh repo edit view`: a repository of the worker's own account
            return check_owner(what, [], f"the repository {pos[2]!r} of the worker's own account")
        named.append(pos[2])  # gh repo edit|delete|archive|sync OWNER/REPO
    if group == "issue" and sub == "transfer" and len(pos) > 3:
        named.append(pos[3])  # the destination
    if not named and env.get("GH_REPO"):
        named.append(env["GH_REPO"])
    if not named:
        return implicit_targets(what, cwd, env)
    return check_named(what, named)


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FIELD_FLAGS = {"-f", "--raw-field", "-F", "--field"}


def graphql_documents(args: list[str], cwd: str | None) -> list[str]:
    """The GraphQL documents a `gh api graphql` call sends: `-f query=...`, `-F query=...`,
    `-F query=@FILE` (read here) and `--input FILE` (its JSON's `query`). Standard input is
    refused: the guard cannot read it without taking it from gh."""
    docs = []
    for flag, value in flag_pairs(args, FIELD_FLAGS | {"--input"}):
        if flag == "--input":
            if value == "-":
                raise Unreadable("`--input -` sends GraphQL from standard input")
            try:
                body = json.loads(read_file(value, cwd))
            except ValueError:
                raise Unreadable(f"{value} is not JSON") from None
            query = body.get("query") if isinstance(body, dict) else None
            if not isinstance(query, str):
                raise Unreadable(f"{value} holds no GraphQL `query`")
            docs.append(query)
            continue
        key, _, val = value.partition("=")
        if key != "query":
            continue
        if flag in ("-F", "--field") and val.startswith("@"):
            if val == "@-":
                raise Unreadable("`-F query=@-` sends GraphQL from standard input")
            docs.append(read_file(val[1:], cwd))
        else:
            docs.append(val)
    if not docs:
        raise Unreadable("no `query` field the guard can read")
    if any(UNKNOWN in d for d in docs):
        raise Unreadable("the query is built by the shell when it runs")
    return docs


def is_mutation(doc: str) -> bool:
    """A document that may write: the word `mutation` (or `subscription`) anywhere in its raw
    text. Comments and strings are not taken out first: a triple quote on each side would hide
    one."""
    return re.search(r"mutation|subscription", doc, re.IGNORECASE) is not None


def check_gh_api(
    args: list[str], pos: list[str], env: dict[str, str], cwd: str | None
) -> str | None:
    methods = [m.upper() for m in flag_values(args, {"-X", "--method"})]
    fields = flag_values(args, FIELD_FLAGS | {"--input"})
    method = methods[-1] if methods else ("POST" if fields else "GET")
    endpoint = pos[1] if len(pos) > 1 else ""
    if endpoint.lstrip("/") == "graphql":
        try:
            docs = graphql_documents(args, cwd)
        except Unreadable as e:
            return (
                f"Refused: `gh api graphql` with GraphQL the upstream guard cannot read ({e}), so "
                f"it cannot tell a query from a mutation. Pass the query inline with "
                f"`-f query='...'`. {INSTEAD}"
            )
        if not any(is_mutation(d) for d in docs):
            return None  # a GraphQL query reads
        return (
            f"Refused: a GraphQL mutation through `gh api graphql`, whose target this guard cannot "
            f"read. Use the gh command for it (`gh pr create --repo {ORG}/<name>`, ...). {INSTEAD}"
        )
    if method not in WRITE_METHODS:
        return None
    what = f"`gh api -X {method} {endpoint}`"
    path = re.sub(r"^https?://api\.github\.com", "", endpoint, flags=re.IGNORECASE).lstrip("/")
    path = path.split("?", 1)[0]
    segments = path.split("/")
    if UNKNOWN in path or "%" in path or "\\" in path or any(s in (".", "..") for s in segments):
        return (
            f"Refused: {what} writes to an endpoint the upstream guard cannot read. Name it as "
            f"`repos/{ORG}/<name>/...`. {INSTEAD}"
        )
    m = re.match(r"^repos/([^/]+)/([^/]+)", path)
    if m:
        owner, repo = m.groups()
        if owner == "{owner}" or repo == "{repo}":
            if env.get("GH_REPO"):
                slug = slug_of(env["GH_REPO"])
                if slug is None or not under_org(slug[0]):
                    return refuse_owner(what, *(slug or (env["GH_REPO"], "")))
                return None
            return implicit_targets(what, cwd, env)
        return None if under_org(owner) else refuse_owner(what, owner, repo)
    m = re.match(r"^orgs/([^/]+)", path)
    if m and under_org(m.group(1)):
        return None
    return (
        f"Refused: {what} writes to an endpoint that is not a repository under {ORG}/ "
        f"(`repos/{ORG}/<name>/...` or `orgs/{ORG}/...`). {INSTEAD}"
    )


# --------------------------------------------------------------------------- git

GIT_GLOBAL_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
    "--super-prefix",
    "--list-cmds",
}
# Of git push's options, these take a separate value. `--signed` and `--force-with-lease` take
# one only as `--signed=...`; `--recurse-submodules` is read on its own.
GIT_PUSH_VALUE = {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
# git's own commands that never push. git runs a builtin before an alias of the same name, so
# no alias is looked up for them.
GIT_BUILTINS = {
    "add",
    "am",
    "apply",
    "bisect",
    "blame",
    "branch",
    "cat-file",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "config",
    "describe",
    "diff",
    "fetch",
    "for-each-ref",
    "grep",
    "init",
    "log",
    "ls-files",
    "ls-remote",
    "ls-tree",
    "merge",
    "merge-base",
    "mv",
    "pull",
    "rebase",
    "reflog",
    "remote",
    "reset",
    "restore",
    "rev-list",
    "rev-parse",
    "revert",
    "rm",
    "shortlog",
    "show",
    "stash",
    "status",
    "switch",
    "symbolic-ref",
    "tag",
    "worktree",
}
GIT_PUSHES = {"push", "send-pack", "http-push"}


def git_subcommand(args: list[str]) -> tuple[list[str], str | None, list[str]]:
    """(global options, subcommand, its arguments) of a git command line."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in GIT_GLOBAL_VALUE:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return args[:i], a, args[i + 1 :]
    return args, None, []


def git_cwd(globals_: list[str], cwd: str | None) -> tuple[str | None, list[str]]:
    """The directory git runs in after its `-C` options, and the other options to pass on."""
    here, rest, i = cwd, [], 0
    while i < len(globals_):
        a = globals_[i]
        if a == "-C" and i + 1 < len(globals_):
            here = str(Path(here or ".") / os.path.expanduser(globals_[i + 1]))
            i += 2
            continue
        if a in GIT_GLOBAL_VALUE and i + 1 < len(globals_):
            rest += [a, globals_[i + 1]]
            i += 2
            continue
        rest.append(a)
        i += 1
    return here, rest


def check_git(args: list[str], env: dict[str, str], cwd: str | None, depth: int = 0) -> str | None:
    """Why this `git` call (its arguments after `git`) is refused, or None to let it run."""
    return tagged(git_refusal(args, env, cwd, depth), sensor("worker-upstream-git-push"))


# The configuration that decides where a push goes: a remote's URLs, and the `url.<base>.
# insteadOf` / `pushInsteadOf` rewrites that turn one URL into another.
REMOTE_URL_KEY = re.compile(r"^remote\.(?P<name>.+)\.(?:url|pushurl)$", re.IGNORECASE)
REWRITE_KEY = re.compile(r"^url\.(?P<base>.+)\.(?:insteadof|pushinsteadof)$", re.IGNORECASE)


def check_config_entry(what: str, key: str, value: str | None) -> str | None:
    """Why setting `key` (to `value`) points a push outside the organization, or None."""
    m = REWRITE_KEY.match(key)
    if m:
        base = m.group("base")
        target = remote_url_target(base)
        if base.endswith("/") and target is None:
            target = remote_url_target(base + "x")  # a prefix: `https://github.com/ikigai-rs/`
        if target == "local" or (isinstance(target, tuple) and under_org(target[0])):
            return None
        return (
            f"Refused: {what} sets `{key}`, a rewrite that sends URLs beginning "
            f"{(value or '…')!r} to {base}, which is not under github.com/{ORG}/. {INSTEAD}"
        )
    m = REMOTE_URL_KEY.match(key)
    if m and value is not None:
        target = remote_url_target(value)
        if target == "local" or (isinstance(target, tuple) and under_org(target[0])):
            return None
        return (
            f"Refused: {what} points the remote `{m.group('name')}` at {value}, which is not "
            f"under github.com/{ORG}/: a push to it would post outside. {INSTEAD}"
        )
    return None


def config_from_options(globals_: list[str], env: dict[str, str]) -> list[tuple[str, str]]:
    """(key, value) of the configuration a command line sets for one run: `-c key=value`,
    `--config-env key=VAR` and GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n."""
    found = []
    for flag, value in flag_pairs(globals_, {"-c", "--config-env"}):
        key, _, val = value.partition("=")
        found.append((key, env.get(val, UNKNOWN) if flag == "--config-env" else val))
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        count = 0
    for n in range(min(count, 100)):
        found.append((env.get(f"GIT_CONFIG_KEY_{n}", ""), env.get(f"GIT_CONFIG_VALUE_{n}", "")))
    return found


def check_git_config(sub: str, rest: list[str]) -> str | None:
    """`git config KEY VALUE` (and `--add`, `set`, `--replace-all`), `git remote add|set-url`."""
    what = f"`git {sub}`"
    if sub == "remote":
        pos = [a for a in rest if not a.startswith("-")]
        if pos[:1] == ["set-url"] and len(pos) >= 3:
            return check_config_entry(what + " set-url", f"remote.{pos[1]}.url", pos[2])
        if pos[:1] == ["add"] and len(pos) >= 3 and "--mirror=push" in rest:
            return check_config_entry(what + " add", f"remote.{pos[1]}.url", pos[2])
        return None
    pos, i = [], 0
    while i < len(rest):
        a = rest[i]
        if a in ("-f", "--file", "--blob", "--type", "--default", "--comment", "--value"):
            i += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    if pos[:1] in (["set"], ["unset"], ["get"], ["list"], ["edit"], ["rename-section"]):
        if pos[0] != "set":
            return None
        pos = pos[1:]
    reads = ("--get", "--unset", "--list", "-l", "--remove-section", "--rename-section", "-e")
    if len(pos) < 2 or any(a.startswith(reads) for a in rest):
        return None  # a read, or a removal: neither points a push anywhere
    return check_config_entry(what, pos[0], pos[1])


def git_refusal(args: list[str], env: dict[str, str], cwd: str | None, depth: int = 0) -> str | None:
    globals_, sub, rest = git_subcommand(args)
    for key, value in config_from_options(globals_, env):
        why = check_config_entry("`git -c`", key, value)
        if why:
            return why
    if sub in ("config", "remote"):
        return check_git_config(sub, rest)
    if sub is None or sub in GIT_BUILTINS:
        return None
    here, opts = git_cwd(globals_, cwd)
    if sub in GIT_PUSHES:
        return check_push(sub, rest, opts, here, env)
    if sub == "subtree":
        pos, i = [], 0
        while i < len(rest):
            if rest[i] in ("-P", "--prefix", "-m", "--message", "--onto", "-b", "--branch"):
                i += 2
                continue
            if not rest[i].startswith("-"):
                pos.append(rest[i])
            i += 1
        if pos[:1] == ["push"]:
            if len(pos) < 2:
                return f"Refused: `git subtree push` names no repository. {INSTEAD}"
            return check_remote("`git subtree push`", pos[1], opts, here, env)
        return None
    # An alias (from config, `-c alias.x=...` or `--config-env`): git runs what it expands to.
    alias = run_git([*opts, "config", "--get", f"alias.{sub}"], here, env)
    if not alias:
        return None
    if depth >= 8:
        return f"Refused: the git alias `{sub}` expands too deeply to read. {INSTEAD}"
    text = alias[0]
    if text.startswith("!"):
        tail = " ".join(shlex.quote(a) for a in rest)
        return check_shell(f"{text[1:]} {tail}", env, here, depth + 1)
    try:
        words = shlex.split(text)
    except ValueError:
        return f"Refused: the git alias `{sub}` cannot be read. {INSTEAD}"
    return git_refusal([*globals_, *words, *rest], env, cwd, depth + 1)


def check_push(
    sub: str, rest: list[str], opts: list[str], here: str | None, env: dict[str, str]
) -> str | None:
    pos, recurse, i = [], None, 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            pos += rest[i + 1 :]
            break
        if a.startswith("--recurse-submodules"):
            if a == "--recurse-submodules" and i + 1 < len(rest):
                recurse, i = rest[i + 1], i + 2
                continue
            recurse = a.partition("=")[2] or "check"
        elif a == "--no-recurse-submodules":
            recurse = "no"
        elif a in GIT_PUSH_VALUE:
            i += 2
            continue
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    what = f"`git {sub}`"
    if sub == "push":
        if recurse is None:
            got = run_git([*opts, "config", "--get", "push.recurseSubmodules"], here, env)
            recurse = got[0] if got else None
        if recurse is not None and recurse.lower() not in ("no", "false", "0", "off"):
            return (
                f"Refused: {what} with --recurse-submodules={recurse} can push each submodule "
                "to its own remote, which the upstream guard does not read. Push each "
                f"repository on its own, to github.com/{ORG}/. {INSTEAD}"
            )
    repo_flag = flag_values(rest, {"--repo"})
    remote = pos[0] if pos else (repo_flag[-1] if repo_flag else None)
    if remote is None:
        if sub != "push":
            return f"Refused: {what} names no repository. {INSTEAD}"
        branch = run_git([*opts, "symbolic-ref", "--short", "-q", "HEAD"], here, env)
        b = branch[0] if branch else ""
        for key in (f"branch.{b}.pushRemote", "remote.pushDefault", f"branch.{b}.remote"):
            got = (
                run_git([*opts, "config", "--get", key], here, env)
                if b or "branch" not in key
                else []
            )
            if got:
                remote = got[0]
                break
        remote = remote or "origin"
    return check_remote(f"`git {sub} {remote}`", remote, opts, here, env)


def check_remote(
    what: str, remote: str, opts: list[str], here: str | None, env: dict[str, str]
) -> str | None:
    if UNKNOWN in remote:
        return f"Refused: {what} goes to a remote the shell names when it runs. {INSTEAD}"
    # A configured rewrite (`url.<base>.pushInsteadOf`) can send any URL elsewhere.
    for line in run_git(
        [*opts, "config", "--get-regexp", r"^url\..*\.(push)?insteadof$"], here, env
    ):
        key, _, value = line.partition(" ")
        why = check_config_entry(what, key, value)
        if why:
            return why
    names = set(run_git([*opts, "remote"], here, env))
    if remote in names:
        urls = run_git([*opts, "remote", "get-url", "--push", "--all", remote], here, env)
    elif "://" in remote or remote.startswith(("/", "./", "../", "~")) or remote == "." or (
        re.match(r"^[^/]+:", remote)
    ):
        urls = run_git([*opts, "ls-remote", "--get-url", remote], here, env) or [remote]
    else:
        # A bare name that is no configured remote: a remote added in the same command line
        # (`git remote add x URL && git push x`), which the guard cannot read yet.
        return (
            f"Refused: {what} names `{remote}`, which is not a configured remote of this "
            f"checkout, so where it goes cannot be read. Push to a configured remote under "
            f"github.com/{ORG}/, in a command of its own. {INSTEAD}"
        )
    if not urls:
        return f"Refused: {what} goes to a remote whose URL cannot be read. {INSTEAD}"
    for url in urls:
        target = remote_url_target(url)
        if target == "local":
            continue
        if target is None:
            return f"Refused: {what} goes to {url}, which is not under github.com/{ORG}/. {INSTEAD}"
        if not under_org(target[0]):
            return refuse_owner(what, *target)
    return None


# --------------------------------------------------------------------------- shell text (the hook)

# A part of a word the shell fills in when it runs ($(...), $1, an unset variable): unknown here.
UNKNOWN = "\x00"
VAR_OPEN, VAR_CLOSE = "\x01", "\x02"


@dataclass
class Command:
    """One simple command: its words (variables still marked), and what it reads on stdin."""

    words: list[str] = field(default_factory=list)
    stdin_texts: list[str] = field(default_factory=list)  # here-documents and here-strings
    piped: bool = False  # its stdin is the previous command's output


class Lexer:
    """Splits shell text into simple commands, in the order they run: the commands of a
    `$(...)`, backticks or `<(...)` come before the command that holds them. Quotes, escapes,
    comments, here-documents (bodies read, and checked when a shell reads them), redirections
    and line continuations are read as the shell reads them. Anything it cannot read (an
    unterminated quote, a here-document with no end) raises Unreadable."""

    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.out: list[Command] = []
        self.heredocs: list[tuple[str, bool, bool, Command]] = []

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < len(self.s) else ""

    def lex(self) -> list[Command]:
        self.parse(None)
        if self.heredocs:
            raise Unreadable("a here-document has no end")
        return self.out

    def parse(self, stop: str | None) -> None:
        """Commands up to the end, or to the `)` that closes a `$(` (stop=")")."""
        cmd = Command()
        word: list[str] | None = None
        redirect: str | None = None  # what the next word is: a target, a delimiter or a string
        depth = 0
        piped_next = False

        def end_word():
            nonlocal word, redirect
            if word is None:
                return
            w = "".join(word)
            word = None
            if redirect is None:
                cmd.words.append(w)
            elif redirect in ("<<", "<<-"):
                raise AssertionError("here-document delimiters are read directly")
            elif redirect == "<<<":
                cmd.stdin_texts.append(w)
            redirect = None

        def end_command(piped: bool = False):
            nonlocal cmd, piped_next
            end_word()
            if redirect is not None:
                raise Unreadable("a redirection names nothing")
            if cmd.words or cmd.stdin_texts:
                cmd.piped = piped_next
                self.out.append(cmd)
            elif piped:
                raise Unreadable("a pipe with no command before it")
            cmd = Command()
            piped_next = piped

        while self.i < len(self.s):
            c = self.s[self.i]
            if stop == ")" and c == ")" and depth == 0:
                self.i += 1
                end_command()
                return
            if c in " \t":
                end_word()
                self.i += 1
            elif c == "\n":
                end_command()
                self.i += 1
                self.read_heredocs()
            elif c == "#" and word is None:
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            elif c == "\\":
                nxt = self.peek(1)
                self.i += 2
                if nxt == "\n":
                    continue
                word = (word or []) + [nxt]
            elif c == "'":
                end = self.s.find("'", self.i + 1)
                if end < 0:
                    raise Unreadable("an unterminated ' quote")
                word = (word or []) + [self.s[self.i + 1 : end]]
                self.i = end + 1
            elif c == '"':
                self.i += 1
                word = (word or []) + [self.read_double()]
            elif c == "$":
                word = (word or []) + [self.read_dollar()]
            elif c == "`":
                word = (word or []) + [self.read_backticks()]
            elif c in "<>" and self.peek(1) == "(" and word is None:
                self.i += 2
                self.parse(")")  # process substitution
                word = [UNKNOWN]
            elif c in "<>" or (c == "&" and self.peek(1) == ">"):
                if word is not None and "".join(word).isdigit():
                    word = None  # a file descriptor: 2>&1
                end_word()
                op = self.read_redirect()
                if op in ("<<", "<<-"):
                    self.read_delimiter(op, cmd)
                elif op in ("<&", ">&") and re.match(r"[0-9-]", self.peek()):
                    while self.i < len(self.s) and re.match(r"[0-9-]", self.s[self.i]):
                        self.i += 1
                else:
                    redirect = op
            elif c in ";&|":
                two = self.s[self.i : self.i + 2]
                if two in ("&&", "||", ";;", "|&"):
                    self.i += 2
                    end_command(piped=two == "|&")
                else:
                    self.i += 1
                    end_command(piped=c == "|")
            elif c == "(":
                self.i += 1
                if stop == ")":
                    depth += 1
                end_command()
            elif c == ")":
                self.i += 1
                depth -= 1
                end_command()
            else:
                word = (word or []) + [c]
                self.i += 1
        if stop == ")":
            raise Unreadable("an unterminated $( or (")
        end_command()

    def read_redirect(self) -> str:
        for op in ("&>>", "&>", "<<<", "<<-", "<<", ">>", ">&", ">|", "<&", "<>", ">", "<"):
            if self.s.startswith(op, self.i):
                self.i += len(op)
                return op
        raise Unreadable("a redirection")  # not reached

    def read_delimiter(self, op: str, cmd: Command) -> None:
        while self.peek() in (" ", "\t"):
            self.i += 1
        m = re.match(r"""(['"]?)([^\s;&|<>()'"]+)\1""", self.s[self.i :])
        if not m:
            raise Unreadable("a here-document with no delimiter the guard can read")
        self.i += m.end()
        delim = m.group(2).replace("\\", "")
        quoted = bool(m.group(1)) or "\\" in m.group(2)
        self.heredocs.append((delim, op == "<<-", quoted, cmd))

    def read_heredocs(self) -> None:
        pending, self.heredocs = self.heredocs, []
        for delim, strip, quoted, cmd in pending:
            lines = []
            while True:
                if self.i >= len(self.s):
                    raise Unreadable(f"the here-document ending {delim} has no end")
                end = self.s.find("\n", self.i)
                end = len(self.s) if end < 0 else end
                line = self.s[self.i : end]
                self.i = end + 1
                if (line.lstrip("\t") if strip else line) == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if not quoted:
                # Its $(...) and backticks run: read them (the text itself is data).
                sub = Lexer(body)
                sub.out = self.out
                sub.read_double(until=None)
                if "$(" in body or "`" in body:
                    body = UNKNOWN + body  # a shell reading it runs what the guard cannot see
            cmd.stdin_texts.append(body)

    def read_double(self, until: str | None = '"') -> str:
        """The text of a double-quoted string (after its opening quote), up to `until`."""
        out = []
        while True:
            if self.i >= len(self.s):
                if until is None:
                    return "".join(out)
                raise Unreadable('an unterminated " quote')
            c = self.s[self.i]
            if until is not None and c == until:
                self.i += 1
                return "".join(out)
            if c == "\\":
                nxt = self.peek(1)
                self.i += 2
                if nxt == "\n":
                    continue
                out.append(nxt if nxt in '$`"\\' else "\\" + nxt)
            elif c == "$":
                out.append(self.read_dollar())
            elif c == "`":
                out.append(self.read_backticks())
            else:
                out.append(c)
                self.i += 1

    def read_dollar(self) -> str:
        """A `$` expansion, at self.i: a variable's marker, or UNKNOWN (its commands read)."""
        nxt = self.peek(1)
        if nxt == "(":
            self.i += 2
            self.parse(")")
            return UNKNOWN
        if nxt == "{":
            depth, j = 0, self.i + 1
            while j < len(self.s):
                if self.s[j] == "{":
                    depth += 1
                elif self.s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            else:
                raise Unreadable("an unterminated ${")
            inner = self.s[self.i + 2 : j]
            self.i = j + 1
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", inner):
                return VAR_OPEN + inner + VAR_CLOSE
            if "$(" in inner or "`" in inner:
                sub = Lexer(inner)
                sub.out = self.out
                sub.read_double(until=None)
            return UNKNOWN
        if nxt == "'":
            end = self.i + 2
            while end < len(self.s) and self.s[end] != "'":
                end += 2 if self.s[end] == "\\" else 1
            if end >= len(self.s):
                raise Unreadable("an unterminated $' quote")
            raw = self.s[self.i + 2 : end]
            self.i = end + 1
            try:
                return raw.encode("latin-1", "backslashreplace").decode("unicode_escape")
            except UnicodeDecodeError:
                return UNKNOWN
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", self.s[self.i + 1 :])
        if m:
            self.i += 1 + m.end()
            return VAR_OPEN + m.group(0) + VAR_CLOSE
        if nxt and nxt in "0123456789@*#?$!-":
            self.i += 2
            return UNKNOWN
        self.i += 1
        return "$"

    def read_backticks(self) -> str:
        j, inner = self.i + 1, []
        while j < len(self.s) and self.s[j] != "`":
            if self.s[j] == "\\" and j + 1 < len(self.s) and self.s[j + 1] in "`\\$":
                inner.append(self.s[j + 1])
                j += 2
                continue
            inner.append(self.s[j])
            j += 1
        if j >= len(self.s):
            raise Unreadable("an unterminated ` quote")
        self.i = j + 1
        self.out += Lexer("".join(inner)).lex()
        return UNKNOWN


def lex(text: str) -> list[Command]:
    return Lexer(text).lex()


def resolve(word: str, env: dict[str, str]) -> str:
    return re.sub(
        f"{VAR_OPEN}([^{VAR_CLOSE}]*){VAR_CLOSE}",
        lambda m: env.get(m.group(1), UNKNOWN),
        word,
    )


SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"}
INTERPRETERS = re.compile(r"^(python[0-9.]*|perl|ruby|node|nodejs|deno|bun|php|lua|osascript)$")
# Code that names gh or git by a full path runs it past the wrappers on PATH.
FULL_PATH_CALL = re.compile(r"(?:^|[^\w.-])(?:~|\.{0,2})?/[^\s'\"]*/(?:gh|git)(?:[\s'\",;)\]]|$)")
RESERVED = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "do",
    "done",
    "while",
    "until",
    "for",
    "select",
    "case",
    "esac",
    "{",
    "}",
    "!",
    "function",
    "coproc",
    "[[",
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")
DECLARE = {"export", "declare", "typeset", "local", "readonly"}

# Commands that run the command after their own options: {name: (options taking a value,
# positional arguments before the command)}.
PREFIXES: dict[str, tuple[set[str], int]] = {
    "command": (set(), 0),
    "builtin": (set(), 0),
    "nohup": (set(), 0),
    "setsid": (set(), 0),
    "nocorrect": (set(), 0),
    "noglob": (set(), 0),
    "chronic": (set(), 0),
    "unbuffer": (set(), 0),
    "exec": ({"-a"}, 0),
    "time": ({"-f", "-o", "--format", "--output"}, 0),
    "sudo": ({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "-R"}, 0),
    "doas": ({"-u", "-C"}, 0),
    "nice": ({"-n", "--adjustment"}, 0),
    "ionice": ({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}, 0),
    "stdbuf": ({"-i", "-o", "-e", "--input", "--output", "--error"}, 0),
    "timeout": ({"-s", "-k", "--signal", "--kill-after"}, 1),
    "gtimeout": ({"-s", "-k", "--signal", "--kill-after"}, 1),
    "caffeinate": ({"-t", "-w"}, 0),
    "flock": ({"-w", "-E", "--timeout", "--conflict-exit-code"}, 1),
    "chrt": ({"-p"}, 1),
    "taskset": (set(), 1),
    "xargs": (
        {"-I", "-i", "-n", "-P", "-L", "-l", "-s", "-d", "-E", "-e", "-a", "--arg-file"},
        0,
    ),
}
# Commands whose option of this name takes a shell command as its value.
COMMAND_OPTION = {"su": "-c", "script": "-c", "flock": "-c"}


def after_options(words: list[str], values: set[str], skip: int) -> list[str] | None:
    """The command `words[0]` runs: what follows its options and `skip` positional arguments."""
    i = 1
    while i < len(words):
        w = words[i]
        if w == "--":
            i += 1
            break
        if not w.startswith("-") or w == "-":
            break
        if w in values:
            i += 2
            continue
        i += 1
    i += skip
    return words[i:] if i < len(words) else None


# How many script files deep the check is. Text the shell builds when it runs ("$cmd", a script
# named by a variable) is refused on the agent's own command line; inside a script file the guard
# has read, it is left to the wrappers on PATH, so that the repository's own scripts run.
_in_file = 0


def dynamic(why: str) -> str | None:
    return None if _in_file else Refusal(sensor("worker-upstream-unreadable"), why)


def check_shell(text: str, env: dict[str, str], cwd: str | None, depth: int = 0) -> str | None:
    """Why a shell command line is refused: the first of its commands this refuses. Text the
    guard cannot read is refused."""
    return tagged(shell_refusal(text, env, cwd, depth), sensor("worker-upstream-unreadable"))


def shell_refusal(text: str, env: dict[str, str], cwd: str | None, depth: int = 0) -> str | None:
    if depth > 8:
        return f"Refused: the command nests shells too deeply for the upstream guard. {INSTEAD}"
    try:
        commands = lex(text)
    except Unreadable as e:
        return (
            f"Refused: the upstream guard cannot read this command ({e}), so it cannot tell "
            f"whether it posts outside {ORG}. Write it so it parses as shell. {INSTEAD}"
        )
    env = dict(env)
    for cmd in commands:
        words = [resolve(w, env) for w in cmd.words]
        local = dict(env)
        while words and (ASSIGNMENT.match(words[0]) or words[0] in RESERVED):
            if ASSIGNMENT.match(words[0]):
                k, _, v = words[0].partition("=")
                local[k.rstrip("+")] = v
            words = words[1:]
        if not words:
            env = local  # bare assignments last for the rest of the line
            continue
        if words[0] in DECLARE:
            for w in words[1:]:
                if ASSIGNMENT.match(w):
                    k, _, v = w.partition("=")
                    env[k] = v
            continue
        if Path(words[0]).name == "cd":
            target = words[1] if len(words) > 1 else env.get("HOME", "~")
            if UNKNOWN not in target and target != "-":
                cwd = str(Path(cwd or ".") / os.path.expanduser(target))
            continue
        why = check_words(words, cmd, local, cwd, depth)
        if why:
            return why
    return None


def check_words(
    words: list[str],
    cmd: Command,
    env: dict[str, str],
    cwd: str | None,
    depth: int,
    opaque: bool = False,
) -> str | None:
    """Why the simple command `words` is refused. `opaque` says more arguments come from
    elsewhere (`xargs`, `find -exec`), so only a gh read and no git push may run."""
    if not words:
        return None
    name = words[0] if words[0] == "." else Path(words[0]).name
    if UNKNOWN in name or VAR_OPEN in name:
        return None  # named when the shell runs: the wrappers on PATH stand there
    if name == "env":
        local, i = dict(env), 1
        while i < len(words):
            w = words[i]
            if w in ("-u", "--unset", "-C", "--chdir") and i + 1 < len(words):
                if w in ("-u", "--unset"):
                    local.pop(words[i + 1], None)
                i += 2
            elif w in ("-S", "--split-string") and i + 1 < len(words):
                return check_shell(
                    " ".join([words[i + 1], *map(shlex.quote, words[i + 2 :])]),
                    local,
                    cwd,
                    depth + 1,
                )
            elif w.startswith("--split-string=") or (w.startswith("-S") and len(w) > 2):
                text = w.partition("=")[2] if w.startswith("--") else w[2:]
                rest = " ".join(map(shlex.quote, words[i + 1 :]))
                return check_shell(f"{text} {rest}", local, cwd, depth + 1)
            elif w in ("-i", "--ignore-environment", "-"):
                local = {k: v for k, v in local.items() if k in ("PATH", "HOME")}
                i += 1
            elif w.startswith("-") and w != "--":
                if w.startswith("-u") and len(w) > 2:
                    local.pop(w[2:], None)
                elif w.startswith("--unset="):
                    local.pop(w.partition("=")[2], None)
                i += 1
            elif w == "--":
                i += 1
            elif ASSIGNMENT.match(w):
                k, _, v = w.partition("=")
                local[k] = v
                i += 1
            else:
                break
        return check_words(words[i:], cmd, local, cwd, depth, opaque)
    if name in ("command", "builtin") and any(w in ("-v", "-V") for w in words[1:2]):
        return None  # a lookup, not a run
    if name in COMMAND_OPTION and COMMAND_OPTION[name] in words:
        j = words.index(COMMAND_OPTION[name])
        if j + 1 < len(words):
            return check_shell(words[j + 1], env, cwd, depth + 1)
    if name in PREFIXES:
        values, skip = PREFIXES[name]
        rest = after_options(words, values, skip)
        return check_words(rest or [], cmd, env, cwd, depth, opaque or name == "xargs")
    if name == "watch":
        rest = after_options(words, {"-n", "--interval", "-d", "--differences", "-q"}, 0)
        return check_shell(" ".join(rest or []), env, cwd, depth + 1)
    if name == "eval":
        text = " ".join(words[1:])
        if UNKNOWN in text or VAR_OPEN in text:
            return dynamic(
                f"Refused: `eval` runs a command the shell builds when it runs, which the "
                f"upstream guard cannot read. Write the command out. {INSTEAD}"
            )
        return check_shell(text, env, cwd, depth + 1)
    if name == "find":
        for j, w in enumerate(words):
            if w in ("-exec", "-execdir", "-ok", "-okdir"):
                end = next(
                    (k for k in range(j + 1, len(words)) if words[k] in (";", "+")), len(words)
                )
                why = check_words(words[j + 1 : end], Command(), env, cwd, depth, True)
                if why:
                    return why
        return None
    if name in SHELLS:
        return check_shell_call(words, cmd, env, cwd, depth)
    if name in ("source", "."):
        if len(words) < 2:
            return None
        return check_script(words[1], env, cwd, depth)
    if INTERPRETERS.match(name):
        for j, w in enumerate(words[1:], 1):
            if w in ("-c", "-e", "-E", "--eval", "-p", "--print") and j + 1 < len(words):
                if FULL_PATH_CALL.search(words[j + 1]):
                    return (
                        f"Refused: `{name}` code that runs gh or git by its full path, past the "
                        f"upstream guard. Run `gh` or `git` by name. {INSTEAD}"
                    )
        return None
    if name == "gh":
        why = check_gh(words[1:], env, cwd)
        if why is None and opaque:
            pos = gh_positionals(words[1:])
            group, sub = (pos + ["", ""])[:2]
            if pos and (group == "api" or not gh_is_read(group, sub, pos)):
                return (
                    f"Refused: `gh {' '.join(pos[:2])}` with arguments the upstream guard "
                    "cannot see (from xargs or find -exec). Run each gh command with its "
                    f"arguments written out. {INSTEAD}"
                )
        return why
    if name == "git" or (name.startswith("git-") and name[4:] in GIT_PUSHES | {"subtree"}):
        args = words[1:] if name == "git" else [name[4:], *words[1:]]
        if opaque:
            _, sub, _ = git_subcommand(args)
            if sub not in GIT_BUILTINS:
                return (
                    f"Refused: `git {sub}` with arguments the upstream guard cannot see (from "
                    f"xargs or find -exec). Run it with its arguments written out. {INSTEAD}"
                )
        return check_git(args, env, cwd, depth)
    return None


def check_shell_call(
    words: list[str], cmd: Command, env: dict[str, str], cwd: str | None, depth: int
) -> str | None:
    """`bash -c TEXT`, `sh script.sh`, `bash <<EOF`, `... | sh`."""
    stdin = False
    for j, w in enumerate(words[1:], 1):
        if w in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            continue
        if words[j - 1] in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            continue
        if w == "--":
            continue
        if w.startswith(("-", "+")) and not w.startswith("--"):
            flags = w[1:]
            if "c" in flags:
                text = next(
                    (x for x in words[j + 1 :] if not x.startswith(("-", "+")) or x == "-"), None
                )
                if text is None:
                    return f"Refused: `{words[0]} -c` with no command. {INSTEAD}"
                if UNKNOWN in text or VAR_OPEN in text:
                    return dynamic(
                        f"Refused: `{Path(words[0]).name} -c` runs a command the shell builds when "
                        f"it runs, which the upstream guard cannot read. Write the command out. "
                        f"{INSTEAD}"
                    )
                return check_shell(text, env, cwd, depth + 1)
            if "s" in flags:
                stdin = True
            continue
        if w.startswith("--"):
            continue
        return check_script(w, env, cwd, depth)  # the first argument is a script file
    # The shell reads its commands from standard input.
    if cmd.stdin_texts:
        for text in cmd.stdin_texts:
            text = resolve(text, env)
            if UNKNOWN in text:
                return dynamic(
                    f"Refused: a here-document for `{Path(words[0]).name}` with $(...) in it, "
                    f"which the upstream guard cannot read. Quote its delimiter. {INSTEAD}"
                )
            why = check_shell(text, env, cwd, depth + 1)
            if why:
                return why
        return None
    if cmd.piped or stdin:
        return dynamic(
            f"Refused: `{Path(words[0]).name}` reads its commands from a pipe, which the upstream "
            f"guard cannot read. Write the commands on the command line, or in a file the shell "
            f"runs. {INSTEAD}"
        )
    return None


def check_script(path: str, env: dict[str, str], cwd: str | None, depth: int) -> str | None:
    if UNKNOWN in path or VAR_OPEN in path:
        return dynamic(
            f"Refused: a shell runs a script the shell names when it runs, which the upstream "
            f"guard cannot read. Name the script. {INSTEAD}"
        )
    try:
        text = read_file(path, cwd)
    except Unreadable as e:
        return dynamic(
            f"Refused: a shell runs {path}, which the upstream guard cannot read first ({e}). "
            f"Write the script in one command and run it in the next. {INSTEAD}"
        )
    global _in_file
    _in_file += 1
    try:
        return check_shell(text, env, cwd, depth + 1)
    finally:
        _in_file -= 1


# --------------------------------------------------------------------------- login shells

PROFILE_BEGIN = "# >>> ikigai upstream guard (tools/upstream_guard.py) >>>"
PROFILE_END = "# <<< ikigai upstream guard <<<"
PROFILE_BLOCK = f"""{PROFILE_BEGIN}
# Written by the ikigai worker (worker/README.md, "The upstream guard"); keep it LAST. For the
# worker's agent only, it puts the guard's gh and git back first on PATH after the lines above
# and the system's (macOS path_helper, Homebrew) put /usr/bin and /opt/homebrew/bin first.
if [ -n "${{{GUARD_ENV}:-}}" ]; then PATH="${GUARD_ENV}:$PATH"; export PATH; fi
{PROFILE_END}
"""
BLOCK_RE = re.compile(re.escape(PROFILE_BEGIN) + r".*?" + re.escape(PROFILE_END) + r"\n?", re.S)


def profile_files(home: Path) -> list[Path]:
    """The startup files a login shell reads last, for sh, bash and zsh: ~/.profile (sh, and
    bash without the next two), the first of ~/.bash_profile and ~/.bash_login that exists, and
    zsh's ~/.zshenv, ~/.zprofile and ~/.zshrc."""
    files = [home / ".profile", home / ".zshenv", home / ".zprofile", home / ".zshrc"]
    for name in (".bash_profile", ".bash_login"):
        if (home / name).exists():
            files.append(home / name)
            break
    return files


def install_profiles(home: Path) -> list[Path]:
    """Put the guard's block at the end of each profile file (moving it there if lines were
    added after it). The files it changed."""
    changed = []
    for f in profile_files(home):
        text = f.read_text() if f.exists() else ""
        body = BLOCK_RE.sub("", text).rstrip("\n")
        new = f"{body}\n\n{PROFILE_BLOCK}" if body else PROFILE_BLOCK
        if new != text:
            f.write_text(new)
            changed.append(f)
    return changed


def check_login_shells(env: dict[str, str], shells=("sh", "bash", "zsh")) -> str | None:
    """Why the agent's shells would run the real gh or git, or None when each finds the
    guard's: `sh`, `bash` and `zsh` (those installed), with `-c` and as login shells (`-lc`)."""
    for shell in shells:
        path = shutil.which(shell, path=env.get("PATH"))
        if path is None:
            continue
        for flag in ("-c", "-lc"):
            try:
                done = subprocess.run(
                    [path, flag, "command -v gh; command -v git"],
                    env=env,
                    cwd=env.get("HOME") or None,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired) as e:
                return f"`{shell} {flag}` did not run ({e})"
            found = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()][-2:]
            for name, line in zip(("gh", "git"), found + [""] * (2 - len(found)), strict=True):
                try:
                    ok = Path(line).name == name and Path(line).resolve().parent == GUARD_BIN
                except OSError:
                    ok = False
                if not ok:
                    return (
                        f"`{shell} {flag} 'command -v {name}'` finds {line or 'nothing'}, not "
                        f"the upstream guard's {GUARD_BIN / name}: a profile file puts another "
                        f"first on PATH after the guard's block. Move the guard's block (between "
                        f"`{PROFILE_BEGIN}` and `{PROFILE_END}`) to the end of that file."
                    )
    return None


# --------------------------------------------------------------------------- entry points


def real_program(name: str, env: dict[str, str]) -> str | None:
    """The first `name` on PATH that is not this guard's wrapper."""
    for d in (env.get("PATH") or os.defpath).split(os.pathsep):
        if not d:
            continue
        try:
            if Path(d).resolve() == GUARD_BIN:
                continue
        except OSError:
            continue
        candidate = Path(d) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def wrapper(name: str, args: list[str]) -> int:
    env = dict(os.environ)
    check = check_gh if name == "gh" else check_git
    try:
        why = check(args, env, os.getcwd())
    except Exception as e:  # noqa: BLE001  (fail closed, saying why)
        why = f"Refused: the upstream guard could not read this `{name}` call ({e}). {INSTEAD}"
    if why:
        fire_refusal(why, os.getcwd(), tool=name)
        print(f"{name}: {why}", file=sys.stderr)
        return WRAPPER_REFUSED
    real = real_program(name, env)
    if real is None:
        print(f"{name}: not found on PATH (after the upstream guard)", file=sys.stderr)
        return 127
    os.execv(real, [real, *args])
    return 0  # not reached


def hook(stdin) -> int:
    """Claude Code PreToolUse: refuse a Bash call this guard refuses (exit 2, the sentence on
    standard error); let every other tool call through."""
    try:
        event = json.load(stdin)
    except (ValueError, OSError):
        print(
            f"Refused: the upstream guard could not read the tool call. {INSTEAD}", file=sys.stderr
        )
        return HOOK_REFUSED
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    try:
        why = check_shell(command, dict(os.environ), event.get("cwd") or os.getcwd())
    except Exception as e:  # noqa: BLE001  (fail closed, saying why)
        why = f"Refused: the upstream guard could not read this command ({e}). {INSTEAD}"
    if why:
        fire_refusal(why, event.get("cwd") or os.getcwd(), "Bash", event.get("session_id"))
        print(why, file=sys.stderr)
        return HOOK_REFUSED
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "hook":
        return hook(sys.stdin)
    if argv and argv[0] in ("gh", "git"):
        return wrapper(argv[0], argv[1:])
    print("usage: upstream_guard.py hook | gh ARGS... | git ARGS...", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
