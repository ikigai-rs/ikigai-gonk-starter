"""The public-tree guard: nothing committed here may name a private machine, a person's home
or a private network.

This repository is public, and some of what is in it (the worker scripts) was copied from a
private one. The guard is what keeps the next copy honest. It fails on any tracked file that
holds:

- a path under a home directory (macOS or Linux) whose account is not one of the design's
  (`agent`, `hubdiag`), the CI fixtures' (`fixadmin`), macOS's shared directory, or a
  placeholder (`<name>`, `$var`, `{var}`, an ellipsis);
- a host name on the mDNS `.local` domain (a dotted directory such as `~/.local/bin`, an
  identifier, or a file name such as `settings.local.json` is not one);
- an IPv4 address other than loopback (127.0.0.0/8) and the unspecified 0.0.0.0, which
  are generic;
- a denylisted word: the names of the project's own machines. They are compared by SHA-256,
  so that this file does not carry them either.

To deny another word, add the hex SHA-256 of its lowercase form to DENIED_WORD_HASHES. To
allow another account's home, add it to ALLOWED_HOMES with the reason.

The patterns below are written so that they do not match their own source, and the examples
in the self-test are assembled at run time for the same reason: this file is scanned too.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import unittest

from support import ROOT

ALLOWED_HOMES = {
    "agent",  # the worker's account: the design (worker/README.md)
    "hubdiag",  # the hub's read-only account: the design
    "fixadmin",  # the admin the CI boundary fixtures create on a throwaway runner
    "Shared",  # macOS's shared directory, where the hub's views live
}
PLACEHOLDER_START = ("$", "<", "{", "…")

DENIED_WORD_HASHES = {
    "0c4ac837abc0830c9da02f051ec60711ac97d5b1aa55b181e6416779a182614f",
    "f1ad6ee4db08bff75f77d1d65b99d273cea298f69ebd45c7fd172f0892554142",
}

# The account name: a placeholder's opening character (`$`, `<`, `{`, an ellipsis) may only
# START it. With `<` anywhere in the class, `/Users/agent</string>` in a plist read as the
# account `agent<` and failed.
HOME_PATH = re.compile(r"/(?:Users|home)/([$<{\u2026]?[\w-]*)")
MDNS_HOST = re.compile(r"[A-Za-z0-9-]\.local(?![\w/-]|\.\w)")
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?!\w|\.\d)")
WORD = re.compile(r"[a-z0-9]+")


def home_paths(text: str) -> list[str]:
    bad = []
    for m in HOME_PATH.finditer(text):
        name = m.group(1)
        if name == "" or name in ALLOWED_HOMES or name.startswith(PLACEHOLDER_START):
            continue
        bad.append(m.group(0))
    return bad


def mdns_hosts(text: str) -> list[str]:
    return [m.group(0) for m in MDNS_HOST.finditer(text)]


def ipv4_literals(text: str) -> list[str]:
    bad = []
    for m in IPV4.finditer(text):
        octets = [int(o) for o in m.group(1).split(".")]
        if any(o > 255 for o in octets):
            continue  # not an address (a version string, a date)
        if octets[0] == 127 or octets == [0, 0, 0, 0]:
            continue
        bad.append(m.group(1))
    return bad


def denied_words(text: str) -> list[str]:
    words = set(WORD.findall(text.lower()))
    return sorted(w for w in words if hashlib.sha256(w.encode()).hexdigest() in DENIED_WORD_HASHES)


def problems(text: str) -> list[str]:
    found = [f"a home path {p!r}" for p in home_paths(text)]
    found += [f"an mDNS host name {h!r}" for h in mdns_hosts(text)]
    found += [f"an IPv4 address {a!r}" for a in ipv4_literals(text)]
    found += [f"a denylisted word {w!r}" for w in denied_words(text)]
    return found


def tracked_files() -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.decode().split("\0") if p]


class TreeGuard(unittest.TestCase):
    def test_no_tracked_file_names_a_private_machine_home_or_network(self):
        files = tracked_files()
        if files is None:
            self.skipTest("not a git working tree: the guard scans tracked files")
        self.assertGreater(len(files), 10, "git ls-files listed almost nothing")
        failures = []
        for rel in files:
            path = ROOT / rel
            if not path.is_file():
                continue  # deleted in the working tree, or a link to nothing
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # binary
            failures += [f"{rel}: {p}" for p in problems(text)]
        self.assertEqual(failures, [], "\n" + "\n".join(failures))


class TheGuardItself(unittest.TestCase):
    """Each rule, red and green, on text assembled here so this file stays clean."""

    def test_home_paths(self):
        users, home = "/" + "Users/", "/" + "home/"
        self.assertEqual(home_paths(users + "someone/src"), [users + "someone"])
        self.assertEqual(home_paths(home + "someone/.bash_history"), [home + "someone"])
        for fine in [
            "Shared/hubdiag",
            "agent/work",
            "hubdiag/.ssh",
            "fixadmin/src",
            "$u",
            "<admin>",
            "{user}/x",
            "\u2026/x",
            "agent</string>",  # a home at the end of an XML element (the worker's plist)
            "agent<",
        ]:
            self.assertEqual(home_paths(users + fine), [], fine)
            self.assertEqual(home_paths(home + fine), [], fine)
        # Markup after a name that is not the design's is still that name, and still caught.
        self.assertEqual(home_paths(users + "someone</string>"), [users + "someone"])
        self.assertEqual(home_paths(f'"{home}someone"'), [home + "someone"])

    def test_mdns_host_names(self):
        local = "." + "local"
        self.assertEqual(mdns_hosts("ssh worker" + local), ["r" + local])
        self.assertEqual(mdns_hosts("ssh worker" + local + ". Then"), ["r" + local])
        self.assertEqual(mdns_hosts("http://box" + local + ":8080/"), ["x" + local])
        for fine in [
            "~/" + local + "/bin",
            "$h/" + local + "/bin",
            " " + local + "/share",
            "settings" + local + ".json",
            "cfg" + local + "_toml",
            "localhost",
        ]:
            self.assertEqual(mdns_hosts(fine), [], fine)

    def test_ipv4_literals(self):
        lan = "192" + ".168.1.20"
        self.assertEqual(ipv4_literals(f"ssh {lan}"), [lan])
        self.assertEqual(ipv4_literals(f"addr={lan}."), [lan])
        loopback, anywhere = "127" + ".0.0.1", "0" + ".0.0.0"
        self.assertEqual(ipv4_literals(f"http://{loopback}:8080 bind {anywhere}"), [])
        self.assertEqual(ipv4_literals("version 1" + ".2.3"), [])
        self.assertEqual(ipv4_literals("not 999" + ".1.1.1"), [])

    def test_denied_words_are_compared_by_hash(self):
        self.assertEqual(denied_words("nothing private here"), [])
        word = "gonk"
        digest = hashlib.sha256(word.encode()).hexdigest()
        DENIED_WORD_HASHES.add(digest)
        try:
            self.assertEqual(denied_words(f"the {word.upper()} host"), [word])
        finally:
            DENIED_WORD_HASHES.discard(digest)


if __name__ == "__main__":
    unittest.main()
