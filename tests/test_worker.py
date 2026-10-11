"""The worker-machine scripts' hermetic tests (worker/tests/*-test.sh), run as part of the gates.

They need no root and change nothing on the machine: probe-test.sh makes the current user the
outsider with mode bits in a scratch directory, and setup-test.sh runs worker-setup.sh's dry run,
which only reads. The end-to-end tests (worker/tests/ci-*.sh) create accounts and edit sshd, so
they run only on throwaway CI runners, in their own jobs.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import unittest

from support import ROOT

TESTS = ROOT / "worker" / "tests"


def run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(TESTS / script)], capture_output=True, text=True, timeout=300
    )


@unittest.skipIf(platform.system() not in ("Linux", "Darwin"), "the worker is Linux or macOS")
@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "these run as a normal user")
class WorkerScripts(unittest.TestCase):
    def check(self, script: str):
        r = run(script)
        self.assertEqual(r.returncode, 0, f"{script} failed:\n{r.stdout}\n{r.stderr}")
        self.assertIn(" 0 failed", r.stdout)

    def test_the_probe_red_and_green(self):
        self.check("probe-test.sh")

    def test_the_setup_dry_run_and_its_refusals(self):
        if shutil.which("ssh-keygen") is None:
            self.skipTest("no ssh-keygen: the test makes a throwaway key with it")
        if platform.system() == "Linux" and not os.path.isdir("/etc/ssh/sshd_config.d"):
            # The setup refuses, correctly, on a Linux machine without OpenSSH's server; the
            # end-to-end job installs it and runs the dry run there.
            self.skipTest("no /etc/ssh/sshd_config.d: OpenSSH server is not installed")
        self.check("setup-test.sh")


if __name__ == "__main__":
    unittest.main()
