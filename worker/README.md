# worker: machines that do agentic work for a hub

A **worker** is a machine a hub dispatches work to: it claims a ledger item, branches a fresh
worktree, runs an agent, pushes a branch and opens a pull request. That loop is
[`tools/worker.py`](../tools/worker.py) (step 8 below). This directory turns setting one up
into a mechanism instead of someone's memory: two scripts the worker's admin runs on the
worker, two interfaces the hub reads over ssh, and the units that run the loop as a service.

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
| `agent` | the worker (by design, it never works in a person's checkouts) | the GitHub token; a worktree per item under `~agent/work` | its own home | its own home |
| `hubdiag` | the hub's eyes | nothing | the journal; the **views** (by default `~agent/work`) | nothing |

Neither `agent` nor `hubdiag` has sudo or a password. `hubdiag` logs in by key only, and sshd lets it
open one remote forward (gonk's tunnel, port 1062) and nothing else; `agent` cannot log in over ssh
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
  that opens (root enumerates every file, minus build output and caches, and on macOS minus
  `~/Library`, which the deny on the home already closes and which is 0700 besides, and hubdiag tries to open each: the full form of the traverse-leak test; the walk
  prints its progress every five seconds); a view it cannot read, or can write (a
  real append-open of existing files, which writes nothing), or a view not on a read-only mount
  (Linux); the agent's tree writable; a forbidden group (`sudo`, `admin`, `wheel`, `adm`,
  `docker`, `lxd`, `libvirt`, `staff`); sudo allowed without a password; no journal (Linux).
- **as agent:** traverse of the admin's home, or any file in it that opens; a view source in the
  admin's home writable; a forbidden group; no linger (Linux).
- **sshd's effective configuration** for hubdiag (`sshd -T -C user=hubdiag,…`): ClientAlive 10/3,
  no password, no X11 or agent forwarding, TCP forwarding remote only, and `agent` denied.
- **macOS:** neither account in `staff`; hubdiag in `com.apple.access_ssh` when Remote Login is
  limited to listed users.
- **WSL2:** WSL1, or systemd not PID 1; and, as hubdiag and as agent, any file of the Windows
  user's profile that opens through `/mnt/c` (or the profile listed), or a Windows program either
  could start. As root it reads the Windows user's `.wslconfig` and reports, without failing,
  whether WSL shuts the distribution down when idle ([Windows (WSL2)](#windows-wsl2)).

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
| add `wsl,wsl-mnt` to `--sections` (any Linux; on one that is not WSL they say so and check nothing) | under WSL2: systemd as PID 1, and hubdiag unable to read the Windows user's files ([Windows (WSL2)](#windows-wsl2)) |

Output is one line per finding, `PASS|FAIL|WARN|INFO <check>: <detail>`, then `summary: …`;
exit 0 with no FAIL, 1 with at least one, 2 on usage. Two more rules belong to the hub, because
only it knows them: the `INFO identity:` line must name `hubdiag` (a wrong `User` in the ssh
stanza otherwise probes as someone else), and hubdiag must hold **no** gh token (`INFO gh-token:`
says `holds no gh token` or `no gh installed`). The gh section also reports the system's gh
version, and WARNs below 2.50.0, the first gh with `gh pr checks --json`, printing the upgrade
line (GitHub's apt repository on Debian and Ubuntu, whose own 24.04 package is 2.45.0). A worker
with an older gh still works: it reads CI from the Actions runs instead, the less exact path.

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
| `INFO gh-version: …` | the system's gh and its version (a gh older than 2.50.0 is a `WARN` in the probe's own output, above, and does not reach this file) |
| `INFO tool <name>: <path>  (<version>)` or `… absent` | the tools in that account's home and on the system |
| `INFO ai-agent <claude\|codex\|hermes>: <path>  (<version>) · login: …` | the coding agents, and whether a login is held, **by the presence of its credential file only** (`~/.claude/.credentials.json`, `~/.codex/auth.json`, `~/.hermes/auth.json` or `.env`; never opened) |
| `INFO ai-agent hermes-profile <name>[ (active)]: provider …, model … · login: …` | each hermes profile, with only those two keys of its `config.yaml` |
| `newest-bin: <UTC time> <path>` | the newest program in that account's `~/.cargo/bin` and `~/.local/bin` at the check |
| `[windows]`, then `INFO\|WARN wsl-idle: …`, `INFO wsl-network: …` | WSL only: what root read in the Windows user's `.wslconfig`, which hubdiag must not be able to read |

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

**With an invitation, the machine enrolls itself** (ledger item 2009). The controller runs
`onboard invite <name> --kind worker` (or `--kind task-member`, or both kinds for one machine)
and hands you a pairing code, an invite id, the layer door's address, its server certificate and
that certificate's fingerprint. Then, as `agent`, in the checkout of step 8:

    python3 tools/worker.py onboard --door door.example.org:4434 --pin <door fingerprint> \
        --server-cert door.crt --code <pairing code> --invite <id> --name <name> \
        --agents claude,codex --gonk gonk.example.org:1062 \
        --gonk-server-cert gonk.crt --gonk-pin <gonk fingerprint>

It makes this machine's certificate here (`~/.ikigai/onboard/<name>`; the key never leaves),
refuses a server certificate that does not hash to its pin, posts the enrollment request to the
door's `enroll` layer with the code's proof, and polls the `grants` layer until the controller
approves it (exponential backoff for up to `--max-wait`, default 24 h; `--once` looks once and
exits 3 while not enrolled). On the entitlement it writes `.gonk/worker-<agent>.toml` per agent
(init-agents' own code, with the entitlement's routers), `.gonk/local.toml` for gonk over QUIC,
the layers client's cert set (`~/.ikigai/layers-client/<name>`) for a task layer, and the
config-home `mount` line for the door in `~/.ikigai/onboard/<name>/config-home.toml` (copy it
into config.toml yourself: every ikigai server on the machine inherits that file), then prints
the lines that start the loops. It never replaces a file that differs without `--force`, and a
rerun posts nothing it posted before. That replaces the hand steps in 6 and 8: making the
identity, sending the certificate, the fingerprint read-back, and editing the configuration.

**What still needs a person:** the OS and its accounts (1, 2), the GitHub token (3), keeping it
awake (4), the model (5), the hub's `hubdiag` ssh stanza and the tunnel (7), the repositories
each loop may work in (`tools/worker_repos.py`), the approval on the controller's side, and,
until the doors reload their trust lists (ledger item 1825), a restart of the door and gonk
after the approval. Steps 1 to 8 below are the whole procedure, by hand.

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

Two routes. Which one is open depends on whether the worker's GitHub account is a **member** of
the organization: GitHub offers an organization as resource owner only to its members.

**Route A, the worker is a member: a fine-grained token** scoped to the organization:

1. Organization setting, once: `github.com/organizations/<org>/settings/personal-access-tokens`:
   allow fine-grained tokens, and decide whether they need approval.
2. `github.com/settings/personal-access-tokens/new`: resource owner **the organization**, an
   expiry, the repositories the worker may touch, and these permissions, nothing else:
   - Repository: **Contents** read and write, **Pull requests** read and write, **Actions** read,
     **Commit statuses** read, **Metadata** read (mandatory).
   - Not **Workflows** (a worker must not edit CI), not **Administration**.
3. If the organization requires approval, an owner approves it; until then it reads nothing.

**Route B, the worker is an outside collaborator** (ikigai-worker is one): it cannot create that
token. Making it a member costs a paid seat and exposes private repositories through the
organization's base permission, so use a **classic** token instead:

1. `github.com/settings/tokens/new` (classic), an expiry, and only the scope **`public_repo`**:
   not `repo`, not `workflow`. Its reach is bounded by the collaborator grants the account has on
   public repositories, and nothing the token says widens that.
2. `gh auth login --with-token` refuses a classic token without the **`repo`** and **`read:org`**
   scopes, and `public_repo` alone is refused. Either create the token with those scopes too (the
   token then reaches every repository the account may push to, private ones included, still
   bounded by the grants), or accept that `gh auth login` is not the way to store it.
   Measured on one worker, 2026-10-16: a classic token with `repo` and `read:org` pushed to 8 of 47
   enrolled repositories, exactly its collaborator grants.
3. Which repositories the worker can push to is decided by those grants, not by the token:
   `tools/worker_repos.py --check-push` says which are missing, and `repo-protect --grant-worker`
   grants them.

`worker.py onboard` does not touch the token: it stays a person's step, typed by hand.

Either way, then:

4. On the worker, AS the agent (the token is typed, never echoed, and never in history):

       sudo -iu agent
       read -rs T && printf '%s' "$T" | gh auth login --with-token && unset T
       gh auth setup-git
       gh auth status          # says where the token is stored, and its prefix class

   On macOS the agent never logs in to the GUI, so it has no unlocked login keychain; gh then
   keeps the token in `~agent/.config/gh/hosts.yml`, which the boundary protects. Check what
   `gh auth status` reports rather than assume (not yet tried on a worker Mac).
5. `sudo ./worker/worker-setup.sh --check` again, so the facts name the holder: the `[agent]`
   block should say `holds a FINE-GRAINED token` (route A) or a classic one (route B).

⚠ **No Checks permission exists for fine-grained tokens** (a classic token has no such limit, and `gh pr checks` works with it). With a fine-grained token `gh pr checks` fails with *Resource
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

**Masking the targets is not the whole fix: the desktop has to be told to stop trying.** On
2026-10-11 the first worker's COSMIC idle daemon ran `systemctl suspend`; logind broadcast *The
system will suspend now!* to every tty, and only then found `suspend.target` masked and refused
(`Unit suspend.target is masked, refusing operation.`). Nothing slept — the mask works — but a
wall message on every console reads like the machine is about to go down, and it is noise a hub
has to explain. The mask cannot prevent it: logind decides to broadcast and to start the target
before it ever looks at the target's state, and its only pre-checks are `sleep.conf`'s `Allow*`
booleans and whether the kernel supports sleep at all, neither of which knows a target is masked.
So turn the desktop's OWN automatic suspend off as well; the mask stays as the backstop for
everything else (a stray `systemctl suspend`, a lid switch, another tool).

- **COSMIC** (what the first worker runs): Settings > **Power & Battery** > **Automatic suspend**
  (and *Automatic suspend when plugged in* / *on battery power*) > **Never**.
- **GNOME:** `gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'`
  (and `sleep-inactive-battery-type` on a laptop), or Settings > Power > Automatic Suspend > Off.
- **KDE:** System Settings > Power Management > *Suspend session* > Never.
- **Xfce:** `xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/inactivity-on-ac -s 0`.

The probe's `sleep` section reports it: `INFO suspend-attempts: N refused in this boot …` names the
count and says which setting to turn off, or `none refused in this boot` when the desktop is
already quiet. It is INFO and not a FAIL (the machine is up; the mask held), so a masked machine
that still shows a count is telling you the desktop half of the fix is outstanding.

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

With an invitation, `worker.py onboard` (above) makes the identity and enrolls it; installing
`ikigai` (and gonk, where this machine runs one) is still this step's.

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

A person's step even with an invitation: `onboard` enrolls the machine's certificate with the
doors, never an ssh key, so the hub's `hubdiag` stanza and the tunnel are set up by hand.

On the hub, a `~/.ssh/config` stanza for the worker that logs in as `hubdiag` with the key whose
public half you installed:

    Host <worker>
      HostName <worker's name or address>
      User hubdiag
      IdentityFile ~/.ssh/<hub key>

then the probe (above) as the first read. gonk's tunnel is a remote forward the sshd drop-in
allows and nothing else does, kept up by a service on the hub:

    ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=10 -R 1062:localhost:1062 <worker>

On the worker, `localhost:1062` then reaches the hub's gonk; a dead session frees the port in
about 30 seconds (`ClientAliveInterval 10`, `CountMax 3`), so the service can re-bind it.
A worker set up before gonk moved from 1060 to 1062 (2026-10-12) permits only 1060: run
`sudo ./worker-setup.sh` again with its original arguments, and it rewrites the drop-in to name 1062.

### 8. The worker loop, as `agent`

[`tools/worker.py`](../tools/worker.py) is the loop. It runs as `agent`, from a clone of this
repository in agent's home (the units below expect `~/gonk-worker`), and works only under
agent's home: it refuses a work root anywhere else, and refuses to run as root.

    sudo -iu agent
    git clone https://github.com/ikigai-rs/ikigai-gonk-starter ~/gonk-worker && cd ~/gonk-worker
    ikigai cert generate --dir ~/gonk-cert
    python3 tools/gonk.py init --ledger <ledger> --holder <worker name> \
        --quic quic://<gonk host>:<port> --cert-dir ~/gonk-cert   # prints the lines to enroll it
    cp worker/worker.sample.toml .gonk/worker.toml                # then edit it
    python3 tools/worker.py plan                                  # what it would take, and why
    python3 tools/worker.py run --once                            # one item, then exit

`init` and the enrollment are the top-level README's "gonk on another machine"; the claims
then carry this machine's identity and are stamped a machine's. The loop uses the regular
`ikigai` client, not `ikigai mcp`. If you also configure an agent with the MCP command
that `init` prints, first create the local `agent` grant using the
[top-level README's MCP setup](../README.md#one-transport-and-mcp-for-agents), with
its unattended worker scopes; the certificate enrollment alone does not create that
client-side grant. That is one loop running one
agent. A machine with several agents runs a loop per agent instead (next), from this same
checkout; the configuration and everything after it below hold for each loop. ⚠ The ssh tunnel in step 7
forwards **TCP**, which carries gonk's HTTP door. gonk's machine door is **QUIC, which is
UDP**, and ssh does not forward UDP, so a worker that reaches gonk only through that tunnel
cannot run the loop over QUIC: it needs gonk's QUIC port reachable directly (or gonk on the
same machine, over its socket).

**Every write the loop makes is authenticated, and the tunnel carries none of them.**
`tools/worker.py` speaks to gonk only through `.gonk/local.toml`'s transport, which is
`socket` or `quic` and nothing else: there is no HTTP transport, so the loop never writes
through the tunnel's anonymous HTTP door, and making that door read-only (anonymous = Read)
changes nothing for it. The authenticated write path is **QUIC, straight from the worker to
gonk's UDP port** (1062 unless gonk's operator chose another) on the network the two share,
with the worker's own client certificate, enrolled with `--worker <ledger>`. gonk's HTTP door
has no machine credential to offer instead: it authenticates a person's passkey session in a
browser, and an anonymous caller is nobody. So, on the worker, as agent, `.gonk/local.toml`
reads:

    transport = "quic"
    quic = "quic://<gonk host's address on that network>:1062"
    cert_dir = "/home/agent/gonk-cert"       # the bundle `init --cert-dir` named
    holder = "<worker name>"

(`python3 tools/gonk.py init --quic … --cert-dir …` writes exactly that.) Two things must hold
on the network: gonk binds its QUIC door on an address the worker can reach, not loopback
(`gonk.quic.bind` in gonk's configuration), and the gonk host's firewall lets that UDP port
in. On macOS, the application firewall allows by APPLICATION, so when it is on it must allow
the `ikigai-gonk` binary that serves the door (by its path, e.g. `~/.cargo/bin/ikigai-gonk`),
and a reinstalled binary may need allowing again. `python3 tools/gonk.py init` with no flags,
on the worker, then says whether gonk answers over QUIC. The tunnel stays useful for reads
(the hub's item pages from the worker, an agent that reads the ledger over HTTP), and for
nothing that must be attributed.

#### One loop per agent

A worker machine with several agents (Claude Code, Codex and Hermes, say) runs **one loop per
agent**, so the dispatcher can route each item to the agent that suits it. It labels the item
`worker:<name>` AND `agent:<agent>`, and only the loop whose configuration names both takes it.
Every loop runs from the one checkout, `~/gonk-worker`, under the one account.

    python3 tools/worker.py init-agents --name <worker name> --agents claude,codex,hermes
    # Review [repos_from] github in each configuration: the sample admits ikigai-rs.
    # Set except = ["name"] for exclusions; explicit [repos.<name>] overrides still work.
    python3 tools/worker.py --config .gonk/worker-codex.toml plan    # each loop, checked
    python3 tools/worker.py --config .gonk/worker-codex.toml run --once

- **`init-agents`** (`setup` is an alias) writes `.gonk/worker-<agent>.toml` for each agent
  (git-ignored, like `worker.toml`), from the machine's `.gonk/worker.toml` when it has one, so its settings and
  repositories come along, and from [`worker.sample.toml`](worker.sample.toml) otherwise. Each
  gets `labels = ["worker:<name>", "agent:<agent>"]`, `holder = "<name>-<agent>"`, and the
  `[agent]` table that has run an item end to end here: Claude Code with
  `--permission-mode bypassPermissions`, Codex with `--sandbox danger-full-access`, Hermes's
  preset (below says why each is opened). It never overwrites a configuration that exists, so
  running it again is safe and an edited file stays edited. `--from <file>` starts from another
  file.
- **`holder`** in a loop's configuration overrides `.gonk/local.toml`'s, so a claim says which
  agent holds it (`my-worker-codex`). The holder is a **label**; the identity stays the
  machine's. gonk records the principal (the machine's certificate) beside the label, so the
  item shows `my-worker-codex (as urn:iki:host:gonk:client:<fingerprint>)`, and **no new
  enrollment** is needed: the certificate enrolled with `--worker <ledger>` claims, renews and
  releases under any holder name. A claim is its holder's label AND its principal, so another
  machine spelling the same label can neither renew nor take it. The claim, every renewal, the
  compare-and-set release and every note the loop posts use the loop's holder.
- **What the loops share, and what each keeps.** Under `.gonk/state/worker/`:

  | | shared by every loop | each loop's own (`<agent>/`) |
  | --- | --- | --- |
  | the per-repository locks (`locks/`) | ✓ two agents never work one repository at once, nor share its clone under the work root | |
  | the items it handed back (`attempts.json`) | | ✓ an item one agent handed back can be routed to another; `--config … forget <N>` clears one loop's |
  | the stop file | `stop` stops every loop between items | `<agent>/stop` stops that loop alone |
  | what it has said on the ledger (`notices.json`) | | ✓ the skips it explained on an item, and the status line it last posted |

  A machine's single `worker.toml` loop keeps `attempts.json` and `stop` where they always were,
  directly under `.gonk/state/worker/`.
- **Routing.** An item labeled only `worker:<name>` is taken by no per-agent loop: it needs an
  `agent:` label too. And do not run the single loop beside the per-agent ones: its labels are
  a subset of theirs, so it would take their items.
- **As services:** [`units/gonk-worker@.service`](units/gonk-worker@.service) is a templated
  systemd user unit, `gonk-worker@<agent>`, which runs
  `tools/worker.py run --config ~/gonk-worker/.gonk/worker-<agent>.toml`; each instance logs to
  the journal under its own name (`journalctl --user -u gonk-worker@codex`). launchd has no
  templated jobs, so on macOS `python3 tools/worker.py plist --agents claude,codex,hermes --out
  <dir>` writes one daemon per agent from
  [`units/dev.ikigai-rs.gonk-worker.plist`](units/dev.ikigai-rs.gonk-worker.plist): its label
  ends `.<agent>`, it passes `--config`, and it logs to `/tmp/ikigai-gonk-worker-<agent>.log`.
  Install each as that file's header says.

**Which agent suits which work** (the dispatcher's choice, by the `agent:` label):

- **Codex**: well-specified code fixes that come with a test, where the item says what is wrong
  and how to show it fixed.
- **Claude Code**: prose, books and documentation, accessibility, and items that need a
  judgment call.
- **Hermes**: a second opinion that is not Claude's, small items, and the non-Claude auditor
  where a process requires one.

**Moving a machine from the single loop to a loop per agent**, as agent
(`sudo machinectl shell agent@`):

    cd ~/gonk-worker && git pull --ff-only
    touch .gonk/state/worker/stop                    # the single loop stops after its item
    systemctl --user status gonk-worker              # until it says inactive (dead)
    systemctl --user disable gonk-worker
    rm .gonk/state/worker/stop                       # it now stops EVERY loop, so remove it
    python3 tools/worker.py init-agents --name <worker name> --agents claude,codex,hermes
    python3 tools/worker_repos.py --owner <owner> --check-push
    for a in claude codex hermes; do python3 tools/worker.py --config .gonk/worker-$a.toml plan; done
    cp worker/units/gonk-worker@.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now gonk-worker@claude gonk-worker@codex gonk-worker@hermes

A drop-in the single unit had (`systemctl --user edit gonk-worker`, e.g. Claude Code's
`EnvironmentFile=`) does not carry over: give it to the instance that needs it,
`systemctl --user edit gonk-worker@claude`. Items handed back by the single loop stay in its
`.gonk/state/worker/attempts.json`, which the per-agent loops do not read.

#### Who may route work here

An unattended agent runs with its own checks opened and a token that pushes, so what it is
told matters as much as where it runs. Two rules keep its instructions to the ones its
dispatcher gave (ledger item 1687 on ikigai's own ledger).

**1. A label is not a dispatch.** The ledger records no setter for a label: a label is a plain
value on the item, and any writer that may label can add `worker:<name>`. It also lets any
writer replace an item's title and body, without attributing the edit. What it does record,
and what gonk checks, is a COMMENT's author: comments are appended and never edited, and
gonk's HTTP and QUIC doors refuse a write whose `author` is shaped like a principal
(`urn:iki:gonk:passkey:…`, `urn:iki:host:gonk:client:…`, the older `urn:iki:gonk:client:…`,
`urn:iki:gonk:layer:…`) unless it is the writer's own, the one the door authenticated. So the
routing is a comment, the **dispatch**:

    Dispatch: worker:<name> agent:<agent> repo:<repo>
    brief: sha256:<SHA-256 of the title, a newline, and the body, as the ledger serves them>

which the dispatcher writes with

    python3 tools/gonk.py dispatch 12 --labels worker:<name>,agent:<agent> [--as <principal>]

It adds the labels the item lacks, reads the item, and posts the dispatch, naming those labels
and the item's one `repo:` label. Over QUIC, gonk names the dispatching machine as the author
(`--as` may name only that machine). Over the owner's socket nothing names anyone, so `--as`
is required there: the principal the dispatch is from, as the workers' `routers` list it.

Before it writes anything it checks the item (ledger item 2394), because a dispatch no worker
will act on sits unexplained:

- It **refuses** an item that is not open, and one handed back and awaiting its dispatcher:
  that is a review (check the evidence, then close it or `reject` it), not a dispatch.
- It **refuses** an item an open item blocks, naming the blockers, as `next` reports them
  (`why: blocked`, `blocked_by`): `next` never offers a blocked item, so no worker would take
  it. `--allow-blocked --note "<why>"` dispatches it anyway, and the dispatch comment carries
  `Dispatched while blocked by <ledger>#N: <why>` after its two routing lines.
- It **warns**, and still dispatches, when no worker whose labels the item will carry lists
  the item's repository in the newest `Worker:` line on its status item (below). The status
  items are discovered from open ledger items labelled `worker-status:<name>`. Coverage
  includes the worker's allowed organization and exclusions, with explicit repositories
  taking precedence. Legacy `--status-item N` and `status_items = [N, …]` still work.
  With no status or matching announcement, it says the check could not run.
- It assigns priority 3 only when the item has no priority (`--priority N` chooses the value).
  `--keep-unprioritized` leaves it unset and warns that prioritized work ranks ahead of it.
  The priority-only item sink changes neither title nor body, so the dispatch digest holds.
  After dispatch it prints rank in `next` for the routing labels, or the exclusion reason.

The worker takes an item only when the **newest** dispatch whose author is one of its
`routers` names every label in its `labels` and the item's `repo:` label, and its digest
matches the item's title and body as they are now. It checks that when it chooses, and again on
the item as read after its claim, and builds the agent's brief from that second read; any
mismatch is a skip (`plan` says which), recorded as the harness sensor
`worker-dispatched-by-a-router`. So: a label added by anyone after a dispatch routes nothing; a
router's later dispatch elsewhere spends its earlier one; a dispatch by anyone else, however it
signs itself, counts for nothing; and an edit to the title or body after the dispatch stops the
item until a router dispatches it again, which is also how a dispatcher sends an edited item.

In the worker's configuration, the one key to add (each loop's file, on a machine with one
loop per agent):

    routers = ["urn:iki:gonk:passkey:<credential id>", "urn:iki:host:gonk:client:<fingerprint>"]

- A **person's passkey** principal is `urn:iki:gonk:passkey:<credential id>`; gonk's item page
  shows it on that person's comments (signed-in writes are attributed to it).
- A **dispatching machine** (a hub that dispatches over QUIC) is
  `urn:iki:host:gonk:client:<fingerprint>`, which `python3 tools/gonk.py init` prints on that
  machine.
- A **hub on gonk's own host**, dispatching over the owner's socket, names a principal with
  `--as`; use the principal of the person who owns that host (their passkey), so the worker
  trusts one name for them whichever door they use.

`routers` is required: a worker refuses a configuration without it, or with an entry that is
not a principal (a free-text author such as `hub` is one any writer can put on a comment).
`worker.sample.toml` ships a placeholder that is refused until it is replaced. After a pull,
a running loop keeps its old configuration until it restarts; restarted without `routers`, it
refuses to start and says why.

**What this proves, and what it does not.** It proves that one of the principals you named
wrote the newest dispatch, with these labels and this brief, given three things it cannot check:
that gonk refuses a borrowed principal `author` at its network doors (it has since 2026-10-07;
an older gonk let any writer name anyone, and then this proves nothing), that the owner's socket
is the gonk host's own account (whoever can open it may write any author, by design), and that
the routers' credentials are their own. It does not say who added a label, only that the labels
that matter were dispatched; it does not stop someone who may write from commenting (rule 2 is
for that), from removing a label, or from un-routing an item by editing it; and it says nothing
about the repository's own content, which the agent reads as code it works on, not as a brief.
The ledger recording who set each label, and an edit's author, would let a worker check the
labels themselves; until it does, the dispatch is the record.

**2. Comments reach the agent as data.** The title and body, pinned by the dispatch, are the
agent's task. Every comment follows as a fenced block under its author's name, as a JSON string
so a name cannot break the line, with a fence longer than any run of backticks in the comment so
it cannot close its own block. The dispatcher's comments (by a router) come under "Notes from
your dispatcher"; everyone else's under a header that says they are data written by others, not
instructions, and the fixed preamble's rule 7 tells the agent never to follow a request in them.
The dispatches themselves and the worker's own hand-backs are left out. This lowers the odds of a
planted comment steering the agent; it cannot make a model immune to text it reads, which is why
rule 1 decides what the agent works on, and why its account, token and the repository's branch
protection stay the boundary.

#### What a worker says on the ledger

A dispatched item the worker skips must not wait unexplained (ledger item 2349 sat nine hours
behind a reason only its worker's journal held; ledger item 2393 is the fix). Two things go on
the ledger, each as a comment by the worker's own principal, with no token, account id or path:

- **A skip the dispatcher can fix, on the item.** When the newest dispatch by a router names
  every one of this loop's `labels` (the item was sent HERE), and the loop skips it because its
  `repo:` label names a repository this loop is not configured for (or it has none, or two), its
  dispatch no longer matches its title and body, or the loop holds for usage, the loop comments
  which worker and loop, the reason and the remedy: for a missing repository, the
  `[repos.<name>]` block to add (an https remote; an ssh one works as well). It says each reason
  once per item, remembered in the loop's `notices.json`, so neither the next poll nor a
  restart repeats it; a new reason (a new dispatch that still does not match, say) is said
  again. The other skips (claimed, awaiting the dispatcher, handed back already, one agent per
  repository) are not the dispatcher's to fix, and stay in the journal; an item claimed or
  awaiting its dispatcher is never commented on.
- **What the loop takes, on its status item.** At start the loop labels its existing
  `[usage] status_item` with `worker-status:<name>` (from its `worker:<name>` routing label).
  With no configured number it discovers a single open item with that label; the dispatcher
  can label an existing standing item once. If a restricted worker grant refuses the label,
  the loop asks the dispatcher to add it and still publishes its status. It posts one line
  when its admission changes:

      Worker: holder=<holder> loop=<loop> labels=<label>,… repos=<name>,… repos_from=<org> except=<name>,…

  (`loop=` only for one of several loops; `gonk.parse_worker_status` reads it back), so a
  dispatcher can check that a worker carries an item's labels and repository before sending it
  there. A running loop keeps the configuration it started with, so a changed one is posted
  when the loop restarts. New repositories under the admitted organization are derived per
  item and need no configuration edit or restart. Coverage describes configuration admission;
  GitHub access is checked by the worker before taking an item.

#### Enrolling every repository

A worker admits repositories through an organization allowance or explicit stanzas. New
configurations use this allowance by default; review the organization before running:

```toml
[repos_from]
github = "ikigai-rs"
except = ["sensitive-repo"]  # optional names, compared without case
```

An unlisted `repo:<name>` becomes `https://github.com/<org>/<name>.git`, GitHub `<org>/<name>`,
base `main`. Each candidate is checked with `gh api repos/<org>/<name>` for existence, matching
identity (a redirect outside the configured repository is refused) and `permissions.push`.
Failures are skipped with the reason, said once on the dispatched item. Router authorization
and this configuration fence both apply. An explicit `[repos.<name>]` always wins, including
remote, owner, base and exclusions. Names cannot include an owner or path.

Existing explicit configurations require no migration. To opt in, add `[repos_from]` to each
loop and restart once. Every new repository in the organization then needs neither an edit
nor a restart. Fine-grained tokens still need access to each new repository on GitHub.

For operators who prefer explicit lists,
[`tools/worker_repos.py`](../tools/worker_repos.py) still adds them from GitHub:

    python3 tools/worker_repos.py --owner <owner> --dry-run     # the diff it would make
    python3 tools/worker_repos.py --owner <owner>               # make it
    python3 tools/worker_repos.py --owner <owner> --include-private --exclude 'scratch-*'
    python3 tools/worker_repos.py --check-push                  # only ask, about every one

It lists the owner's repositories (`gh repo list <owner> --json name,url,isArchived,visibility
--limit 1000`), skips archived ones, private ones unless `--include-private`, any that
`--only`/`--exclude` (names or shell patterns, comma-separated) leave out, and any whose name a
`repo:<name>` label cannot carry (`.github`); then it appends a block (`remote`, `github`) for
each one missing from **each** configuration it is pointed at: every `.gonk/worker*.toml` by
default, `--config <file>` (repeatable) for some. It only appends: an existing block is never
edited or removed (one with another name but the same `github` counts as enrolled), every
comment stays, the file is replaced atomically and keeps its mode, and a second run adds
nothing.

`--check-push` asks GitHub, for every repository the configurations name, whether the account
`gh` is signed in as may push (`gh api repos/<owner>/<name> --jq .permissions.push`), and lists
the ones it may not, with the page to fix it on. The fine-grained token's repository list is a
manual step: on GitHub, signed in as the account the token belongs to, at
`https://github.com/settings/personal-access-tokens`, edit the worker's token and add the
repositories under **Repository access** (an organization that approves tokens then approves
the change at `https://github.com/organizations/<owner>/settings/personal-access-token-requests`).
With a classic token it says instead that the collaborator grants decide, and points to
`repo-protect --grant-worker` (it reads the token's type from `gh auth status`).
⚠ Unverified here: whether GitHub narrows `permissions.push` to a fine-grained token's own
repository list, or reports the account's role on the repository. A repository the token was
not given answers 404 when it is private; a public one answers, and may say `true` for an
account that could push with another credential. The first push is the final word.

**The configuration**, `.gonk/worker.toml`, or each loop's `.gonk/worker-<agent>.toml`
(git-ignored; [`worker.sample.toml`](worker.sample.toml) is the commented template):

| key | default | what |
| --- | --- | --- |
| `labels` | (required) | `next` is narrowed to items carrying ALL of these, e.g. `["worker:<name>"]`, or `["worker:<name>", "agent:<agent>"]` for one loop of several; the dispatcher marks an item for this worker by labeling it |
| `routers` | (required) | the principals whose dispatch routes an item here, e.g. `["urn:iki:gonk:passkey:<credential id>"]`; never free text ("Who may route work here") |
| `holder` | `.gonk/local.toml`'s | the holder name this loop claims under, e.g. `<name>-<agent>`; a label, never a new identity |
| `without` | none | items carrying ANY of these are never taken |
| `[repos_from]` | none; sample uses `ikigai-rs` | `github` is one allowed organization; optional `except` excludes names; requires this table or an explicit repository |
| `[repos.<name>]` | none | `remote` (what `git clone` takes), `github` (`owner/name`, read from a github.com remote when omitted), `base` (default `main`) |
| `[agent]` | (required) | `preset` (`claude`, `codex`, `hermes`, `hermes-profile` with `profile`) or `command`, a list with placeholders `{prompt}` `{prompt_file}` `{workdir}` `{branch}` `{item}` `{repo}` `{report_file}` `{profile}` |
| `work_root` | `~/work` | worktrees go to `<work_root>/<repo>/item-<N>`; must be under agent's home |
| `lease`, `renew_every` | `30m`, a third of the lease | the claim's lease, and the heartbeat that renews it |
| `budget` | `45m` | wall-clock per item; the agent is stopped when it runs out |
| `ci_wait`, `ci_poll` | `10m`, `20s` | how long to wait, after the agent exits, for its pull request's CI to finish |
| `poll` | `60s` | between polls of `next` when nothing is ready |
| `keep` | `false` | keep every worktree after its hand-back (`run --keep` for one run) |
| `gh`, `stop_file` | `gh`, `.gonk/state/worker/stop` (a loop's own: `.gonk/state/worker/<agent>/stop`) | the GitHub CLI; the file that stops the loop between items (`.gonk/state/worker/stop` stops every loop) |

The presets are each agent's documented non-interactive form (`claude -p`, `codex exec`,
`hermes -z`). **Hermes, Codex and Claude Code have each run an item end to end on a Linux
worker**: hermes as its preset, Codex with its sandbox opened, `command = ["codex", "exec",
"--sandbox", "danger-full-access", "{prompt}"]`, and Claude Code with its permission checks
off, `command = ["claude", "-p", "--permission-mode", "bypassPermissions", "{prompt}"]`
(below). Both open up for the same reason: the agent commits, pushes and opens the pull
request, which their defaults refuse or stop to ask about with nobody there to answer; the
agent's own Unix account is the boundary. An unattended agent needs its own permission
settings (Claude Code's permission mode, Codex's sandbox and approval policy); the worker
does not choose them for you, and the bare presets leave each agent's default in place.

**Claude Code, unattended.** What the first run (ledger item 1236, Claude Code 2.1.296 on
the Pop!_OS 24.04 worker, `run --once`) showed, and what is still to try:

- **Install** it as agent with Anthropic's native installer (`curl -fsSL
  https://claude.ai/install.sh | bash` under `sudo -iu agent`), not npm. It puts the binary
  under `~/.local/share/claude/versions/<version>` and the `claude` link in `~/.local/bin`,
  the directory the units already put on PATH; `claude --version` checks it.
- **Authenticate** in one of two ways, and never put the token in `.gonk/worker.toml`, the
  unit file or the repository:
  - *A login held by the account* (what the first run used): `claude auth login` once as
    agent, which stores it in `~/.claude/.credentials.json` (mode 600), the file the probe's
    `login:` reports by presence. `claude auth status` says which method is in use
    (`"authMethod": "claude.ai"`) without printing the secret.
  - *A long-lived token* (the documented route for unattended runs; it has run here, ledger
    item 1238 being that run): `claude setup-token` ("Set up a long-lived authentication
    token (requires Claude subscription)") prints a token with the line `export
    CLAUDE_CODE_OAUTH_TOKEN=<token>`. The CLI reads it from that environment variable, and
    the worker passes its own environment to the agent, so the token must be in the
    **worker's** environment. Put it in `~/.config/gonk-worker/claude.env`, a file of
    agent's at mode 600 that holds the one `CLAUDE_CODE_OAUTH_TOKEN=` line, and load it:
    - *By hand* (`worker.py run --once` from a login shell): a line in `~/.profile`,
      `set -a; [ -r ~/.config/gonk-worker/claude.env ] && . ~/.config/gonk-worker/claude.env; set +a`.
      This is the route that has run: the worker was started from a login shell, carried the
      variable and passed it to the agent.
    - *The systemd unit* (untested): a drop-in (`systemctl --user edit gonk-worker`) with
      `EnvironmentFile=%h/.config/gonk-worker/claude.env`.

    Such a token is limited to inference, which is all a worker needs. While the variable is
    set it wins over the stored login, so after the token works, `claude auth logout` removes
    the stored login (`~/.claude/.credentials.json` is then absent) and the token is the
    only credential, which is narrower: inference only, nothing else on the account.
    **A trap:** from inside the agent the token looks absent. Claude Code hides its own auth
    variable from the shell commands it runs, so there `claude auth status` says not logged
    in and `test -n "$CLAUDE_CODE_OAUTH_TOKEN"` says unset, while the agent is running on
    that very token. Do not diagnose a worker from inside an agent. The check is `claude
    auth status` in a login shell of the worker account.
- **Permissions:** `claude -p` alone keeps the default permission mode, which asks before
  edits and shell commands, and an unattended run has nobody to answer.
  `--permission-mode bypassPermissions` (or `--dangerously-skip-permissions`, "Bypass all
  permission checks") lets it edit, run the gates, commit, push and run `gh pr create`
  without asking. That is why `[agent]` sets a `command` rather than the bare `preset = "claude"`.
  The boundary is the one Codex's note names: agent's Unix account, with no sudo, no
  password and its own fine-grained GitHub token, and branch protection on the repository
  for the merge it is told not to do.

**What one item goes through:**

1. **Pull.** `next` with the labels, then the first ranked item that is unclaimed, carries
   exactly one `repo:<name>` label naming a configured repository, is not awaiting its
   dispatcher (`awaiting_dispatcher`: the ledger's `awaiting: dispatcher` state, or — on a
   ledger older than 0.8.0 — its newest comment being an `Evidence:` hand-back, with any later
   comment, such as the dispatcher saying why the evidence did not hold, making it ready
   again), was not handed back by this worker before (`.gonk/state/worker/attempts.json`;
   `worker.py forget <N>` allows it again), and whose repository has no active claim.
2. **One agent per repository.** It refuses an item whose repository has an open item with a
   live claim, held by anyone (an expired lease is a holder that stopped renewing, and does
   not fence it), and runs one item per repository on this machine (a lock file per
   repository). It re-reads after claiming and backs off if it then sees another claim.
3. **Claim and heartbeat.** The claim takes the lease; every `renew_every` the worker reads
   the item and renews. If the item is held by someone else, released, or closed, or the
   renewal is refused, the claim is lost: it **stops the agent**, comments that on the item,
   keeps the worktree, and **releases nothing** (a release would hand back someone else's
   claim).
4. **Workspace.** One clone per repository (`<work_root>/<repo>/.source`, fetched before each
   item), and a fresh worktree on a new branch `worker/item-<N>-<time>` from `origin/<base>`.
   A directory left by an earlier run is never reused: the next is `item-<N>.2`.
5. **Agent.** The prompt is a fixed preamble (AGENTS.md's rules: reproduce first; work only
   in this worktree; a pull request with green CI; never merge, never close; the evidence
   file when it is not a defect), then the item's title and body. Its output goes to
   `<work_root>/<repo>/item-<N>.log`, beside the worktree, inside hubdiag's view. When the
   budget runs out the worker stops its whole process group (SIGTERM, then SIGKILL after
   `grace`).
6. **Hand-back.** The pull requests on the branch (`gh pr list --head`), and their CI:
   `gh pr checks`, or, when the token may not read checks (a fine-grained token cannot), the
   Actions runs for the head's **full** sha (`gh run list --commit`; a short sha answers `[]`
   silently, so the worker never asks with one). The comment names which source answered.
   Then `Evidence: <PR URL>` and the details go on the item as a comment posted with
   `handback=true`, the claim is released, and the worktree is removed (unless `keep`).
   `handback=true` makes the hand-back a STATE of the item (ikigai-ledger 0.8.0, ledger item
   1199), so `next` stops offering it to the next worker; a ledger older than 0.8.0 ignores
   the argument it does not know and writes the comment, the `Evidence:` prefix being the
   hand-back there. With no pull request, the first line of the agent's report file (a URL) is
   the evidence; with neither, the item goes back with a note and no evidence. An overrun goes
   back the same way, naming the overrun.

`run --once` exits 0 when it handed an item back with evidence, 3 when there was nothing to
take, 4 when it took one and could not hand it back with evidence (lost, overrun, stopped,
nothing to show), 1 on an error, 2 on a configuration error. The loop (`run`) polls until the
stop file exists or it is signaled; on SIGTERM it stops the current agent and hands the item
back with a note first.

**As a service:** for a loop per agent, [`units/gonk-worker@.service`](units/gonk-worker@.service)
and the generated plists (above). For the single loop, [`units/gonk-worker.service`](units/gonk-worker.service) (Linux, a systemd
user unit of agent; agent lingers, so it runs with nobody logged in; its log is the journal)
and [`units/dev.ikigai-rs.gonk-worker.plist`](units/dev.ikigai-rs.gonk-worker.plist) (macOS, a
LaunchDaemon run as agent, since agent never logs in to the GUI; its log is
`/tmp/ikigai-gonk-worker.log`). Each sets PATH itself, because a unit reads no `.profile`:
`~/.cargo/bin` (ikigai) first, then `~/.local/bin` (the agents), then the system; on macOS
Homebrew's, whose `python3` is new enough. Install steps are in each file's header. The
plist has not run on a worker Mac yet; none exists.

**What it will not do:** close an item, merge a pull request, work in a person's checkout,
take an item without a `repo:` label, take one of a repository someone holds, or release a
claim that is no longer its own.

**What waits on the ledger:**

- **One agent per repository is best-effort** until the ledger enforces it: the worker reads
  the claims and then claims, so two workers can both read "free" at once. The re-read after
  claiming narrows that and does not close it.
- **The brief is the item's body** behind the worker's fixed preamble, until briefs are
  resources gonk serves. The item's comments (from the dispatcher and others, oldest
  first, excluding the worker's own hand-back comments) are included as a marked
  block, bounded in size.
- **The grant is wider than the worker.** Today a machine identity is enrolled with
  `<ledger>=write`, which can also close, relabel and file; the worker's code does none of
  those, but nothing in gonk stops it, and the agent runs as the same account and could use
  the same identity. The worker grant exists now (`ikigai-gonk client add <name> --cert
  client.crt --worker <ledger>`, per-verb ledger capabilities, no close), and `init` prints
  it for an unattended machine; enrolling a worker with it is still a step the operator does
  by hand.
- **Never merging is an instruction to the agent**, which holds a token that could merge.
  What enforces it is the repository's branch protection (required checks and reviews).

## Windows (WSL2)

A Windows machine is a worker through **WSL2**: a Linux distribution in a small VM, set up as
on Linux above, plus what WSL changes. Native Windows is not supported (the `ikigai` client has
no Windows build). What WSL changes:

- **systemd is off by default.** Without it there is no linger, no user unit and no journal, so
  the worker loop cannot run as a service. `/etc/wsl.conf` `[boot] systemd=true` turns it on;
  `worker-setup.sh` sets it, and the probe FAILs while systemd is not PID 1.
- **WSL stops when idle, and when Windows sleeps.** WSL shuts a distribution down after
  `instanceIdleTimeout` milliseconds of idleness (default 15000) unless `%UserProfile%\.wslconfig`
  sets it to `-1` under `[general]` (documented by Microsoft). `[wsl2] vmIdleTimeout` is the VM's
  own timeout; what `-1` does there is not documented, and the distribution's is the one that
  stops the worker. Windows sleeping stops everything, and nothing inside the VM can read
  Windows' power settings: the probe prints the commands and says it did not check them.
- **NAT hides the distribution from the LAN.** A hub cannot reach WSL's sshd until the
  networking is mirrored or a port is forwarded (step 4).
- **The Windows drives are open to every Linux account.** WSL mounts them under `/mnt` (DrvFs)
  for the whole distribution, and without the `metadata` mount option the Linux permissions
  there are invented (every file belongs to the default user, mode 0777) while a read is made
  with the Windows user's own credentials. So `hubdiag` and `agent` can read the Windows user's
  whole profile, and with interop on start a Windows program that runs as that user. The setup
  closes the drives with `/etc/wsl.conf` `[automount]`, and `--check` FAILs while either account
  can read any file of the profile.
- **Trees on the Windows drives are refused.** A view or the agent's work tree under `/mnt/` is
  refused by the setup: Linux permissions do not hold there, so neither can the boundary; it is
  slow; and Windows tools write CRLF into it.

### The steps, in order

On **Windows**, in PowerShell **as Administrator**:

1. Install WSL2 and Ubuntu, and confirm it is version 2 (`wsl --set-version Ubuntu-24.04 2` if not):

       wsl --install -d Ubuntu-24.04
       wsl --update
       wsl -l -v

2. Create `%UserProfile%\.wslconfig` (Notepad is fine) so WSL never shuts the distribution down
   when idle, and, on Windows 11 22H2 or later, mirrors Windows' network into it (step 4):

       [general]
       instanceIdleTimeout=-1

       [wsl2]
       networkingMode=mirrored

3. Keep Windows awake on wall power (`powercfg /change` takes minutes; 0 is the value the Power
   settings show as Never), and read the result back:

       powercfg /change standby-timeout-ac 0
       powercfg /change hibernate-timeout-ac 0
       powercfg /query

   On a laptop that will run closed, also set *Control Panel > Power Options > Choose what
   closing the lid does > When plugged in* to **Do nothing**.

4. **Let the hub reach sshd.** With mirrored networking (step 2), WSL's sshd listens on Windows'
   own addresses, and the Hyper-V firewall must let it in:

       New-NetFirewallHyperVRule -Name "WSL sshd" -DisplayName "WSL sshd" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 22

   If Windows' own OpenSSH Server is running it already holds port 22: stop it, or give WSL's
   sshd another port and put that `Port` in the hub's stanza. **Without mirrored networking**
   (older Windows), forward a Windows port to the distribution instead. Its address changes
   every time WSL starts, so the rule must be re-made after each restart:

       $ip = (wsl hostname -I).Trim().Split(' ')[0]
       netsh interface portproxy add v4tov4 listenport=2222 listenaddress=0.0.0.0 connectport=22 connectaddress=$ip
       New-NetFirewallRule -DisplayName "WSL sshd (portproxy)" -Direction Inbound -Protocol TCP -LocalPort 2222 -Action Allow
       netsh interface portproxy show v4tov4
       netsh interface portproxy delete v4tov4 listenport=2222 listenaddress=0.0.0.0   # to undo

   and the hub's stanza names the Windows machine with `Port 2222`. Nothing here runs these
   commands; they are Microsoft's
   ([WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking)), with this
   worker's ports filled in.

Then **inside the distribution** (`wsl -d Ubuntu-24.04`), as your own Linux user, the admin:

5. The packages, as in [step 1](#1-the-os) for Linux, and a clone of this repository **in the
   Linux filesystem** (`~/src/ikigai-gonk-starter`, never under `/mnt/c`).
6. The dry run, then the setup. On a fresh distribution the setup writes `/etc/wsl.conf`
   (`[boot] systemd=true`, and the `[automount]` restriction below) and **stops**, because
   systemd is not yet PID 1:

       ./worker/worker-setup.sh --dry-run --key hub.pub
       sudo ./worker/worker-setup.sh --key hub.pub

7. Restart WSL so it reads `/etc/wsl.conf`: from PowerShell `wsl --shutdown`, wait until
   `wsl --list --running` says nothing is running (Microsoft's "8 second rule"), and open the
   distribution again. `ps -p 1 -o comm=` should now say `systemd`.
8. The setup again (it now finishes), then the self-test:

       sudo ./worker/worker-setup.sh --key hub.pub
       sudo ./worker/worker-setup.sh --check

   On WSL, `--check` adds: `PASS wsl-systemd`, `PASS wsl-read` for hubdiag and for agent
   (nothing of the Windows profile opens), and, read as root, `INFO wsl-idle` with
   `instanceIdleTimeout=-1` (a `WARN` there means step 2 has not taken: `wsl --shutdown` once
   more after editing `.wslconfig`).
9. **The GPU** is the Windows NVIDIA driver's: install the driver on Windows, never a Linux
   driver inside WSL. Its `nvidia-smi` is `/usr/lib/wsl/lib/nvidia-smi`. Ollama is installed
   inside the distribution with the Linux installer ([step 5](#5-ollama-and-a-model-by-memory)).
10. From here the Linux steps apply unchanged: the GitHub token ([step 3](#3-the-github-token-as-agent)),
    ikigai and gonk ([step 6](#6-ikigai-and-gonk-as-agent)), the hub's stanza
    ([step 7](#7-reach-it-from-the-hub)) and the loop ([step 8](#8-the-worker-loop-as-agent)).
    The probe's `sleep` section still checks the Linux sleep targets; under WSL they are not
    what sleeps (Windows is), but masking them is harmless.

### The Windows drives: two ways to close them

`worker-setup.sh --wsl-automount restrict|off` writes one of these to `/etc/wsl.conf`; either
takes effect when WSL restarts, and either is undone by editing that line back.

| | `restrict` (the default) | `off` |
| --- | --- | --- |
| writes | `[automount]` `options="metadata,uid=<admin>,gid=<admin>,umask=077"` | `[automount]` `enabled=false` |
| the admin | keeps `/mnt/c`, as their own | loses `/mnt/c` until it is turned back on |
| hubdiag, agent | see the drives as the admin's files at 0700/0600: no entry, no read, no Windows program | see no drive at all |
| why | nothing the admin uses disappears | the smallest surface |

Options already in `options=` are kept (only `metadata`, `uid`, `gid` and `umask` are replaced).
Two things to know about `restrict`: with `metadata` on, a `chmod` or `chown` made from Linux on
a Windows file is stored in that file's NTFS extended attributes and outlives turning the option
off; and the restriction covers the drives WSL mounts automatically, not a drive mounted by hand
or through `/etc/fstab`. Interop (`[interop] enabled`) is left on: once the drives are closed,
hubdiag and agent can reach no Windows program to start, and `--check` fails if they can.
`[interop] enabled=false` closes that too, at the cost of `explorer.exe .` and friends for the
admin.

### Not yet confirmed on a real WSL machine

The tests run against a stub of a WSL machine (`--wsl-root`), so these rest on Microsoft's
documentation, not a run, and the first WSL worker should confirm each:

- that DrvFs with `metadata,uid=<admin>,umask=077` refuses hubdiag and agent at `/mnt/c` itself
  (the documentation says the masks apply with `metadata`; that the mount's root directory takes
  them too is expected, not seen), and that root can still read `.wslconfig` through it;
- that `instanceIdleTimeout=-1` keeps a distribution running with only systemd's services in it;
- that WSL starts at all after Windows boots without someone opening it. Nothing here starts it.
  A logon task is the likely answer (`schtasks /create /tn "WSL worker" /sc onlogon /tr
  "wsl.exe -d Ubuntu-24.04 --exec /bin/true"`), untried;
- mirrored networking with the Hyper-V firewall rule above, and the portproxy fallback;
- Ubuntu 24.04's socket-activated sshd under WSL's systemd, and `sshd -T` in `--check`.

## What this was learned on

The Linux half was learned setting up the first worker by hand, a Pop!_OS 24.04 laptop with a
6 GB RTX 3060, and is proved on GitHub's hosted Ubuntu runners. The macOS half was written from
Apple's man pages and is proved on GitHub's hosted macOS runners; no worker Mac has run it yet.
The WSL2 half was written from Microsoft's WSL documentation and is proved only against a stub
of a WSL machine; no WSL worker has run it yet (what that run must confirm is listed in its
section).

## Files

| file | runs | what |
| --- | --- | --- |
| [`worker-setup.sh`](worker-setup.sh) | on the worker, as root | setup (idempotent, `--dry-run`), and `--check` |
| [`probe.sh`](probe.sh) | as the probed account | what this account can reach; read-only by construction |
| [`../tools/worker.py`](../tools/worker.py) | on the worker, as agent | the worker loop (step 8); `init-agents` and `plist` for a loop per agent |
| [`../tools/worker_repos.py`](../tools/worker_repos.py) | on the worker, as agent | enrolls an owner's repositories in every loop's configuration; `--check-push` |
| [`worker.sample.toml`](worker.sample.toml) | copied to `.gonk/worker.toml`, or by `init-agents` to `.gonk/worker-<agent>.toml` | the loop's configuration, commented |
| [`units/gonk-worker@.service`](units/gonk-worker@.service) | as agent, by systemd | a loop per agent, `gonk-worker@<agent>` |
| [`units/gonk-worker.service`](units/gonk-worker.service), [`units/dev.ikigai-rs.gonk-worker.plist`](units/dev.ikigai-rs.gonk-worker.plist) | as agent, by systemd or launchd | the single loop as a service, with its PATH; the plist is also what `worker.py plist` generates each agent's daemon from |
| [`tests/probe-test.sh`](tests/probe-test.sh), [`tests/setup-test.sh`](tests/setup-test.sh) | anywhere, no root | the hermetic tests, run by `scripts/gates.sh` |
| [`tests/ci-linux.sh`](tests/ci-linux.sh), [`tests/ci-macos.sh`](tests/ci-macos.sh) | on throwaway CI runners only | the boundary, end to end |
