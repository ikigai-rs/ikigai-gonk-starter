# Working on Windows

**Use WSL2.** Run the agent, `git`, `python3`, `ikigai` and `just` inside a WSL2 Linux
distribution, on a clone in the Linux filesystem, and everything in this repository works as
it does on Linux. Native Windows is not supported yet: the `ikigai` client that
`tools/gonk.py` calls has no Windows build, and its local-socket transport is Unix-only.

This page says what is verified and what is not, with the source for each, checked on
2026-10-10. "Verified" means the tool's own documentation says so, or a test here proves it;
it never means someone ran it on Windows. Nobody has run this starter on a Windows machine yet.

## Two traps any Windows checkout hits, and what this repository does

Both come from Git for Windows, whose defaults differ from Linux and macOS git. Both are
handled here, so a clone works whichever git made it, but a WSL2 clone made with Linux git
never meets them at all.

**Symlinks.** Unless `core.symlinks` is on (it needs Developer Mode or Administrator rights),
Git for Windows checks a committed symlink out as a small text file holding the target's
NAME. A `CLAUDE.md` linked to `AGENTS.md` becomes a file whose whole content is
`AGENTS.md`, and because a `CLAUDE.md` exists, Claude Code reads it INSTEAD of `AGENTS.md`:
the agent gets nine bytes in place of the rules, and nothing says so. Reproduce it anywhere
with `git -c core.symlinks=false clone <repo>`.

So `CLAUDE.md` here is not a link. It is one line, `@AGENTS.md`, an import that Claude Code
expands at launch, and the one Claude Code's own documentation recommends when anyone who
clones the repository works on Windows
([memory: share one file with other coding tools](https://code.claude.com/docs/en/memory)).
The tree holds no symlinks at all (`tests/test_shape.py` fails on one), and a generated
repository carries the same file. The other agents never read it: Codex reads `AGENTS.md`;
Hermes Agent and pi take the FIRST context file they find, and both rank `AGENTS.md` above
`CLAUDE.md` (the Hermes context-files documentation; pi's `loadContextFileFromDir` in
`packages/coding-agent/src/core/resource-loader.ts`).

**Line endings.** Git for Windows installs with `core.autocrlf=true`, which checks every text
file out with CRLF. A shell script with CRLF does not run (`bad interpreter: /bin/bash^M`, or
`env: bash\r: No such file or directory` through `/usr/bin/env`), and a file compared byte for
byte, such as the spec vectors in `openspec/vectors.json`, differs. Reproduce it with
`git -c core.autocrlf=true clone <repo>`.

So `.gitattributes` sets `eol=lf` for every text file, which overrides `core.autocrlf`: the
repository and every checkout are LF. A generated repository carries the same file.
`tests/test_windows.py` clones this tree, and a generated one, the way Git for Windows does
(`core.autocrlf=true`, `core.symlinks=false`) and fails on any CRLF or symlink; it also
clones once WITHOUT `.gitattributes` and expects CRLF, so the check is known to see it.

Editing from the Windows side can still write CRLF into the working tree (an editor that
saves CRLF, or a file copied in through `\\wsl$`). `git diff` shows it, and the next commit
normalizes it to LF, but a script edited that way fails until then.

## WSL2: the setup

All of this is from Microsoft's
[WSL configuration reference](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)
unless it says otherwise.

1. **Install WSL2 and a distribution**: `wsl --install` from an Administrator PowerShell
   (Ubuntu by default). WSL1 is not enough: Codex dropped it in 0.115, and Claude Code's
   sandboxing needs WSL2.
2. **Turn on systemd** if the machine will run anything as a service (a gonk, or the worker
   loop as a user unit). In the distribution's `/etc/wsl.conf`:

   ```ini
   [boot]
   systemd=true
   ```

   then `wsl --shutdown` from PowerShell and start the distribution again. Configuration is
   read only when the VM starts; Microsoft calls waiting for it to stop "the 8 second rule".
   The `[boot]` section needs Windows 11 or Server 2022.
3. **Clone in the Linux filesystem**, under your Linux home (`~/src/<repo>`), with the `git`
   inside WSL. Not under `/mnt/c`: Windows-mounted paths are slower, Linux permissions do not
   map onto them without the `metadata` mount option, and a clone there is reachable by Windows
   tools that bring both traps above back. OpenAI's
   [Codex on WSL](https://learn.chatgpt.com/docs/windows/wsl) page gives the same advice
   ("faster I/O and fewer symlink and permission issues").
4. **Install the tools inside WSL**: Python 3.11 or later, `git`, `gh`, `just`, the `ikigai`
   client, and the agent itself with its Linux installer.

### Reaching gonk, and being reached

- **gonk on the same machine**, inside the same distribution: the local socket works as on
  Linux (`init --socket <path>`).
- **gonk on another machine**: `init --quic quic://<host>:<port> --cert-dir <bundle>`.
  Outbound connections from WSL2's default NAT network are expected to work; this has not
  been tested from WSL.
- **Anything that must accept connections from the LAN** (a gonk others use, or a worker the
  hub reaches over ssh) is behind WSL2's NAT by default. `networkingMode=mirrored` in
  `%UserProfile%\.wslconfig` under `[wsl2]` mirrors Windows' interfaces into Linux; it needs
  Windows 11 22H2 or later. Without it, a `netsh interface portproxy` rule on Windows is the
  usual fallback (not documented on the page above, and not tested here).

### Staying up

WSL stops a distribution when it is idle and the VM after that, and Windows sleep stops
everything. For a machine that serves or works unattended, `.wslconfig`:

```ini
[general]
instanceIdleTimeout=-1   # documented: -1 disables a distribution's idle shutdown
```

The `[wsl2]` key `vmIdleTimeout` (Windows 11 only) sets the VM's idle timeout in
milliseconds; the documentation does not say what `-1` does, so that is UNKNOWN. Keep Windows
itself from sleeping in its power settings.

### `/mnt/c` exposes the Windows user to every Linux account

WSL mounts the Windows drives under `/mnt/` by default (`[automount] enabled=true`), and
Linux processes launch Windows programs (`[interop] enabled=true`). Both reach Windows as the
Windows user who started WSL, and without the `metadata` mount option the drives' Linux
permissions are not Windows' own, so expect every account in the distribution to have that
reach, not only yours (expected from the defaults documented above; not tested here). On a personal
machine that is the point. On a worker, where an agent runs unattended under its own account,
it is a hole in the boundary that `worker/worker-setup.sh` builds on Linux and macOS: the
agent's account could read the Windows profile. `[automount] enabled=false` and
`[interop] enabled=false` in `/etc/wsl.conf` turn both off for the whole distribution
(documented keys; that this closes the hole has not been tested).
**The worker setup and its `--check` do not handle WSL yet**; until they do, do not run an
unattended worker in WSL. See the starter's
[worker/README.md](https://github.com/ikigai-rs/ikigai-gonk-starter/blob/main/worker/README.md).

## Each agent on Windows

| agent | native Windows | WSL2 | instruction file |
| --- | --- | --- | --- |
| Claude Code | VERIFIED supported (Windows 10 1809+). Its Bash tool needs Git for Windows (Git Bash); without it, a PowerShell tool runs commands. Sandboxing is not supported natively ([setup](https://code.claude.com/docs/en/setup)) | VERIFIED supported, with sandboxing; WSL1 works without it ([setup](https://code.claude.com/docs/en/setup)) | `CLAUDE.md`, which imports `AGENTS.md` |
| Codex | VERIFIED supported in PowerShell, with a Windows sandbox (`mxc`, `elevated` or `unelevated`) ([Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)) | VERIFIED supported, with the Linux sandbox (bubblewrap); WSL1 unsupported since 0.115 ([Codex on WSL](https://learn.chatgpt.com/docs/windows/wsl)) | `AGENTS.md` |
| Hermes Agent | VERIFIED supported on Windows 10 and 11 by a PowerShell installer; its terminal tool runs commands through Git Bash ([Windows native](https://hermes-agent.nousresearch.com/docs/user-guide/windows-native)). Sandboxing: UNKNOWN, the page does not say | VERIFIED, as a Linux install ([Windows WSL2](https://hermes-agent.nousresearch.com/docs/user-guide/windows-wsl-quickstart)) | `AGENTS.md` (ranked above `CLAUDE.md`, first match wins) |
| pi | UNKNOWN: its documentation does not mention Windows | UNKNOWN, expected to behave as on Linux | `AGENTS.md` (ranked above `CLAUDE.md` in its source) |

What is UNKNOWN for every agent: whether it works with this starter on native Windows at all,
since the `ikigai` client `tools/gonk.py` drives is not built for Windows. An agent there can
read the rules and edit files, but cannot claim, report or close.

Two more things to know before choosing native Windows for an agent, once that changes:

- **The optional Claude Code hook** (`hooks/claude/settings.sample.json`) runs
  `python3 "$CLAUDE_PROJECT_DIR/…"`. On Windows `python3` may not be on PATH (the Python
  launcher is `py`), and `$CLAUDE_PROJECT_DIR` is shell syntax. UNKNOWN whether it runs; not
  tested.
- **The Codex desktop app** recommends keeping projects on the Windows drive and reaching
  them from WSL through `/mnt/<drive>/`
  ([Codex app on Windows](https://learn.chatgpt.com/docs/windows/windows-app)), the opposite
  of step 3 above. That advice is for its Windows-native agent. Pick one world per clone: the
  agent, `git` and the tools all in WSL on a Linux-filesystem clone, or all on Windows, never
  a Windows agent editing a WSL clone or the reverse.
