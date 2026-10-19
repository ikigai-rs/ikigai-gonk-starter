#!/bin/bash
# probe.sh: what THIS account can and cannot reach on a worker machine.
#
# It runs AS the account being probed: over ssh as hubdiag (the hub's check pipes this file to
# `bash -s`), or from root as hubdiag and as agent (`worker-setup.sh --check`).
# The worker needs no copy of this file for the hub-side run.
#
# ★ READ-ONLY, BY CONSTRUCTION. It opens files for READING; it asks access(2); and to prove a
# write is refused it opens an EXISTING file for APPEND and writes nothing. O_APPEND without
# O_TRUNC on a file that exists creates nothing, truncates nothing and changes no timestamp, so a
# boundary that is wrong is reported without the probe having used it. It never creates, writes,
# renames or deletes anything, and it never prints a credential (the gh section classifies a
# token by its prefix and drops it).
#
#   probe.sh [--sections LIST] [--closed DIR]... [--view DIR]... [--no-write DIR]...
#            [--forbid-group G]... [--expect-group G]... [--home DIR]... [--candidates]
#            [--windows-home DIR]... [--gh-bin PATH] [--journalctl-bin PATH] [--wsl-root DIR]
#
#   --sections      comma list from: identity,closed,view,nowrite,journal,linger,sleep,hw,tools,gh,
#                   agents,wsl,wsl-mnt,wslconfig (default: identity,closed,view,nowrite,journal)
#                   wsl        (WSL only) WSL2 with systemd as PID 1; the [automount] and interop
#                              state; what this probe cannot see (Windows' power settings)
#                   wsl-mnt    (WSL only) THIS account must not read the Windows user's profile
#                              through the drives WSL mounts (DrvFs, /mnt/c), nor start a Windows
#                              program. Run it as hubdiag and agent, never as the admin, who may.
#                   wslconfig  (WSL only) the Windows user's .wslconfig idle timeouts, read only if
#                              THIS account can read it: worker-setup.sh --check runs it as root,
#                              never as hubdiag or agent
#   --windows-home DIR  (WSL) a Windows profile as Linux sees it (/mnt/c/Users/<name>); default:
#                   every profile under <automount root>c/Users that this account can list
#   --closed DIR    a home this account must NOT reach: no traverse, no listing, and none of the
#                   guessable names inside it opens (the traverse leak: once a home is traversable,
#                   every world-readable file in it is readable by name, .bash_history first)
#   --view DIR      a read-only view this account MUST read and must NOT write
#   --no-write DIR  a tree this account must not write (another account's work, the admin's repos)
#   --forbid-group  a group this account must not be in (sudo, admin, wheel, staff, docker, …)
#   --expect-group  a group this account must be in (systemd-journal for hubdiag on Linux)
#   --linger USER   (Linux) USER must have lingering on, so its user units run with nobody logged in
#   --home DIR      for the tools section: also look under DIR/.cargo/bin, DIR/.deno/bin, …;
#                   for the agents section: the home whose claude, codex and hermes are reported
#   --candidates    read absolute paths from stdin, one per line, and FAIL on every one that
#                   opens. worker-setup.sh --check feeds it a root enumeration of the admin's and
#                   the agent's homes; that is the full form of the leak test. Without it the
#                   closed section tries a fixed list of guessable names, which is what an
#                   unprivileged caller (the hub over ssh) can do.
#   --gh-bin PATH        tests: the gh to classify a token with (default: the system's)
#   --journalctl-bin PATH  tests: the journalctl to read the sleep section's journal with
#                        (default: journalctl on PATH)
#   --wsl-root DIR  tests: read /proc, /etc/wsl.conf and the mounted Windows drives under DIR
#                   instead of /, and ignore WSL_DISTRO_NAME (so a stub decides alone)
#
# Output: one line per finding, `PASS|FAIL|WARN|INFO <check>: <detail>`, then a summary line.
# Exit: 0 no FAIL · 1 at least one FAIL · 2 usage.
#
# ⚠ bash 3.2: macOS's /bin/bash. No associative arrays, no mapfile, no ${x,,}.
set -u

sections="identity,closed,view,nowrite,journal"
closed=() views=() nowrite=() forbid=() expect=() homes=() lingers=() winhomes=()
candidates=0 ghbin="" jcbin="" wslroot=""
while [ $# -gt 0 ]; do
    case "$1" in
        --sections) sections="$2"; shift 2 ;;
        --closed) closed+=("$2"); shift 2 ;;
        --view) views+=("$2"); shift 2 ;;
        --no-write) nowrite+=("$2"); shift 2 ;;
        --forbid-group) forbid+=("$2"); shift 2 ;;
        --expect-group) expect+=("$2"); shift 2 ;;
        --home) homes+=("$2"); shift 2 ;;
        --linger) lingers+=("$2"); shift 2 ;;
        --candidates) candidates=1; shift ;;
        --gh-bin) ghbin="$2"; shift 2 ;;   # tests: a stub gh
        --journalctl-bin) jcbin="$2"; shift 2 ;;   # tests: a stub journalctl
        --windows-home) winhomes+=("$2"); shift 2 ;;
        --wsl-root) wslroot="${2%/}"; shift 2 ;;   # tests: a stub /proc, /etc and /mnt
        -h|--help) sed -n '2,55p' "$0" 2>/dev/null; exit 0 ;;
        *) echo "probe.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

fails=0 passes=0 warns=0
pass() { echo "PASS $1: $2"; passes=$((passes + 1)); }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }
warn() { echo "WARN $1: $2"; warns=$((warns + 1)); }
info() { echo "INFO $1: $2"; }
want() { case ",$sections," in *",$1,"*) return 0 ;; esac; return 1; }

os="$(uname -s)"
me="$(id -un)"

# Can this account open PATH for reading? A real open(2), not access(2): an ACL, a group, a
# mode bit or a bind mount all decide it the way they would for an attacker.
can_read()   { { : < "$1"; } 2>/dev/null; }
# Can it open an EXISTING file for append? Writes zero bytes; see the header.
can_append() { [ -f "$1" ] && { : >> "$1"; } 2>/dev/null; }
can_enter()  { ( cd "$1" ) 2>/dev/null; }
can_list()   { ls "$1" >/dev/null 2>&1; }
in_group()   { id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$1"; }

# Names an attacker guesses once a home is traversable. Credentials first, then shell history
# (the traverse leak), then config that names hosts and tokens.
GUESSES=".bash_history .zsh_history .python_history .lesshst .viminfo .psql_history
.mysql_history .node_repl_history .local/share/fish/fish_history .config/fish/fish_history
.netrc .git-credentials .gitconfig .config/git/credentials
.ssh/id_ed25519 .ssh/id_rsa .ssh/id_ecdsa .ssh/authorized_keys .ssh/known_hosts .ssh/config
.config/gh/hosts.yml .config/gh/config.yml .cargo/credentials.toml .cargo/credentials
.npmrc .pypirc .docker/config.json .aws/credentials .kube/config .config/ikigai/config.toml
.config/ikigai/gonk/grants.json .ikigai/gonk.sock .profile .bashrc .zshrc .zprofile"

# ── WSL ─────────────────────────────────────────────────────────────────────────────────────
# Read an INI value (wsl.conf, .wslconfig): file section key → the last value, trimmed, its
# quotes and a trailing comment dropped; sections and keys compared case-insensitively. A file
# saved on Windows may carry CRLF, a UTF-8 BOM, or be UTF-16 (NUL bytes), so CR and NUL are
# dropped and a BOM is skipped, all under the C locale (in a UTF-8 locale macOS's tr refuses a
# UTF-16 BOM as an illegal byte sequence and prints nothing). Exit 1 when the file or the key
# is absent.
ini_get() {
    [ -r "$1" ] || return 1
    LC_ALL=C tr -d '\000\r' < "$1" 2>/dev/null | LC_ALL=C awk -v s="$2" -v k="$3" '
        BEGIN { s = tolower(s); k = tolower(k); u8 = "\357\273\277"; u16 = "\377\376" }
        {
            line = $0
            if (NR == 1) { if (index(line, u8) == 1) line = substr(line, 4); if (index(line, u16) == 1) line = substr(line, 3) }
            sub(/^[ \t]+/, "", line); sub(/[ \t]+$/, "", line)
            if (line == "" || line ~ /^[#;]/) next
            if (line ~ /^\[.*\]$/) { sec = tolower(substr(line, 2, length(line) - 2)); gsub(/^[ \t]+|[ \t]+$/, "", sec); next }
            if (sec != s) next
            i = index(line, "="); if (i == 0) next
            key = substr(line, 1, i - 1); val = substr(line, i + 1)
            gsub(/^[ \t]+|[ \t]+$/, "", key)
            if (tolower(key) != k) next
            sub(/[ \t]+[#;].*$/, "", val); gsub(/^[ \t]+|[ \t]+$/, "", val)
            gsub(/^"|"$/, "", val)
            found = 1; out = val
        }
        END { if (found) print out; exit !found }'
}
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# Is this WSL, and which? wsl=0 not WSL, 1 WSL1, 2 WSL2; wsl_why says what said so. Three
# signals, any one is enough: interop's binfmt_misc entry (absent when [interop] is off, hence
# not alone), WSL_DISTRO_NAME (set for processes wsl.exe starts, not for an ssh login or
# runuser, hence not alone), and /proc/version, which names Microsoft on both WSL1 and WSL2.
# WSL2's kernel says "microsoft-standard-WSL2"; WSL1's is "4.4.0-<build>-Microsoft". A custom
# WSL2 kernel (.wslconfig kernel=) may name neither, so WSL without that word counts as WSL2.
R="$wslroot"
wsl=0 wsl_why=""
if [ "$os" = Linux ] || [ -n "$R" ]; then
    pv="$(cat "$R/proc/version" 2>/dev/null)"
    if [ -e "$R/proc/sys/fs/binfmt_misc/WSLInterop" ] || [ -e "$R/proc/sys/fs/binfmt_misc/WSLInterop-late" ]; then
        wsl_why="binfmt_misc has WSLInterop"
    fi
    if [ -z "$R" ] && [ -n "${WSL_DISTRO_NAME:-}" ]; then wsl_why="${wsl_why:+$wsl_why, }WSL_DISTRO_NAME=$WSL_DISTRO_NAME"; fi
    case "$pv" in *[Mm]icrosoft*) wsl_why="${wsl_why:+$wsl_why, }/proc/version names Microsoft" ;; esac
    if [ -n "$wsl_why" ]; then
        case "$pv" in *WSL2*|*microsoft-standard*) wsl=2 ;; *Microsoft*) wsl=1 ;; *) wsl=2 ;; esac
    fi
fi
wslconf="$R/etc/wsl.conf"
# Where WSL mounts the Windows drives: [automount] root, default /mnt/ (always with a slash).
mnt="$(ini_get "$wslconf" automount root 2>/dev/null)"; mnt="${mnt:-/mnt/}"
case "$mnt" in */) ;; *) mnt="$mnt/" ;; esac
# The Windows profiles to try: those named, then each under <root>c/Users this account can
# list (the accounts Windows makes for itself are not people's profiles).
windows_homes() {
    local d n
    for d in ${winhomes[@]+"${winhomes[@]}"}; do printf '%s\n' "$d"; done
    can_list "$R${mnt}c/Users" || return 0
    for d in "$R${mnt}c/Users"/*; do
        [ -d "$d" ] || continue
        n="$(basename "$d")"
        case "$n" in Public|Default|"Default User"|"All Users"|defaultuser0|WsiAccount) continue ;; esac
        printf '%s\n' "$d"
    done | awk '!seen[$0]++'
}
# Names worth trying in a Windows profile: keys and tokens first, then shell history and the
# WSL configuration itself. One per line (some hold spaces).
WIN_GUESSES=".ssh/id_ed25519
.ssh/id_rsa
.ssh/config
.gitconfig
.git-credentials
.aws/credentials
.wslconfig
AppData/Roaming/GitHub CLI/hosts.yml
AppData/Roaming/Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt
NTUSER.DAT"

if want identity; then
    info identity "$(id)"
    if [ "$(id -u)" = 0 ]; then fail identity "running as root: the probe must run AS the account it checks"; fi
    for g in ${forbid[@]+"${forbid[@]}"}; do
        if in_group "$g"; then fail group "$me is in group $g"; else pass group "$me is not in $g"; fi
    done
    for g in ${expect[@]+"${expect[@]}"}; do
        if in_group "$g"; then pass group "$me is in $g"; else fail group "$me is NOT in $g"; fi
    done
    # `sudo -n -l` lists what this account may run without running anything, and -n never
    # prompts. Exit 0 means a rule exists that needs no password: refused here. A rule that needs
    # a password is moot for an account that has none, but is still worth seeing.
    if command -v sudo >/dev/null 2>&1; then
        if out="$(sudo -n -l 2>&1)"; then
            fail sudo "sudo -n -l succeeded: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"
        else
            pass sudo "refused ($(printf '%s' "$out" | head -1 | cut -c1-80))"
        fi
    else
        pass sudo "no sudo on PATH"
    fi
fi

if want closed; then
    for d in ${closed[@]+"${closed[@]}"}; do
        if can_enter "$d"; then
            fail closed "$me can TRAVERSE $d (every world-readable file in it is readable by name)"
        elif [ -e "$d" ] || ! can_list "$(dirname "$d")" || ls -a "$(dirname "$d")" 2>/dev/null | grep -qxF "$(basename "$d")"; then
            # Exists but refused. (`-e` alone cannot tell: a macOS ACL that denies readattr makes
            # stat fail too, so the parent's listing is asked whether the name is there.)
            pass closed "$me cannot traverse $d"
        else
            # The parent is readable and the name is absent: nothing to protect yet.
            warn closed "$d does not exist"
        fi
        if can_list "$d"; then fail closed "$me can LIST $d"; fi
        leaked=""
        for g in $GUESSES; do
            if can_read "$d/$g"; then leaked="$leaked $g"; fi
        done
        if [ -n "$leaked" ]; then fail leak "$me can read in $d:$leaked"
        else pass leak "none of $(echo $GUESSES | wc -w | tr -d ' ') guessable names in $d opens"; fi
    done
    if [ "$candidates" = 1 ]; then
        n=0 opened=0 all="" last=$SECONDS
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            n=$((n + 1))
            # Progress, on STDERR only (stdout is what the hub parses and must not change): one
            # fixed line, "probe-progress: tried N", every PROBE_PROGRESS_SECS (default 5) seconds.
            if [ $((SECONDS - last)) -ge "${PROBE_PROGRESS_SECS:-5}" ]; then
                last=$SECONDS
                echo "probe-progress: tried $n" >&2
            fi
            if can_read "$f"; then
                opened=$((opened + 1))
                all="$all$f
"
            fi
        done
        if [ "$opened" -gt 0 ]; then
            # Twenty names, the ones that matter first: a home made by useradd holds hundreds of
            # skeleton files, and the history or the token must not be the 21st line.
            hot='history|hosts\.yml|credential|token|netrc|/\.ssh/|id_(rsa|ed25519|ecdsa)|\.pypirc|\.npmrc|config\.json|grants\.json'
            shown="$( { printf '%s' "$all" | grep -E "$hot"; printf '%s' "$all" | grep -vE "$hot"; } | head -20 | sed 's/^/       /')"
            fail leak "$me can open $opened of $n enumerated file(s), the most sensitive first:
$shown"
        else
            pass leak "$me can open none of $n enumerated file(s)"
        fi
    fi
fi

if want view; then
    for v in ${views[@]+"${views[@]}"}; do
        if ! can_list "$v"; then fail view "$me cannot read the view $v"; continue; fi
        pass view "$me reads $v ($(ls -A "$v" 2>/dev/null | wc -l | tr -d ' ') entries)"
        if [ -w "$v" ]; then fail view-write "$v is writable by $me (access W_OK)"; fi
        # Real kernel answers: an append-open of existing files a few levels down. A read-only
        # bind mount refuses with EROFS, a copy owned by another account with EACCES.
        tried=0 wrote=""
        for f in $(find "$v" -maxdepth 4 -type f 2>/dev/null | head -5); do
            tried=$((tried + 1))
            if can_append "$f"; then wrote="$wrote $f"; fi
        done
        if [ -n "$wrote" ]; then fail view-write "$me could open for writing:$wrote"
        elif [ "$tried" -gt 0 ]; then pass view-write "$me cannot write $v ($tried existing file(s) refused an append-open)"
        else warn view-write "$v holds no files to try a write against; only access(2) was asked"; fi
        # The view is for hubdiag, not for every local account: some directory on the way to it
        # must withhold "other" search. (Asked of the mode bits, since this account cannot
        # become another one to try.)
        open_to_all=1
        for d in "$(dirname "$v")" "$v"; do
            if [ "$os" = Darwin ]; then m="$(stat -f %Lp "$d" 2>/dev/null)"; else m="$(stat -c %a "$d" 2>/dev/null)"; fi
            case "$m" in *[1357]) ;; *) open_to_all=0 ;; esac
        done
        if [ "$open_to_all" = 1 ]; then
            warn view-scope "every local account can reach $v ($(dirname "$v") and $v both grant 'other' search)"
        else
            pass view-scope "$v is closed to accounts other than its owner and group"
        fi
        if [ -f "$v/.hubdiag-view-stamp" ]; then
            info view-age "$v refreshed $(cat "$v/.hubdiag-view-stamp" 2>/dev/null)"
        fi
        if [ "$os" = Linux ] && command -v findmnt >/dev/null 2>&1; then
            opts="$(findmnt -no OPTIONS --target "$v" 2>/dev/null | head -1)"
            case ",$opts," in
                *,ro,*) pass view-mount "$v is mounted read-only ($opts)" ;;
                *) fail view-mount "$v is not on a read-only mount (${opts:-no mount})" ;;
            esac
        fi
    done
fi

if want nowrite; then
    for d in ${nowrite[@]+"${nowrite[@]}"}; do
        if ! can_enter "$d"; then pass no-write "$me cannot reach $d"; continue; fi
        if [ -w "$d" ]; then fail no-write "$d is writable by $me"; continue; fi
        wrote=""
        for f in $(find "$d" -maxdepth 4 -type f 2>/dev/null | head -5); do
            if can_append "$f"; then wrote="$wrote $f"; fi
        done
        if [ -n "$wrote" ]; then fail no-write "$me could open for writing:$wrote"
        else pass no-write "$me reaches $d but cannot write it"; fi
    done
fi

if want journal; then
    if [ "$os" = Linux ]; then
        if command -v journalctl >/dev/null 2>&1 && [ -n "$(journalctl -q -n 1 --no-pager 2>/dev/null)" ]; then
            if [ -n "$(journalctl -q -n 1 --no-pager _UID=0 2>/dev/null)" ]; then
                pass journal "$me reads the system journal"
            else
                fail journal "$me reads only its own journal (not in systemd-journal?)"
            fi
        else
            fail journal "$me cannot read the journal"
        fi
    else
        # macOS has no journal group. Our launchd services log to /tmp/*.log (world-readable by
        # launchd's default umask), and that is what hubdiag reads; the unified log is reported.
        n="$(ls /tmp/ikigai-*.log 2>/dev/null | wc -l | tr -d ' ')"
        info journal "$n /tmp/ikigai-*.log file(s) visible"
        if log show --last 5s --style compact >/dev/null 2>&1; then
            info journal "log show works for $me"
        else
            info journal "log show is refused for $me (the unified log needs admin)"
        fi
    fi
fi

if want linger && [ "$os" = Linux ]; then
    for u in ${lingers[@]+"${lingers[@]}"}; do
        # The flag file is what logind reads at boot; /var/lib/systemd/linger is world-readable.
        if [ -e "/var/lib/systemd/linger/$u" ]; then pass linger "$u lingers"
        else fail linger "$u does not linger (sudo loginctl enable-linger $u)"; fi
    done
fi

# sleep: a worker that can suspend is a worker that silently stops. The first worker (a laptop,
# on AC, lid open) suspended at 15:06 on 2026-10-10 from COSMIC's idle auto-suspend, and nothing
# at the system level prevented it. A FAIL here, never a warning (a design decision, 2026-10-10).
#
# The masks stop the SLEEP, but not the ATTEMPT: a desktop's own idle-suspend (COSMIC's
# cosmic_idle, GNOME's gsd-power) still calls logind's Suspend, logind still broadcasts "The
# system will suspend now!" to every tty, and only then does it find suspend.target masked and
# refuse (2026-10-11: exactly that on the first worker, harmless but alarming to read). Masking
# the targets cannot silence it — logind's only pre-checks are the sleep.conf Allow* booleans
# and whether suspend is supported at all, and neither knows about a masked target. Turning the
# desktop's automatic suspend OFF is the fix; the count below is the INFO that says whether it
# has been done.
if want sleep; then
    if [ "$os" = Linux ]; then
        jc="${jcbin:-journalctl}"
        for t in sleep suspend hibernate hybrid-sleep suspend-then-hibernate; do
            s="$(systemctl is-enabled "$t.target" 2>/dev/null)"
            case "$s" in
                masked|masked-runtime) pass sleep "$t.target is $s" ;;
                *) fail sleep "$t.target is ${s:-unknown}: the machine can $t (sudo systemctl mask $t.target)" ;;
            esac
        done
        for k in HandleLidSwitch HandleLidSwitchExternalPower HandleLidSwitchDocked; do
            if raw="$(busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager "$k" 2>/dev/null)"; then
                v="$(printf '%s' "$raw" | sed 's/^s //; s/"//g')"
                info lid "$k=${v:-(unset: follows HandleLidSwitch)}"
            else
                info lid "$k=unknown (logind did not answer)"
            fi
        done
        bat="" ac=""
        for p in /sys/class/power_supply/*; do
            [ -r "$p/type" ] || continue
            case "$(cat "$p/type" 2>/dev/null)" in
                Battery) bat="$bat $(basename "$p")($(cat "$p/status" 2>/dev/null))" ;;
                Mains) ac="$ac $(basename "$p")=$( [ "$(cat "$p/online" 2>/dev/null)" = 1 ] && echo online || echo offline)" ;;
            esac
        done
        info power "battery:${bat:- none} · AC:${ac:- none reported}"
        n="$("$jc" -b -q --no-pager -k -g 'PM: suspend entry' 2>/dev/null | wc -l | tr -d ' ')"
        last="$("$jc" -b -q --no-pager -k -g 'PM: suspend entry' -o short-iso 2>/dev/null | tail -1 | cut -c1-25)"
        info suspends "$n in this boot${last:+ (last: $last)}"
        # The refusals: logind's "Unit suspend.target is masked, refusing operation." (a start of
        # a masked target), and, if the desktop instead called a sleep verb directly, logind's own
        # "Failed to execute suspend operation: …" or its "Requested suspend operation not
        # supported, ignoring." Both mean a desktop tried and the mask said no. INFO, not FAIL:
        # nothing was suspended, and this is exactly what a correctly masked machine looks like
        # until the desktop's automatic suspend is turned off too.
        refused="$("$jc" -b -q --no-pager -g 'Unit (sleep|suspend|hibernate|hybrid-sleep|suspend-then-hibernate)\.target is .*, refusing operation|Failed to execute (suspend|hibernate|hybrid-sleep|suspend-then-hibernate) operation|Requested (suspend|hibernate|hybrid-sleep|suspend-then-hibernate) operation not supported' 2>/dev/null | wc -l | tr -d ' ')"
        if [ "${refused:-0}" -gt 0 ]; then
            info suspend-attempts "$refused refused in this boot: the desktop's automatic suspend is still on. A sleep call was made and the mask refused it — no sleep, but a logind wall broadcast to every tty first. Turn the desktop's own automatic suspend off: COSMIC Settings > Power & Battery > Automatic suspend > Never; GNOME: gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'"
        else
            info suspend-attempts "none refused in this boot"
        fi
    else
        # pmset(1): "-c" is the charger (wall power) profile; "sleep 0" disables system sleep.
        acsleep="$(pmset -g custom 2>/dev/null | awk '/^AC Power:/ {ac=1; next} /^[A-Za-z].*:$/ {ac=0} ac && $1 == "sleep" {print $2; exit}')"
        disabled="$(pmset -g 2>/dev/null | awk '$1 == "SleepDisabled" {print $2}')"
        if [ "$disabled" = 1 ]; then
            pass sleep "SleepDisabled 1 (pmset disablesleep): the system does not sleep"
        elif [ "$acsleep" = 0 ]; then
            pass sleep "AC Power: sleep 0 (never)"
        else
            fail sleep "AC Power: sleep ${acsleep:-unknown} minutes: the machine idles to sleep (sudo pmset -c sleep 0 disksleep 0)"
        fi
        info power "$(pmset -g batt 2>/dev/null | head -1)"
        boot="$(sysctl -n kern.boottime 2>/dev/null | sed 's/^{ sec = \([0-9]*\).*/\1/')"
        since="$(date -r "${boot:-0}" '+%Y-%m-%d %H:%M:%S' 2>/dev/null)"
        n="$(pmset -g log 2>/dev/null | awk -v s="$since" '($1 " " $2) >= s && $4 == "Sleep" {c++} END {print c+0}')"
        info suspends "$n sleep event(s) since boot ($since)"
    fi
fi

if want hw; then
    info os "$(uname -srm)"
    if [ "$os" = Linux ]; then
        [ -r /etc/os-release ] && info distro "$(. /etc/os-release; echo "${PRETTY_NAME:-$NAME}")"
        info cpu "$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/.*: //') ($(nproc 2>/dev/null) threads)"
        info ram "$(awk '/MemTotal/ {printf "%.0f GB", $2/1048576}' /proc/meminfo 2>/dev/null)"
        if command -v nvidia-smi >/dev/null 2>&1; then
            info gpu "$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)"
        else
            info gpu "no nvidia-smi"
        fi
    else
        info cpu "$(sysctl -n machdep.cpu.brand_string 2>/dev/null) ($(sysctl -n hw.ncpu 2>/dev/null) cores)"
        info ram "$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 )) GB (unified: the GPU's memory too)"
        info gpu "$(system_profiler SPDisplaysDataType 2>/dev/null | awk -F': ' '/Chipset Model|Total Number of Cores/ {printf "%s ", $2}')"
    fi
    info disk "$(df -h / 2>/dev/null | awk 'NR==2 {print $2 " total, " $4 " free on /"}')"
fi

# tools: by ABSOLUTE PATH, so the answer does not depend on whose PATH the probe inherited.
# `ikigai`, `ikigai-gonk`, `cargo` and friends live in an account's own home: --home adds those.
if want tools; then
    tool() { # name candidate…
        local name="$1" c found=""; shift
        for c in "$@"; do
            # shellcheck disable=SC2086
            for g in $c; do
                if [ -x "$g" ] || { [ "$name" = libclang ] && [ -r "$g" ]; }; then found="$g"; break 2; fi
            done
        done
        if [ -n "$found" ]; then
            v=""
            case "$name" in
                libclang) ;;
                go) v="$("$found" version 2>/dev/null | head -1)" ;;
                *) v="$("$found" --version 2>/dev/null | head -1)" ;;
            esac
            info "tool $name" "$found${v:+  ($v)}"
        else
            info "tool $name" "absent"
        fi
    }
    hb=""; hc=""; hd=""
    for h in ${homes[@]+"${homes[@]}"}; do hb="$hb $h/.cargo/bin"; hd="$hd $h/.deno/bin"; hc="$hc $h/go/bin"; done
    cands() { local n="$1" d out=""; shift; for d in "$@"; do out="$out $d/$n"; done; echo "$out"; }
    sys="/usr/local/bin /usr/bin /opt/homebrew/bin"
    tool cargo $(cands cargo $hb)
    tool rustc $(cands rustc $hb)
    tool just $(cands just $hb $sys)
    tool git $(cands git $sys)
    tool clang $(cands clang $sys) /opt/homebrew/opt/llvm/bin/clang /Library/Developer/CommandLineTools/usr/bin/clang
    tool libclang '/usr/lib/llvm-*/lib/libclang.so*' '/usr/lib/x86_64-linux-gnu/libclang*.so*' \
        '/usr/lib/aarch64-linux-gnu/libclang*.so*' /usr/lib64/libclang.so \
        /Library/Developer/CommandLineTools/usr/lib/libclang.dylib /opt/homebrew/opt/llvm/lib/libclang.dylib
    tool gh $(cands gh $sys)
    tool ikigai $(cands ikigai $hb)
    tool ikigai-gonk $(cands ikigai-gonk $hb)
    tool ollama $(cands ollama $sys) /Applications/Ollama.app/Contents/Resources/ollama
    tool deno $(cands deno $hd $hb $sys)
    tool go /usr/local/go/bin/go $(cands go $sys) /usr/lib/go/bin/go $(cands go $hc)
    # Under WSL the GPU is the Windows NVIDIA driver's, and its nvidia-smi is in /usr/lib/wsl/lib.
    tool nvidia-smi /usr/bin/nvidia-smi /usr/lib/wsl/lib/nvidia-smi
    # Ollama answers any local account over HTTP, so its models are visible to whoever asks.
    if tags="$(curl -s -m 3 http://127.0.0.1:11434/api/tags 2>/dev/null)" && [ -n "$tags" ]; then
        info ollama "models: $(printf '%s' "$tags" | grep -o '"name":"[^"]*"' | sed 's/"name":"//; s/"$//' | tr '\n' ' ')"
    else
        info ollama "no server answering on 127.0.0.1:11434"
    fi
fi

# agents: the coding agents in THIS account's home: claude, codex and hermes, by
# absolute path, with `--version` (bounded: a version that hangs must not hang the check), and
# WHICH ACCOUNT HOLDS A LOGIN, by the PRESENCE of each agent's credential file and nothing
# else: no credential file is opened, read or printed. For hermes, every profile (the default
# at ~/.hermes, each named one under ~/.hermes/profiles/) with the provider and model from its
# config.yaml, and only those two keys: config.yaml can hold an api_key, so nothing else in it
# is printed. The profiles are listed from the directory rather than `hermes profile list`, so
# nothing of hermes's runs here but --version. `--version` is the one thing each agent is asked,
# as the tools section asks its tools, bounded at 15 s.
if want agents; then
    bounded() { perl -e 'alarm shift; exec @ARGV or exit 127' 15 "$@"; }
    for h in ${homes[@]+"${homes[@]}"}; do
        # npm's global prefix, if this account set one: the `prefix` key of its ~/.npmrc, read
        # as text (that file can hold registry tokens, so only this key is looked at). npm itself
        # is not run: it writes a log under ~/.npm for every command.
        npmbin=""
        if [ -r "$h/.npmrc" ]; then
            npmbin="$(awk -F= '/^[[:space:]]*prefix[[:space:]]*=/ {v = $2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); print v; exit}' "$h/.npmrc" 2>/dev/null)"
            case "$npmbin" in "~/"*) npmbin="$h/${npmbin#\~/}" ;; esac
            [ -n "$npmbin" ] && npmbin="$npmbin/bin"
        fi
        agent_bin() { # name → the first executable among the usual install places
            local c
            for c in "$h/.local/bin/$1" "$h/.claude/local/$1" "$h/.npm-global/bin/$1" ${npmbin:+"$npmbin/$1"} \
                     $h/.nvm/versions/node/*/bin/$1 "$h/.cargo/bin/$1" "$h/.hermes/hermes-agent/venv/bin/$1" \
                     "/usr/local/bin/$1" "/opt/homebrew/bin/$1" "/usr/bin/$1"; do
                [ -x "$c" ] && { echo "$c"; return 0; }
            done
            return 1
        }
        present() { # file… → which of them exist (never opened), or "no credential file"
            local f found=""
            for f in "$@"; do [ -e "$f" ] && found="$found ~/${f#"$h"/}"; done
            if [ -n "$found" ]; then echo "login: credential file present (${found# })"; else echo "login: no credential file"; fi
        }
        for a in claude codex hermes; do
            if ! bin="$(agent_bin "$a")"; then info "ai-agent $a" "absent"; continue; fi
            v="$(HOME="$h" bounded "$bin" --version 2>/dev/null | head -1)"
            case "$a" in
                claude)
                    login="$(present "$h/.claude/.credentials.json")"
                    [ "$os" = Darwin ] && [ "$login" = "login: no credential file" ] \
                        && login="login: no credential file (macOS keeps Claude Code's login in the Keychain; not probed)" ;;
                codex) login="$(present "$h/.codex/auth.json")" ;;
                hermes) login="$(present "$h/.hermes/auth.json" "$h/.hermes/.env")" ;;
            esac
            info "ai-agent $a" "$bin${v:+  ($v)} · $login"
        done
        if [ -d "$h/.hermes" ]; then
            active="$(head -1 "$h/.hermes/active_profile" 2>/dev/null)"
            for cfg in "$h/.hermes/config.yaml" "$h"/.hermes/profiles/*/config.yaml; do
                [ -f "$cfg" ] || continue
                d="$(dirname "$cfg")"
                if [ "$d" = "$h/.hermes" ]; then pname=default; else pname="$(basename "$d")"; fi
                # model: either a mapping (provider, default|model) or a bare string
                pm="$(awk '
                    /^model:[[:space:]]*$/ { m = 1; next }
                    /^model:[[:space:]]*[^[:space:]#]/ { v = $0; sub(/^model:[[:space:]]*/, "", v); model = v; next }
                    m && /^[^[:space:]#]/ { m = 0 }
                    m && /^[[:space:]]+(provider|default|model):/ {
                        k = $1; sub(/:$/, "", k); v = $0; sub(/^[^:]*:[[:space:]]*/, "", v)
                        if (k == "provider") provider = v; else if (model == "") model = v
                    }
                    END {
                        gsub(/["\047]/, "", provider); gsub(/["\047]/, "", model)
                        sub(/[[:space:]]+#.*/, "", provider); sub(/[[:space:]]+#.*/, "", model)
                        printf "provider %s, model %s", (provider == "" ? "(unset: auto)" : provider), (model == "" ? "(unset)" : model)
                    }' "$cfg" 2>/dev/null)"
                [ -n "$pm" ] || pm="config.yaml unreadable"
                login="$(present "$d/auth.json" "$d/.env")"
                mark=""; { [ "$pname" = "${active:-default}" ] && mark=" (active)"; }
                info "ai-agent hermes-profile $pname$mark" "$pm · $login"
            done
        fi
    done
fi

# gh: does THIS account hold a token, and what kind? The prefix is the whole answer: GitHub's
# token formats are self-identifying (github_pat_ fine-grained, ghp_ classic, gho_ OAuth from
# `gh auth login`'s browser flow, ghu_/ghs_ app tokens). The token is held in a variable for
# one comparison and unset; nothing but the class is printed.
if want gh; then
    [ -n "$ghbin" ] || for c in /usr/bin/gh /usr/local/bin/gh /opt/homebrew/bin/gh; do [ -x "$c" ] && { ghbin="$c"; break; }; done
    if [ -z "$ghbin" ]; then
        info gh-token "$me: no gh installed"
    else
        # The worker reads a pull request's CI with `gh pr checks --json`, which gh 2.50.0 added
        # (its release notes: "add json output for `gh pr checks`", cli/cli#9079). An older gh
        # refuses the flag and the worker falls back to the Actions runs for the head's full sha,
        # the less exact path. Ubuntu 24.04's own apt package is 2.45.0; GitHub's apt repository
        # is current.
        gv="$("$ghbin" --version 2>/dev/null | head -1)"
        ver="$(printf '%s' "$gv" | sed -n 's/^gh version \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')"
        if [ -z "$ver" ]; then
            warn gh-version "$ghbin: version not recognized (${gv:-no answer to --version}); \`gh pr checks --json\` needs gh 2.50.0 or later"
        elif [ "${ver%%.*}" -gt 2 ] || { [ "${ver%%.*}" -eq 2 ] && [ "$(printf '%s' "$ver" | cut -d. -f2)" -ge 50 ]; }; then
            info gh-version "$ghbin is gh $ver (\`gh pr checks --json\` needs 2.50.0 or later)"
        else
            if [ "$os" = Darwin ]; then
                how="brew upgrade gh"
            elif command -v dpkg >/dev/null 2>&1; then
                # GitHub's apt repository (cli/cli docs/install_linux.md), printed, never run.
                how='sudo mkdir -p -m 755 /etc/apt/keyrings && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null && sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null && sudo apt update && sudo apt install gh'
            else
                how="see https://github.com/cli/cli#installation"
            fi
            warn gh-version "$ghbin is gh $ver, older than 2.50.0, which added \`gh pr checks --json\`: the worker falls back to the Actions runs for the head's full sha. Upgrade: $how"
        fi
        t="$("$ghbin" auth token 2>/dev/null)"
        case "$t" in
            github_pat_*) info gh-token "$me holds a FINE-GRAINED token (prefix github_pat_)" ;;
            ghp_*) info gh-token "$me holds a CLASSIC token (prefix ghp_): broader than a worker needs" ;;
            gho_*) info gh-token "$me holds an OAUTH token (prefix gho_, gh's browser login)" ;;
            ghu_*|ghs_*) info gh-token "$me holds a GitHub App token (prefix ${t%%_*}_)" ;;
            "") info gh-token "$me holds no gh token" ;;
            *) info gh-token "$me holds a token of unrecognized form" ;;
        esac
        unset t
    fi
fi

# wsl: the machine under WSL. Without systemd as PID 1 there is no linger, no user unit and no
# journal, so the worker loop cannot run as a service and the sleep checks mean nothing: a FAIL.
# What decides whether the worker stays up is on WINDOWS (WSL's idle shutdown, Windows' own
# sleep), and Windows' power settings are invisible from inside the VM: said, not guessed.
if want wsl; then
    if [ "$wsl" = 0 ]; then
        info wsl "not WSL ($os)"
    else
        info wsl "WSL$wsl ($wsl_why)"
        if [ "$wsl" = 1 ]; then
            fail wsl "WSL1 has no systemd and no VM of its own: a worker needs WSL2 (from PowerShell: wsl --set-version <distro> 2)"
        fi
        pid1="$(cat "$R/proc/1/comm" 2>/dev/null)"
        sd="$(lower "$(ini_get "$wslconf" boot systemd)")"
        if [ "$pid1" = systemd ]; then
            pass wsl-systemd "systemd is PID 1"
        elif [ "$sd" = true ]; then
            fail wsl-systemd "systemd is not PID 1 (PID 1 is ${pid1:-unknown}) although /etc/wsl.conf sets [boot] systemd=true: WSL has not restarted since. From PowerShell: wsl --shutdown, then start the distribution again"
        else
            fail wsl-systemd "systemd is not PID 1 (PID 1 is ${pid1:-unknown}): no linger, no user units, no journal. Add to /etc/wsl.conf the two lines  [boot]  systemd=true  (worker-setup.sh adds them), then from PowerShell: wsl --shutdown"
        fi
        am="$(ini_get "$wslconf" automount enabled)"; ao="$(ini_get "$wslconf" automount options)"
        info wsl-automount "enabled=${am:-true (default)} root=$mnt options=${ao:-(none)}"
        ib="$R/proc/sys/fs/binfmt_misc/WSLInterop"; [ -e "$ib" ] || ib="$R/proc/sys/fs/binfmt_misc/WSLInterop-late"
        if [ -r "$ib" ]; then info wsl-interop "$(head -1 "$ib" 2>/dev/null) (binfmt_misc $(basename "$ib"); [interop] enabled=$(ini_get "$wslconf" interop enabled || echo 'true (default)'))"
        else info wsl-interop "off (no WSLInterop entry in binfmt_misc)"; fi
        info wsl-power "Windows sleeping stops this VM, and its power settings cannot be read from inside it: NOT checked here. On Windows, as Administrator: powercfg /change standby-timeout-ac 0 ; powercfg /change hibernate-timeout-ac 0 ; then powercfg /query to read them back (a laptop also needs its lid action set to Do nothing)"
    fi
fi

# wsl-mnt: the boundary WSL adds. WSL mounts the Windows drives (DrvFs) for EVERY account in the
# distribution, and without the metadata mount option the Linux permissions there are made up
# (every file 0777 for the default user), while a read is done with the Windows user's own token.
# So hubdiag and agent can read the Windows user's whole profile, and, with interop on, start a
# Windows program that runs AS that user. A FAIL on any read, a listing, or a program it may
# start. Run as hubdiag and agent; the admin may of course read their own profile.
if want wsl-mnt && [ "$wsl" != 0 ]; then
    whs="$(windows_homes)"
    if [ ! -e "$R${mnt}c" ]; then
        pass wsl-mnt "no Windows drive at ${mnt}c ([automount] enabled=false, or another root)"
    elif [ -z "$whs" ]; then
        pass wsl-mnt "$me cannot list ${mnt}c/Users, and no --windows-home was named"
    fi
    while IFS= read -r wh; do
        [ -n "$wh" ] || continue
        if can_list "$wh"; then fail wsl-mnt "$me can LIST the Windows profile $wh"; fi
        tried=0 opened=""
        list="$( { printf '%s\n' "$WIN_GUESSES" | awk -v p="$wh/" '{ print p $0 }'; find "$wh" -maxdepth 2 -type f 2>/dev/null | head -20; } | awk '!seen[$0]++')"
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            tried=$((tried + 1))
            if can_read "$f"; then opened="$opened
       $f"; fi
        done <<EOF
$list
EOF
        if [ -n "$opened" ]; then
            fail wsl-read "$me can read the Windows user's files through DrvFs ($wh):$opened
       Fix: sudo worker-setup.sh sets /etc/wsl.conf [automount] options=metadata,uid=<admin>,gid=<admin>,umask=077 (or --wsl-automount off), then from PowerShell: wsl --shutdown"
        else
            pass wsl-read "$me can read none of $tried file(s) tried in $wh"
        fi
    done <<EOF
$whs
EOF
    for exe in "$R${mnt}c/Windows/System32/cmd.exe" "$R${mnt}c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"; do
        [ -x "$exe" ] || continue
        if [ -e "$R/proc/sys/fs/binfmt_misc/WSLInterop" ] || [ -e "$R/proc/sys/fs/binfmt_misc/WSLInterop-late" ]; then
            fail wsl-exec "$me can start $exe, which runs as the Windows user (interop on): the same [automount] fix closes it, or [interop] enabled=false"
        else
            info wsl-exec "$me can reach $exe, but interop is off, so it cannot start it"
        fi
    done
fi

# wslconfig: does WSL itself shut the distribution down when idle? %UserProfile%\.wslconfig is
# Windows' file, read here only when THIS account can read it: worker-setup.sh --check runs it
# as root. Never as hubdiag or agent: if they could read it, wsl-mnt has already failed. WARN,
# not FAIL: an unreadable .wslconfig is what a correctly closed /mnt/c looks like to most
# accounts, and the half that matters as much (Windows' own sleep) is invisible here anyway.
if want wslconfig && [ "$wsl" != 0 ]; then
    seen=0 whs="$(windows_homes)"
    while IFS= read -r wh; do
        [ -n "$wh" ] || continue
        f="$wh/.wslconfig"
        if ! can_read "$f"; then
            if [ -e "$f" ] || ! can_list "$wh"; then continue; fi
            seen=1
            warn wsl-idle "$wh has no .wslconfig: WSL's defaults apply, and a distribution is shut down when idle (instanceIdleTimeout 15000 ms), the worker with it. Create it on Windows with  [general]  instanceIdleTimeout=-1"
            continue
        fi
        seen=1
        it="$(ini_get "$f" general instanceIdleTimeout)"; vt="$(ini_get "$f" wsl2 vmIdleTimeout)"; nm="$(ini_get "$f" wsl2 networkingMode)"
        if [ "$it" = -1 ]; then
            info wsl-idle "$f: [general] instanceIdleTimeout=-1 (documented: WSL does not shut the distribution down for idleness)"
        else
            warn wsl-idle "$f: [general] instanceIdleTimeout=${it:-unset (default 15000 ms)}: WSL shuts an idle distribution down, the worker with it. Set instanceIdleTimeout=-1 under [general]"
        fi
        info wsl-idle "$f: [wsl2] vmIdleTimeout=${vt:-unset (default 60000 ms)} (the VM's own idle timeout; what -1 does there is undocumented, and the distribution's timeout is the one that matters)"
        [ -n "$nm" ] || nm="unset (NAT: the LAN cannot reach sshd in this distribution)"
        info wsl-network "$f: [wsl2] networkingMode=$nm"
    done <<EOF
$whs
EOF
    if [ "$seen" = 0 ]; then
        warn wsl-idle "no .wslconfig readable to $me (under ${mnt}c/Users): cannot tell whether WSL shuts this distribution down when idle. Check %UserProfile%\\.wslconfig on Windows for [general] instanceIdleTimeout=-1"
    fi
fi

echo "summary: $passes pass, $fails fail, $warns warn (as $me on $(hostname -s 2>/dev/null || hostname))"
[ "$fails" -eq 0 ]
