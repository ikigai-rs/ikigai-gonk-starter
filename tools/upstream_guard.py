#!/usr/bin/env python3
"""The worker's upstream guard: an unattended agent posts only to GitHub projects under
ikigai-rs, mechanically. Ledger item 4449.

A line in the brief was not enough: twice a worker opened an issue on a project outside
ikigai-rs (rudof-project/rudof#963 on 2026-11-22, oxigraph/oxigraph#2050 on 2026-11-26). This
module refuses such a post before it leaves the machine, in two places the worker sets up for
the agent it runs (tools/worker.py, `guard_env` and `with_guard`):

- **`gh` and `git` wrappers first on PATH** (`worker/guard/bin/`), for every agent: Claude
  Code, Codex, Hermes or a command of one's own. Each wrapper runs `main(["gh", ...])` or
  `main(["git", ...])` here, which refuses (exit 3, the sentence on standard error) or execs
  the real program, the next one of that name on PATH.
- **A Claude Code PreToolUse hook** (`main(["hook"])`), which reads each `Bash` command's text
  and refuses the same calls however the program is named (`/opt/homebrew/bin/gh`, `env gh`,
  `bash -c "gh ..."`), exit 2 with the sentence.

What is refused:

- a `gh` write (`issue create|comment|edit|close|reopen|...`, `pr create|comment|review|edit|
  close|merge|...`, `release create|edit|...`, any `discussion`, and `gh api` with a write
  method or fields) whose repository is not under ikigai-rs, however it is named: `-R`,
  `--repo`, `GH_REPO`, a URL argument, a `repos/OWNER/REPO/...` path, or, when none is named,
  the checkout's GitHub remotes (all of them, since gh may pick `upstream` over `origin`);
- `git push` to a remote whose URL is not under github.com/ikigai-rs/. A push to a local path
  or `file://` URL is not a post and goes through (the tests of this repository do it).

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
import subprocess
import sys
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

# The gh commands that write, by group. A group in ALL_WRITE writes whatever its subcommand.
GH_WRITES = {
    "issue": {
        "create",
        "comment",
        "edit",
        "close",
        "reopen",
        "delete",
        "transfer",
        "lock",
        "unlock",
        "pin",
        "unpin",
        "develop",
        "new",
    },
    "pr": {
        "create",
        "comment",
        "review",
        "edit",
        "close",
        "merge",
        "reopen",
        "ready",
        "lock",
        "unlock",
        "new",
    },
    "release": {"create", "edit", "delete", "upload", "delete-asset", "new"},
    "label": {"create", "edit", "delete", "clone"},
}
GH_ALL_WRITE = {"discussion"}

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
}

REPO_URL = re.compile(
    r"^(?:https?://)?(?:[^@/]+@)?(?P<host>github\.com|www\.github\.com)/(?P<owner>[^/]+)/(?P<repo>[^/#?]+)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- repositories


def under_org(owner: str) -> bool:
    return owner.lower() == ORG


def slug_of(spec: str) -> tuple[str, str] | None:
    """(owner, repo) of a gh repository spec: `OWNER/REPO`, `HOST/OWNER/REPO` (github.com only)
    or a github.com URL; None when it is none of these (another host, or not a repository)."""
    spec = spec.strip()
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
    if not url:
        return None
    if url.startswith("file://") or url.startswith(("/", "./", "../", "~")) or url == ".":
        return "local"
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
            [git, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=20
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


# --------------------------------------------------------------------------- gh


def gh_positionals(args: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out += args[i + 1 :]
            break
        if a.startswith("--") and "=" in a:
            pass
        elif a in GH_VALUE_FLAGS:
            i += 1
        elif (
            a.startswith("-") and len(a) > 2 and a[:2] in GH_VALUE_FLAGS and not a.startswith("--")
        ):
            pass  # -Rowner/repo
        elif not a.startswith("-"):
            out.append(a)
        i += 1
    return out


def flag_values(args: list[str], names: set[str]) -> list[str]:
    """Every value given to one of the flags `names` (`-R x`, `--repo x`, `--repo=x`, `-Rx`)."""
    found, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a in names and i + 1 < len(args):
            found.append(args[i + 1])
            i += 2
            continue
        for n in names:
            if n.startswith("--") and a.startswith(n + "="):
                found.append(a[len(n) + 1 :])
            elif not n.startswith("--") and a.startswith(n) and len(a) > len(n):
                found.append(a[len(n) :])
        i += 1
    return found


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


def check_gh(args: list[str], env: dict[str, str], cwd: str | None) -> str | None:
    """Why this `gh` call (its arguments after `gh`) is refused, or None to let it run."""
    pos = gh_positionals(args)
    if not pos:
        return None
    group = pos[0]
    if group == "api":
        return check_gh_api(args, pos, env, cwd)
    sub = pos[1] if len(pos) > 1 else ""
    if group not in GH_ALL_WRITE and sub not in GH_WRITES.get(group, ()):
        return None
    what = f"`gh {group} {sub}`".replace(" `", "`") if sub else f"`gh {group}`"
    named = flag_values(args, {"-R", "--repo"})
    named += [a for a in pos[2:] if REPO_URL.match(a)]
    if not named and env.get("GH_REPO"):
        named.append(env["GH_REPO"])
    if not named:
        return implicit_targets(what, cwd, env)
    for spec in named:
        slug = slug_of(spec)
        if slug is None:
            return (
                f"Refused: {what} targets {spec!r}, not a github.com repository under {ORG}/. "
                + INSTEAD
            )
        if not under_org(slug[0]):
            return refuse_owner(what, *slug)
    return None


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def check_gh_api(
    args: list[str], pos: list[str], env: dict[str, str], cwd: str | None
) -> str | None:
    methods = [m.upper() for m in flag_values(args, {"-X", "--method"})]
    fields = flag_values(args, {"-f", "-F", "--field", "--raw-field", "--input"})
    method = methods[-1] if methods else ("POST" if fields else "GET")
    if method not in WRITE_METHODS:
        return None
    endpoint = pos[1] if len(pos) > 1 else ""
    what = f"`gh api -X {method} {endpoint}`"
    if endpoint.lstrip("/") == "graphql":
        body = " ".join(fields)
        if not re.search(r"\bmutation\b", body):
            return None  # a graphql query reads
        return (
            f"Refused: a GraphQL mutation through `gh api graphql`, whose target this guard cannot "
            f"read. Use the gh command for it (`gh pr create --repo {ORG}/<name>`, ...). {INSTEAD}"
        )
    path = re.sub(r"^https?://api\.github\.com", "", endpoint).lstrip("/")
    m = re.match(r"^repos/([^/]+)/([^/?]+)", path)
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
    m = re.match(r"^orgs/([^/?]+)", path)
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
}
GIT_PUSH_VALUE = {"--repo", "--receive-pack", "--exec", "-o", "--push-option", "--signed"}


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
            here = str(Path(here or ".") / globals_[i + 1])
            i += 2
            continue
        if a in GIT_GLOBAL_VALUE and i + 1 < len(globals_):
            rest += [a, globals_[i + 1]]
            i += 2
            continue
        rest.append(a)
        i += 1
    return here, rest


def check_git(args: list[str], env: dict[str, str], cwd: str | None) -> str | None:
    """Why this `git` call (its arguments after `git`) is refused, or None to let it run."""
    globals_, sub, rest = git_subcommand(args)
    if sub != "push":
        return None
    here, opts = git_cwd(globals_, cwd)
    repo_flag = flag_values(rest, {"--repo"})
    pos, i = [], 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            pos += rest[i + 1 :]
            break
        if a in GIT_PUSH_VALUE:
            i += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    remote = pos[0] if pos else (repo_flag[-1] if repo_flag else None)
    if remote is None:
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
    names = set(run_git([*opts, "remote"], here, env))
    if remote in names:
        urls = run_git([*opts, "remote", "get-url", "--push", "--all", remote], here, env)
    else:
        urls = run_git([*opts, "ls-remote", "--get-url", remote], here, env) or [remote]
    if not urls:
        return f"Refused: `git push {remote}` goes to a remote whose URL cannot be read. {INSTEAD}"
    for url in urls:
        target = remote_url_target(url)
        if target == "local":
            continue
        if target is None:
            return (
                f"Refused: `git push {remote}` goes to {url}, which is not under "
                f"github.com/{ORG}/. {INSTEAD}"
            )
        if not under_org(target[0]):
            return refuse_owner(f"`git push {remote}`", *target)
    return None


# --------------------------------------------------------------------------- shell text (the hook)

SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
PREFIXES = {"env", "command", "exec", "nohup", "time", "sudo", "xargs", "builtin"}
SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&", ";;"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def simple_commands(text: str) -> list[list[str]]:
    """The simple commands of a shell command line, split at `;`, `&&`, `||`, `|`, `&`, new
    lines and parentheses. A line shlex cannot read is split on whitespace."""
    lexer = shlex.shlex(text.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = text.split()
    out, cur = [], []
    for t in tokens:
        if t in SEPARATORS or (t and set(t) <= set(";&|()")):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def check_shell(text: str, env: dict[str, str], cwd: str | None, depth: int = 0) -> str | None:
    """Why a shell command line is refused: the first of its gh or git calls this refuses."""
    for words in simple_commands(text):
        local = dict(env)
        while words and (
            ASSIGNMENT.match(words[0]) or words[0] in PREFIXES or words[0].startswith("-")
        ):
            if ASSIGNMENT.match(words[0]):
                k, _, v = words[0].partition("=")
                local[k] = v
            words = words[1:]
        if not words:
            continue
        name = Path(words[0]).name
        if name == "cd" and len(words) > 1:
            cwd = str(Path(cwd or ".") / os.path.expanduser(words[1]))
            continue
        why = None
        if name in SHELLS and depth < 3:
            for j, w in enumerate(words[1:], 1):
                if w.startswith("-") and "c" in w.lstrip("-") and j + 1 < len(words):
                    why = check_shell(words[j + 1], local, cwd, depth + 1)
                    break
        elif name == "gh":
            why = check_gh(words[1:], local, cwd)
        elif name == "git":
            why = check_git(words[1:], local, cwd)
        if why:
            return why
    return None


# --------------------------------------------------------------------------- entry points

GUARD_BIN = Path(__file__).resolve().parent.parent / "worker" / "guard" / "bin"


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
