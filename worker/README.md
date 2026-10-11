# worker: machines that do agentic work for a hub

A **worker** is a machine a hub dispatches work to: it claims a ledger item, branches a fresh
worktree, runs an agent, pushes a branch and opens a pull request (the worker loop, which is
coming to this repository; see "Coming next" in the [top-level README](../README.md)). This
directory turns setting one up into a mechanism instead of someone's memory: two scripts the
worker's admin runs on the worker, and two interfaces the hub reads over ssh.

| on the worker, by its admin | on the hub |
| --- | --- |
| `sudo ./worker-setup.sh --key hub.pub` sets up, idempotent; `--dry-run` first | an ssh `Host` stanza that logs in as `hubdiag` with the hub's key |
| `sudo ./worker-setup.sh --check` is the boundary self-test, and writes the facts the hub reads | [`probe.sh`](probe.sh) piped over that ssh, read-only, and the facts file read with `cat` (below) |

The hub's own tooling (an inventory of its machines, the tunnel units, one command that wraps
both reads) is the hub's to build on those two interfaces; ikigai's hub keeps its version in
its private tooling repository and reads the same probe output and the same facts format.

## Three accounts, and who can reach what

| account | is | holds | may read | may write |
| --- | --- | --- | --- | --- |
| the admin | the person who owns the machine | their keys, their checkouts, their history | everything of theirs | their own tree |
| `agent` | the worker (by design, it never works in a person's checkouts) | the fine-grained GitHub token; a worktree per item under `~agent/work` | its own home | its own home |
| `hubdiag` | the hub's eyes | nothing | the journal; the **views** (by default `~agent/work`) | nothing |

Neither `agent` nor `hubdiag` has sudo or a password. `hubdiag` logs in by key only, and sshd lets it
open one remote forward (gonk's tunnel, port 1060) and nothing else; `agent` cannot log in over ssh
at all (`DenyUsers`): its admin reaches it with `sudo -iu agent`.

**A view is never a grant of traverse on a home.** That is the mistake this directory exists to
not repeat. The first worker's setup, done by hand, ran `setfacl -m u:hubdiag:--x ~` so hubdiag
could reach a repository tree in the admin's home. Pop!_OS closes homes to others, so the files
inside are created world-readable, and traverse made every file with a GUESSABLE name readable:
`~/.bash_history` first. The fix, and the design:

- **Linux:** a read-only **bind mount** of each view at `/srv/hubdiag/<name>` (`/etc/fstab`,
  `bind,ro`). Path lookup never passes through the home, so the home stays closed. `/srv/hubdiag`
  is `root:hubdiag 0750`, so no other local account reaches the views either.
- **macOS has no bind mount.** The view is a **copy** under `/Users/Shared/hubdiag/<name>`,
  refreshed every five minutes by a LaunchDaemon (`dev.ikigai-rs.hubdiag-view-<name>`) running as
  the source's owner: `rsync` into a directory owned by that account with group `hubdiag`, mode
  0750, every write bit but the owner's and every "other" bit dropped. Up to five minutes stale;
  `.hubdiag-view-stamp` says when, and the probe reports it. Rejected, and why:
  - an **APFS snapshot** (`tmutil localsnapshot` + `mount_apfs -s`) is read-only but keeps the
    live volume's permissions, so the home is just as closed inside it; mounting it with
    `-o noowners` would open every home, `.bash_history` included;
  - **bindfs** needs macFUSE, a third-party kernel extension that wants reduced security on
    Apple Silicon: a worse trade than five minutes of staleness;
  - **moving the tree out of the home** (a symlink back) changes the agent's own paths for the
    hub's convenience, and tools that resolve symlinks then disagree about where they are.
- **macOS homes are `drwxr-x--- <user>:staff`**, and an account made in System Settings (or by
  `sysadminctl`) has `staff` as its primary group, so it can read every standard user's home.
  `worker-setup.sh` makes `hubdiag` and `agent` with `dscl`, each with **its own primary group**
  (so `dsmemberutil checkmembership -U hubdiag -G staff` says not a member), and adds an ACL
  **deny** of traverse for each on the admin's home, which holds even if one is later put in
  `staff` (CI proves both: [`tests/ci-macos.sh`](tests/ci-macos.sh)).

**Logs on macOS.** There is no `systemd-journal` group. hubdiag reads the launchd services' logs
(`/tmp/ikigai-*.log`, world-readable by launchd's default umask); the unified log refuses it:
`log show` as a non-admin answers *Could not open local log store: Operation not permitted*
(measured on the CI runner), and making hubdiag an admin would give it sudo. A key login by an
account whose password is `*` works on macOS too (also measured there).

`--view NAME=DIR` adds or replaces views; the default is `work=<agent's home>/work`. A person's
own tree may be a second view (`--view work=/home/agent/work --view src=/home/<admin>/src`); a
view whose source is in the admin's home is also checked, as `agent`, to be unwritable by it.

## The boundary self-test

`sudo ./worker-setup.sh --check` runs [`probe.sh`](probe.sh) AS each account and fails on:

- **as hubdiag:** traverse or listing of the admin's or the agent's home; ANY file in either home
  that opens (root enumerates every file, minus build output and caches, and hubdiag tries to
  open each: the full form of the traverse-leak test); a view it cannot read, or can write (a
  real append-open of existing files, which writes nothing), or a view not on a read-only mount
  (Linux); the agent's tree writable; a forbidden group (`sudo`, `admin`, `wheel`, `adm`,
  `docker`, `lxd`, `libvirt`, `staff`); sudo allowed without a password; no journal (Linux).
- **as agent:** traverse of the admin's home, or any file in it that opens; a view source in the
  admin's home writable; a forbidden group; no linger (Linux).
- **sshd's effective configuration** for hubdiag (`sshd -T -C user=hubdiag,…`): ClientAlive 10/3,
  no password, no X11 or agent forwarding, TCP forwarding remote only, and `agent` denied.
- **macOS:** neither account in `staff`; hubdiag in `com.apple.access_ssh` when Remote Login is
  limited to listed users.

It then writes the **facts file** (below): the things hubdiag cannot see for itself, because the
homes are closed to it.

## The two interfaces the hub reads

### The probe, over ssh as hubdiag

The worker needs no copy of `probe.sh` for this: the hub pipes it to `bash -s`.

    ssh hubdiag@<worker> bash -s -- <arguments> < probe.sh

The arguments a hub passes (this is what ikigai's hub check sends; `--help` lists them all):

| argument | for |
| --- | --- |
| `--sections identity,closed,view,nowrite,journal,linger,sleep,hw,tools,gh` | everything the hub can learn as hubdiag |
| `--closed /home/<admin> --closed /home/agent` (macOS: `/Users/…`) | homes hubdiag must not reach |
| `--home /home/agent` | where the tools section also looks (`~/.cargo/bin`, `~/.deno/bin`, …) |
| `--view /srv/hubdiag/<name> --no-write <source>`, per view (macOS: `/Users/Shared/hubdiag/<name>`) | readable, not writable; the source not writable |
| `--forbid-group G`, each of `sudo admin wheel adm docker lxd libvirt staff` (macOS: `admin wheel staff _developer`) | groups hubdiag must not be in |
| `--expect-group systemd-journal --linger agent` (Linux only) | the journal, and the agent's user units |

Output is one line per finding, `PASS|FAIL|WARN|INFO <check>: <detail>`, then `summary: …`;
exit 0 with no FAIL, 1 with at least one, 2 on usage. Two more rules belong to the hub, because
only it knows them: the `INFO identity:` line must name `hubdiag` (a wrong `User` in the ssh
stanza otherwise probes as someone else), and hubdiag must hold **no** gh token (`INFO gh-token:`
says `holds no gh token` or `no gh installed`).

### The facts file

`--check` writes it at `/srv/hubdiag/facts` (macOS: `/Users/Shared/hubdiag/facts`), `root:hubdiag
0640`, and the hub reads it with `ssh hubdiag@<worker> cat <path>`. Line by line:

| line | means |
| --- | --- |
| `# …` | a comment |
| `checked: 2026-10-10T15:06:00Z` | when the root-side check ran (UTC) |
| `result: PASS` or `result: FAIL` | its verdict, followed by each failing line indented two spaces (`  FAIL …`) |
| `[agent]`, `[<admin>]` | the account the following lines are about |
| `INFO gh-token: …` | whether that account holds a gh token, and of what kind, **by prefix only**: `github_pat_` fine-grained, `ghp_` classic, `gho_` gh's OAuth login (the token is never printed or stored) |
| `INFO tool <name>: <path>  (<version>)` or `… absent` | the tools in that account's home and on the system |
| `INFO ai-agent <claude\|codex\|hermes>: <path>  (<version>) · login: …` | the coding agents, and whether a login is held, **by the presence of its credential file only** (`~/.claude/.credentials.json`, `~/.codex/auth.json`, `~/.hermes/auth.json` or `.env`; never opened) |
| `INFO ai-agent hermes-profile <name>[ (active)]: provider …, model … · login: …` | each hermes profile, with only those two keys of its `config.yaml` |
| `newest-bin: <UTC time> <path>` | the newest program in that account's `~/.cargo/bin` and `~/.local/bin` at the check |

⚠ **The facts are a snapshot.** An install made after the check is invisible to the hub, by the
same boundary. Report the facts' age and the `newest-bin` line, and after installing anything as an
account, re-run `sudo ./worker-setup.sh --check`. (A comparison against the newest binary NOW
cannot be made from hubdiag, which cannot list those directories; that is the boundary working.)

## CI

The jobs `worker boundary (linux)` and `worker boundary (macos)` run the setup for real on
throwaway GitHub-hosted runners ([`tests/ci-linux.sh`](tests/ci-linux.sh),
[`tests/ci-macos.sh`](tests/ci-macos.sh); both refuse to run anywhere else), prove `--check`
green, then re-create each mistake and prove it RED: the first worker's ACL, the same for
`agent`, a world-traversable home, a writable agent tree, a view remounted read-write, and on
macOS `hubdiag` in `staff` without the deny. On Linux they also log in as hubdiag over real ssh,
prove the one remote forward allowed and every other forward refused, and run the hub's probe
over that ssh, sleep check included. [`tests/probe-test.sh`](tests/probe-test.sh) and
[`tests/setup-test.sh`](tests/setup-test.sh) are the hermetic halves, which need no root and run
in `scripts/gates.sh` everywhere.

## Setting up a worker, in order

### 1. The OS

**Linux (Pop!_OS / Ubuntu 24.04; any systemd distribution should do):**

    sudo apt update && sudo apt install -y openssh-server acl build-essential pkg-config clang libclang-dev git curl
    sudo systemctl enable --now ssh

`acl` gives `getfacl`/`setfacl`, which the setup uses to find and remove a stray ACL. NVIDIA: Pop's
NVIDIA ISO ships the driver; `nvidia-smi` should name the GPU before Ollama is installed.

**macOS:** System Settings > General > Sharing > **Remote Login** on, "Allow access for: Only these
users" (the setup adds `hubdiag` to that list, `com.apple.access_ssh`). Then
`xcode-select --install` for the compilers and libclang. Note that Remote Login's switch may need
Full Disk Access for the terminal if you try `systemsetup -setremotelogin on` instead.

### 2. The accounts, the views, sshd

Copy the hub's PUBLIC key to the worker (say, `hub.pub`), then on the worker, from a checkout of
this repository (or just the two files `worker-setup.sh` and `probe.sh`, side by side):

    ./worker/worker-setup.sh --dry-run --key hub.pub     # read every line
    sudo ./worker/worker-setup.sh --key hub.pub
    sudo ./worker/worker-setup.sh --check

The dry run needs no root and no `--admin`: not under sudo, it takes you (`id -un`) as the admin
and says so (`--admin <user>` names someone else). Each run ends with the NEXT command, labeled as
such: the dry run's is the real setup, the setup's is `--check`, a different command.

On a machine set up by hand before this existed, the setup also **repairs**: it moves hubdiag's
groups to exactly `systemd-journal` (Ubuntu's `adduser` also adds `users`), removes any ACL entry
naming hubdiag or agent from the admin's home, makes `authorized_keys` root-owned, and re-modes
`/srv/hubdiag` to `root:hubdiag 0750`. Lines added by hand to `/etc/ssh/sshd_config` can be
removed afterward: the drop-in (`/etc/ssh/sshd_config.d/10-ikigai-worker.conf`) carries them, and
comes first.

### 3. The GitHub token, as `agent`

A **fine-grained** personal access token, scoped to the organization whose repositories the worker
works in:

1. Organization setting, once: `github.com/organizations/<org>/settings/personal-access-tokens`:
   allow fine-grained tokens, and decide whether they need approval.
2. `github.com/settings/personal-access-tokens/new`: resource owner **the organization**, an
   expiry, the repositories the worker may touch, and these permissions, nothing else:
   - Repository: **Contents** read and write, **Pull requests** read and write, **Actions** read,
     **Commit statuses** read, **Metadata** read (mandatory).
   - Not **Workflows** (a worker must not edit CI), not **Administration**.
3. If the organization requires approval, an owner approves it; until then it reads nothing.
4. On the worker, AS the agent (the token is typed, never echoed, and never in history):

       sudo -iu agent
       read -rs T && printf '%s' "$T" | gh auth login --with-token && unset T
       gh auth setup-git
       gh auth status          # says where the token is stored, and its prefix class

   On macOS the agent never logs in to the GUI, so it has no unlocked login keychain; gh then
   keeps the token in `~agent/.config/gh/hosts.yml`, which the boundary protects. Check what
   `gh auth status` reports rather than assume (not yet tried on a worker Mac).
5. `sudo ./worker/worker-setup.sh --check` again, so the facts name the holder: the `[agent]`
   block should say `holds a FINE-GRAINED token`.

⚠ **No Checks permission exists for fine-grained tokens.** `gh pr checks` fails with *Resource
not accessible by personal access token (statusCheckRollup)*. The Actions API answers, and is
enough when all of a repository's CI is GitHub Actions:

    gh run list -R <org>/<repo> --branch <branch> --json name,status,conclusion,headSha

A check built on it must treat a run as green only once it completed with success, skipped or
neutral, and no runs at all as no verdict. Whether a worker merges at all, or the dispatcher does
after checking the evidence, is the dispatcher's decision ([AGENTS.md](../AGENTS.md): the worker
reports, the dispatcher closes).

### 4. It must never sleep

A worker that suspends stops claiming, renewing and reporting, and nothing says why. The first
worker (a laptop on AC, lid open) suspended mid-afternoon from COSMIC's idle auto-suspend; nothing
at the system level prevented it. The probe's `sleep` section FAILs on a machine that can sleep,
and reports what it saw: on Linux `systemctl is-enabled` for the five sleep targets (`masked` is
the only pass), logind's `HandleLidSwitch*`, the battery and AC state, and the suspends in this
boot (`PM: suspend entry` in the kernel journal); on macOS the AC profile's `sleep` and any
`SleepDisabled`, and the sleep events in `pmset -g log` since boot.

**Linux**, as the admin (a desktop's own idle-suspend setting then has nothing to call):

    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

and, only if it will run with the lid CLOSED, in `/etc/systemd/logind.conf.d/worker.conf`:

    [Login]
    HandleLidSwitch=ignore
    HandleLidSwitchExternalPower=ignore
    HandleLidSwitchDocked=ignore

then `sudo systemctl restart systemd-logind` (it ends graphical sessions on some desktops: do it
from a text console or over ssh, or reboot). With the targets masked a closed lid asks logind to
suspend and the request fails, so the machine stays up either way; `ignore` stops it trying.

**macOS**, as the admin:

    sudo pmset -c sleep 0 disksleep 0

`pmset(1)` documents both: `-c` is the charger (wall power) profile, `sleep` is "system sleep
timer (value in minutes, or 0 to disable)", `disksleep` the disk spindown timer. A Mac mini has
only that profile. (`pmset disablesleep 1`, which `pmset -g` shows as `SleepDisabled 1`, is not in
the man page; the check accepts it but the runbook does not rely on it.) Display sleep is harmless
and can stay.

### 5. Ollama, and a model by memory

    # Linux: the official installer, a systemd service on 127.0.0.1:11434
    curl -fsSL https://ollama.com/install.sh | sh
    # macOS: the app, or brew install ollama && brew services start ollama

| memory for the model | what fits | e.g. |
| --- | --- | --- |
| 6 GB VRAM (a laptop RTX 3060) | 7–8B at 4-bit, with room for context | `qwen3:8b`, `qwen2.5-coder:7b` |
| 64 GB unified (a Mac mini) | up to ~30B, leaving the system ~20 GB | `qwen3-coder:30b` (a 3B-active MoE; fast), or a dense ~30B at 4-bit |

A model larger than the VRAM spills into system RAM and runs at a fraction of the speed: on a
6 GB card, stay under ~5 GB of weights. Every local account can ask Ollama for its models over
HTTP, so the probe's `tools` section lists them. Spell the host `127.0.0.1` wherever `llm.json`
or a `--cap` names it (gonk's [fresh-machine runbook](https://github.com/ikigai-rs/ikigai-gonk/blob/main/docs/fresh-machine.md),
"Why the host's spelling matters").

### 6. ikigai and gonk, as `agent`

Follow gonk's [`docs/fresh-machine.md`](https://github.com/ikigai-rs/ikigai-gonk/blob/main/docs/fresh-machine.md)
as the agent account (`sudo -iu agent`; rustup installs into its home). ★ **`agent`'s Rust lives
in `/home/agent/.cargo/bin`** (macOS: `/Users/agent/.cargo/bin`), and what finds it depends on how
a program starts:

- **A login shell** (`sudo -iu agent`, `sudo machinectl shell agent@`): rustup's installer adds
  `. "$HOME/.cargo/env"` to `~/.profile` and `~/.bashrc`, so `cargo` and `ikigai` are on `PATH`.
  Verify with `command -v cargo ikigai`.
- **A systemd user unit** (the worker loop) reads neither file. Set it in the unit, or use
  absolute paths: `Environment=PATH=/home/agent/.cargo/bin:/usr/local/bin:/usr/bin:/bin`.
- **A launchd job** (macOS) likewise: the plist's `EnvironmentVariables` dictionary carries a
  `PATH` key with `/Users/agent/.cargo/bin` first.

That runbook was written and captured on macOS. The differences on Linux, and on a worker
account, that it does not yet say:

1. **Packages.** It says `build-essential clang libclang-dev git` for Debian/Ubuntu and that a bare
   Linux box is untried. `pkg-config` is also wanted, and `libssl-dev` may be for the same RocksDB
   build; untested here whether gonk's graph needs it.
2. **Running it as a service.** It points at a launchd agent and says a systemd user unit is
   "the same idea". On Linux a user unit stops at logout unless the account **lingers**
   (`worker-setup.sh` turns it on for `agent`), and `systemctl --user` from `sudo -iu agent` needs
   that session's bus; if it says *Failed to connect to bus*, use `sudo machinectl shell agent@`.
3. **The socket path limit** is 104 bytes on macOS and 108 on Linux; its `--data-home` caution
   applies to both.
4. **Passkeys need a browser on the machine**, at `localhost`. A worker is headless and works
   over the socket and QUIC, so the runbook's browse invite does not apply; the hub's gonk is
   reached through the tunnel instead.
5. **The model server.** Its example is MLX; MLX is Apple-silicon only. On Linux use Ollama
   (above) at `http://127.0.0.1:11434/v1`.
6. **macOS, a worker account with no GUI login:** a TCC prompt (Local Network, for an mDNS
   announce) can never be answered for it, and it has no unlocked keychain. Keep the agent's
   ikigai processes off mDNS announcing (configured peers, not discovery), and expect anything
   that reaches for the Keychain to fail. Unverified: no worker Mac has run it yet.
7. **Firewall.** A Linux worker running `ufw` must allow the QUIC port (UDP) only if the hub
   dials in; the worker loop's design has the worker pull, so nothing is opened.

### 7. Reach it from the hub

On the hub, a `~/.ssh/config` stanza for the worker that logs in as `hubdiag` with the key whose
public half you installed:

    Host <worker>
      HostName <worker's name or address>
      User hubdiag
      IdentityFile ~/.ssh/<hub key>

then the probe (above) as the first read. gonk's tunnel is a remote forward the sshd drop-in
allows and nothing else does, kept up by a service on the hub:

    ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -R 1060:localhost:1060 <worker>

On the worker, `localhost:1060` then reaches the hub's gonk; a dead session frees the port in
about 30 seconds (`ClientAliveInterval 10`, `CountMax 3`), so the service can re-bind it.

## What this was learned on

The Linux half was learned setting up the first worker by hand, a Pop!_OS 24.04 laptop with a
6 GB RTX 3060, and is proved on GitHub's hosted Ubuntu runners. The macOS half was written from
Apple's man pages and is proved on GitHub's hosted macOS runners; no worker Mac has run it yet.
Windows (WSL2) is not covered yet.

## Files

| file | runs | what |
| --- | --- | --- |
| [`worker-setup.sh`](worker-setup.sh) | on the worker, as root | setup (idempotent, `--dry-run`), and `--check` |
| [`probe.sh`](probe.sh) | as the probed account | what this account can reach; read-only by construction |
| [`tests/probe-test.sh`](tests/probe-test.sh), [`tests/setup-test.sh`](tests/setup-test.sh) | anywhere, no root | the hermetic tests, run by `scripts/gates.sh` |
| [`tests/ci-linux.sh`](tests/ci-linux.sh), [`tests/ci-macos.sh`](tests/ci-macos.sh) | on throwaway CI runners only | the boundary, end to end |
