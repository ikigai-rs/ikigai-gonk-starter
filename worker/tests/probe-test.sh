#!/bin/bash
# Hermetic test for probe.sh: the boundary probe, red and green.
#
# The real thing runs AS another account (hubdiag, agent), which a test without root cannot be.
# So the fixture makes the CURRENT user the outsider by its own mode bits: a fixture "home" at
# 0000 cannot be entered even by its owner, and one at 0755 can, which is the traverse-leak case
# (traverse granted, files inside world-readable). The probe asks the kernel each time (open,
# append-open, cd), so what is tested is the probe's logic against real permission answers.
# The end-to-end form, as real accounts with the real setup, is ci-linux.sh and ci-macos.sh
# beside this file, in CI.
#
# Run: worker/tests/probe-test.sh   (exit 0 = all pass)
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
probe="$here/probe.sh"
sb="$(mktemp -d)"
trap 'chmod -R u+rwx "$sb" 2>/dev/null; rm -rf "$sb"' EXIT

pass=0 fail=0
has()   { if printf '%s' "$3" | grep -qF -- "$2"; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1: no '$2' in:"; printf '%s\n' "$3" | sed 's/^/         /'; fail=$((fail+1)); fi; }
hasnt() { if printf '%s' "$3" | grep -qF -- "$2"; then echo "  FAIL $1: unexpected '$2'"; fail=$((fail+1)); else echo "  ok   $1"; pass=$((pass+1)); fi; }
expect_exit() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1: expected exit $2, got $3"; fail=$((fail+1)); fi; }
mtime() { if [ "$(uname -s)" = Darwin ]; then stat -f %m "$1"; else stat -c %Y "$1"; fi; }
size()  { wc -c < "$1" | tr -d ' '; }

home="$sb/home"; view="$sb/view"; tree="$sb/tree"
mkdir -p "$home/.ssh" "$home/.config/gh" "$view/repo/.git" "$tree/item-1"
printf 'secret history\n' > "$home/.bash_history"
printf 'oauth_token: x\n' > "$home/.config/gh/hosts.yml"
printf '# view\n' > "$view/repo/README.md"; printf 'ref: refs/heads/main\n' > "$view/repo/.git/HEAD"
printf 'work\n' > "$tree/item-1/README.md"

echo "── the traverse leak: a traversable home with world-readable files in it"
chmod 0755 "$home"
out="$(bash "$probe" --sections closed --closed "$home" 2>&1)"; st=$?
expect_exit "red" 1 "$st"
has "names the traverse" "FAIL closed: $(id -un) can TRAVERSE $home" "$out"
has "finds .bash_history by guessing" ".bash_history" "$(printf '%s' "$out" | grep '^FAIL leak')"
has "and gh's hosts.yml" ".config/gh/hosts.yml" "$(printf '%s' "$out" | grep '^FAIL leak')"
out="$(printf '%s\n' "$home/.bash_history" "$home/nonexistent" | bash "$probe" --sections closed --closed "$home" --candidates 2>&1)"
has "enumerated candidates: the one that opens is named" "can open 1 of 2 enumerated file(s)" "$out"
has "with its path" "$home/.bash_history" "$out"

echo "── the fix: the home closed"
chmod 0000 "$home"
out="$(printf '%s\n' "$home/.bash_history" "$home/.config/gh/hosts.yml" | bash "$probe" --sections closed --closed "$home" --candidates 2>&1)"; st=$?
expect_exit "green" 0 "$st"
has "cannot traverse" "PASS closed: $(id -un) cannot traverse $home" "$out"
has "no guess opens" "guessable names in $home opens" "$out"
has "no candidate opens" "can open none of 2 enumerated file(s)" "$out"
chmod 0755 "$home"

echo "── an absent home is a warning, not a pass and not a failure"
out="$(bash "$probe" --sections closed --closed "$sb/nobody" 2>&1)"; st=$?
expect_exit "exit 0" 0 "$st"
has "warns" "WARN closed: $sb/nobody does not exist" "$out"

echo "── a view: readable, and every write refused"
chmod -R a-w "$view"
before="$(mtime "$view/repo/README.md") $(size "$view/repo/README.md")"
listing="$(find "$view" | sort)"
out="$(bash "$probe" --sections view --view "$view" 2>&1)"; st=$?
case "$(uname -s)" in Darwin) expect_exit "green on a read-only view" 0 "$st" ;; esac
has "reads it" "PASS view: $(id -un) reads $view" "$out"
has "append-open refused" "PASS view-write: $(id -un) cannot write $view" "$out"
after="$(mtime "$view/repo/README.md") $(size "$view/repo/README.md")"
[ "$before" = "$after" ] && { echo "  ok   the probe changed no file (mtime and size)"; pass=$((pass+1)); } \
    || { echo "  FAIL the probe changed a file: $before → $after"; fail=$((fail+1)); }
chmod -R u+w "$view"
out="$(bash "$probe" --sections view --view "$view" 2>&1)"; st=$?
expect_exit "a writable view is red" 1 "$st"
has "names it" "FAIL view-write" "$out"
after2="$(mtime "$view/repo/README.md") $(size "$view/repo/README.md")"
[ "$before" = "$after2" ] && { echo "  ok   even a WRITABLE file is left unchanged (append-open, zero bytes)"; pass=$((pass+1)); } \
    || { echo "  FAIL the append-open changed a writable file: $before → $after2"; fail=$((fail+1)); }
[ "$(find "$view" | sort)" = "$listing" ] && { echo "  ok   nothing created in the view"; pass=$((pass+1)); } \
    || { echo "  FAIL the view's file list changed"; fail=$((fail+1)); }

echo "── view scope: who else can reach it"
chmod 0755 "$sb" "$view"
out="$(bash "$probe" --sections view --view "$view" 2>&1)"
has "open to all is a warning" "WARN view-scope" "$out"
chmod 0750 "$view"
out="$(bash "$probe" --sections view --view "$view" 2>&1)"
has "a 0750 view is closed" "PASS view-scope" "$out"

echo "── another account's tree: unreachable, or reachable and unwritable"
chmod 0000 "$tree"
out="$(bash "$probe" --sections nowrite --no-write "$tree" 2>&1)"; st=$?
expect_exit "unreachable is green" 0 "$st"
has "says so" "PASS no-write: $(id -un) cannot reach $tree" "$out"
chmod 0755 "$tree"; chmod -R a-w "$tree"
out="$(bash "$probe" --sections nowrite --no-write "$tree" 2>&1)"; st=$?
expect_exit "reachable but read-only is green" 0 "$st"
chmod -R u+w "$tree"
out="$(bash "$probe" --sections nowrite --no-write "$tree" 2>&1)"; st=$?
expect_exit "writable is red" 1 "$st"
has "names it" "FAIL no-write" "$out"

echo "── groups"
g="$(id -gn)"
out="$(bash "$probe" --sections identity --forbid-group "$g" --expect-group nosuchgroup-xyz 2>&1)"; st=$?
expect_exit "red" 1 "$st"
has "a forbidden group" "FAIL group: $(id -un) is in group $g" "$out"
has "a missing expected group" "FAIL group: $(id -un) is NOT in nosuchgroup-xyz" "$out"

echo "── gh: classified by prefix, never printed"
mkdir -p "$sb/bin"
for case in "github_pat_11ABCDEFG0123456789:FINE-GRAINED" "ghp_abcdefghijklmnop:CLASSIC" "gho_abcdefghij:OAUTH" ":holds no gh token"; do
    tok="${case%%:*}" want="${case#*:}"
    printf '#!/bin/sh\n[ "$1 $2" = "auth token" ] && printf "%%s" "%s"\n' "$tok" > "$sb/bin/gh"; chmod +x "$sb/bin/gh"
    out="$(bash "$probe" --sections gh --gh-bin "$sb/bin/gh" 2>&1)"
    has "${tok:-no token}: $want" "$want" "$out"
    if [ -n "$tok" ]; then hasnt "the token itself is not printed" "${tok#*_}" "$out"; fi
done

echo "── gh: its version, and a WARN below 2.50.0, the first with \`gh pr checks --json\`"
for case in "gh version 2.45.0 (2024-03-04 Ubuntu 2.45.0-1ubuntu0.3):WARN gh-version" \
            "gh version 2.49.2 (2024-05-13):WARN gh-version" \
            "gh version 2.50.0 (2024-05-29):INFO gh-version" \
            "gh version 2.81.0 (2025-10-01):INFO gh-version" \
            "gh version 3.0.0 (2027-01-01):INFO gh-version" \
            "gh is not answering:WARN gh-version"; do
    gv="${case%:*}" want="${case##*:}"
    printf '#!/bin/sh\n[ "$1" = --version ] && echo "%s"\n[ "$1 $2" = "auth token" ] && printf ghp_SECRETTOKEN\nexit 0\n' "$gv" > "$sb/bin/gh"; chmod +x "$sb/bin/gh"
    out="$(bash "$probe" --sections gh --gh-bin "$sb/bin/gh" 2>&1)"; st=$?
    has "$gv: $want" "$want" "$out"
    expect_exit "$gv: a version is never a failure" 0 "$st"
    hasnt "$gv: the token is not printed" "SECRETTOKEN" "$out"
done
printf '#!/bin/sh\n[ "$1" = --version ] && echo "gh version 2.45.0 (2024-03-04)"\nexit 0\n' > "$sb/bin/gh"
out="$(bash "$probe" --sections gh --gh-bin "$sb/bin/gh" 2>&1)"
has "an old gh names the version that added it" "older than 2.50.0, which added \`gh pr checks --json\`" "$out"
if [ "$(uname -s)" = Darwin ]; then
    has "and the upgrade (Homebrew on macOS)" "brew upgrade gh" "$out"
elif command -v dpkg >/dev/null 2>&1; then
    has "and the upgrade (GitHub's apt repository)" "https://cli.github.com/packages stable main" "$out"
    has "with the architecture left for the reader's shell" 'arch=$(dpkg --print-architecture)' "$out"
fi

echo "── agents: claude, codex, hermes by path, version and credential-file PRESENCE"
ah="$sb/agenthome"
mkdir -p "$ah/.local/bin" "$ah/.claude" "$ah/.codex" "$ah/.hermes/profiles/work"
printf '#!/bin/sh\necho "2.1.294 (Claude Code)"\n' > "$ah/.local/bin/claude"
printf '#!/bin/sh\necho "codex-cli 0.50.0"\n' > "$ah/.local/bin/codex"
printf '#!/bin/sh\necho "Hermes Agent v1.2.3"\n' > "$ah/.local/bin/hermes"
chmod +x "$ah/.local/bin/"*
printf 'SECRET-CLAUDE-CREDENTIAL\n' > "$ah/.claude/.credentials.json"
printf 'SECRET-HERMES-AUTH\n' > "$ah/.hermes/auth.json"
printf 'model:\n  # pick one\n  default: "anthropic/claude-opus-4.6"\n  provider: "anthropic"\n  api_key: SECRET-IN-CONFIG\ndisplay:\n  provider: not-this-one\n' > "$ah/.hermes/config.yaml"
printf 'model: "glm-5.3"   # bare string form\n' > "$ah/.hermes/profiles/work/config.yaml"
printf 'GLM_API_KEY=SECRET-IN-ENV\n' > "$ah/.hermes/profiles/work/.env"
printf 'work\n' > "$ah/.hermes/active_profile"
out="$(bash "$probe" --sections agents --home "$ah" 2>&1)"; st=$?
expect_exit "informational: exit 0" 0 "$st"
has "claude: path and version" "INFO ai-agent claude: $ah/.local/bin/claude  (2.1.294 (Claude Code))" "$out"
has "claude: its credential file is present" "login: credential file present (~/.claude/.credentials.json)" "$out"
has "codex: found, no credential file" "INFO ai-agent codex: $ah/.local/bin/codex  (codex-cli 0.50.0) · login: no credential file" "$out"
has "hermes: its auth file" "(Hermes Agent v1.2.3) · login: credential file present (~/.hermes/auth.json)" "$out"
has "hermes default profile: provider and model" "INFO ai-agent hermes-profile default: provider anthropic, model anthropic/claude-opus-4.6" "$out"
has "a named profile, the active one, bare model form" "INFO ai-agent hermes-profile work (active): provider (unset: auto), model glm-5.3" "$out"
has "its own credential file" "(~/.hermes/profiles/work/.env)" "$out"
hasnt "no credential is ever printed" "SECRET" "$out"
hasnt "no other config key is read as the provider" "not-this-one" "$out"
out="$(bash "$probe" --sections agents --home "$sb/nobody-home" 2>&1)"
has "an account without them: absent" "INFO ai-agent hermes: absent" "$out"

echo "── suspend attempts refused: reported as INFO, by a stub journalctl"
# The masks stop the sleep but not the attempt: a desktop's own idle-suspend still calls logind,
# which broadcasts the wall message and only then finds the target masked. The probe counts the
# refusals in this boot so the hub can say the desktop's automatic suspend is still on. Hermetic:
# a stub journalctl stands in for the journal, and says exactly what logind would. Linux only —
# the sleep section has no such branch on macOS (its own sleep section is pmset's).
if [ "$(uname -s)" = Linux ]; then
    stub() { # name, then the lines the stub prints for every call
        local n="$1"; shift
        { printf '#!/bin/sh\n'; for l in "$@"; do printf 'printf "%%s\\n" %s\n' "$(printf '%q' "$l")"; done; } > "$sb/bin/$n"
        chmod +x "$sb/bin/$n"
    }
    stub journalctl "Unit suspend.target is masked, refusing operation." \
                    "Oct 11 08:18:03 worker systemd-logind[1]: Unit suspend.target is masked, refusing operation."
    out="$(bash "$probe" --sections sleep --journalctl-bin "$sb/bin/journalctl" 2>&1)"
    has "counts the refusals" "INFO suspend-attempts: 2 refused in this boot" "$out"
    has "names what to turn off (COSMIC)" "COSMIC Settings > Power & Battery > Automatic suspend > Never" "$out"
    has "names what to turn off (GNOME)" "sleep-inactive-ac-type 'nothing'" "$out"
    # The refusals are INFO and never a FAIL: the line is INFO, and no FAIL names suspend-attempts
    # (the exit status is not asserted — it follows THIS machine's sleep targets, which differ
    # between a masked worker and a CI runner, and is not what the count is about).
    hasnt "reported as INFO, not FAIL" "FAIL suspend-attempts" "$out"
    stub journalctl
    out="$(bash "$probe" --sections sleep --journalctl-bin "$sb/bin/journalctl" 2>&1)"
    has "a quiet boot says so" "INFO suspend-attempts: none refused in this boot" "$out"
    hasnt "no advice when there is nothing to advise" "Automatic suspend > Never" "$out"
    # The other shape: a desktop calling the sleep verb straight through, which logind refuses with
    # a different sentence (systemd 255, logind-action.c). Both are counted.
    stub journalctl "Failed to execute suspend operation: Access denied"
    out="$(bash "$probe" --sections sleep --journalctl-bin "$sb/bin/journalctl" 2>&1)"
    has "a direct sleep verb refused is counted too" "INFO suspend-attempts: 1 refused in this boot" "$out"
    hasnt "and is still INFO" "FAIL suspend-attempts" "$out"
else
    echo "  ok   skipped on macOS: the sleep section is Linux's, and macOS's is pmset's"
    pass=$((pass+1))
fi

echo "── WSL: detection, systemd as PID 1, the Windows drives, .wslconfig (a stub root, --wsl-root)"
# There is no WSL machine in CI. The stub stands in for what the probe reads (/proc/version, the
# interop binfmt entry, PID 1's name, /etc/wsl.conf) and for the drives WSL mounts under /mnt.
# DrvFs's default (every file 0777 for the default user, read with the Windows user's token)
# is a tree this test's user can read; the fix (metadata,umask=077 for the admin, or no mount)
# is, from another account, a tree it cannot enter: mode 0000 here, as for the homes above.
W="$sb/wsl"
WSL2="Linux version 6.6.87-microsoft-standard-WSL2 (root@builder) (gcc (GCC) 11.2.0) #1 SMP"
wslfix() { # kernel-version pid1-name [wsl.conf content]; interop on, no Windows drive yet
    chmod -R u+rwx "$W" 2>/dev/null; rm -rf "$W"
    mkdir -p "$W/proc/1" "$W/proc/sys/fs/binfmt_misc" "$W/etc"
    printf '%s\n' "$1" > "$W/proc/version"; printf '%s\n' "$2" > "$W/proc/1/comm"
    printf 'enabled\ninterpreter /init\n' > "$W/proc/sys/fs/binfmt_misc/WSLInterop"
    if [ "$#" -ge 3 ]; then printf "$3" > "$W/etc/wsl.conf"; fi
}
UD=Users   # (a variable, so the public-tree guard does not read a fixture profile as a home path)
winfix() { # root-dir: a C: drive with a person's profile (the name has a space), as DrvFs shows it
    P="$W$1c/$UD/Win User"
    mkdir -p "$P/.ssh" "$P/Documents" "$W$1c/$UD/Public" "$W$1c/Windows/System32"
    printf 'not a real key\n' > "$P/.ssh/id_ed25519"; printf 'notes\n' > "$P/Documents/notes.txt"
    printf 'shared\n' > "$W$1c/$UD/Public/readme.txt"
    printf '#!/bin/sh\n' > "$W$1c/Windows/System32/cmd.exe"; chmod 0755 "$W$1c/Windows/System32/cmd.exe"
}
wslprobe() { out="$(WSL_DISTRO_NAME=Ubuntu bash "$probe" --wsl-root "$W" "$@" 2>&1)"; st=$?; }

wslfix "Linux version 6.8.0-1021-azure (buildd@lcy02-amd64) (gcc 13.2.0)" systemd
rm "$W/proc/sys/fs/binfmt_misc/WSLInterop"
wslprobe --sections wsl,wsl-mnt,wslconfig
expect_exit "a Linux that is not WSL: exit 0" 0 "$st"
has "says it is not WSL" "INFO wsl: not WSL" "$out"
hasnt "and checks nothing of WSL's" "wsl-" "$out"
hasnt "WSL_DISTRO_NAME does not override a stub" "WSL2" "$out"

wslfix "Linux version 4.4.0-19041-Microsoft (Microsoft@Microsoft.com) (gcc version 5.4.0 (GCC) ) #1237-Microsoft" init
wslprobe --sections wsl
expect_exit "WSL1 is red" 1 "$st"
has "names WSL1 and the conversion" "FAIL wsl: WSL1 has no systemd and no VM of its own: a worker needs WSL2 (from PowerShell: wsl --set-version <distro> 2)" "$out"

wslfix "$WSL2" init
wslprobe --sections wsl
expect_exit "WSL2 without systemd is red" 1 "$st"
has "detected, with the reasons" "INFO wsl: WSL2 (binfmt_misc has WSLInterop, /proc/version names Microsoft)" "$out"
has "systemd not PID 1, and the lines to add" "FAIL wsl-systemd: systemd is not PID 1 (PID 1 is init): no linger, no user units, no journal. Add to /etc/wsl.conf the two lines  [boot]  systemd=true" "$out"
has "and the restart" "then from PowerShell: wsl --shutdown" "$out"
has "Windows' power settings: the commands, and that they are not checked" "NOT checked here. On Windows, as Administrator: powercfg /change standby-timeout-ac 0 ; powercfg /change hibernate-timeout-ac 0" "$out"
hasnt "the power line is not a FAIL" "FAIL wsl-power" "$out"

wslfix "$WSL2" init '[boot]\r\nsystemd = true\r\n'
wslprobe --sections wsl
expect_exit "configured but not restarted is red" 1 "$st"
has "says WSL has not restarted (and read a CRLF wsl.conf)" "although /etc/wsl.conf sets [boot] systemd=true: WSL has not restarted since" "$out"

wslfix "Linux version 6.6.36-custom (builder@host) (gcc 12)" systemd '[boot]\nsystemd=true\n[automount]\noptions = "metadata,uid=1000,gid=1000,umask=077" # the fix\n'
wslprobe --sections wsl
expect_exit "WSL2 with systemd as PID 1 is green" 0 "$st"
has "a custom kernel is WSL2 by the interop entry" "INFO wsl: WSL2 (binfmt_misc has WSLInterop)" "$out"
has "systemd is PID 1" "PASS wsl-systemd: systemd is PID 1" "$out"
has "reports the automount options (quotes and comment dropped)" "INFO wsl-automount: enabled=true (default) root=/mnt/ options=metadata,uid=1000,gid=1000,umask=077" "$out"
has "reports interop" "INFO wsl-interop: enabled" "$out"

echo "── WSL: the Windows user's profile, open the way DrvFs shows it by default"
wslfix "$WSL2" systemd
winfix /mnt/
P="$W/mnt/c/$UD/Win User"
wslprobe --sections wsl-mnt
expect_exit "an open drive is red" 1 "$st"
has "the profile is found under c/Users and listed" "FAIL wsl-mnt: $(id -un) can LIST the Windows profile $P" "$out"
has "its key is read" "$P/.ssh/id_ed25519" "$(printf '%s' "$out" | sed -n '/^FAIL wsl-read/,/Fix:/p')"
has "and a file found by listing" "$P/Documents/notes.txt" "$(printf '%s' "$out" | sed -n '/^FAIL wsl-read/,/Fix:/p')"
has "with the fix" "Fix: sudo worker-setup.sh sets /etc/wsl.conf [automount] options=metadata,uid=<admin>,gid=<admin>,umask=077" "$out"
hasnt "Public is not a person's profile" "Users/Public" "$out"
has "a Windows program it may start is red (interop on)" "FAIL wsl-exec: $(id -un) can start $W/mnt/c/Windows/System32/cmd.exe, which runs as the Windows user" "$out"
rm "$W/proc/sys/fs/binfmt_misc/WSLInterop"
wslprobe --sections wsl-mnt
hasnt "interop off: reaching cmd.exe is not a FAIL" "FAIL wsl-exec" "$out"
has "and says why" "INFO wsl-exec: $(id -un) can reach $W/mnt/c/Windows/System32/cmd.exe, but interop is off" "$out"
has "the files are still red" "FAIL wsl-read" "$out"

echo "── WSL: the drive closed (the [automount] restriction, from another account)"
chmod 0000 "$W/mnt/c"
wslprobe --sections wsl-mnt
expect_exit "a closed drive is green" 0 "$st"
has "nothing to list" "PASS wsl-mnt: $(id -un) cannot list /mnt/c/Users, and no --windows-home was named" "$out"
wslprobe --sections wsl-mnt --windows-home "$P"
expect_exit "a named profile behind the closed drive is green" 0 "$st"
has "tried by name, nothing opens" "PASS wsl-read: $(id -un) can read none of 10 file(s) tried in $P" "$out"
hasnt "and no Windows program" "wsl-exec" "$out"
chmod 0755 "$W/mnt/c"

echo "── WSL: no drive at all ([automount] enabled=false), and a moved [automount] root"
rm -rf "$W/mnt"
wslprobe --sections wsl-mnt
expect_exit "no drive is green" 0 "$st"
has "says so" "PASS wsl-mnt: no Windows drive at /mnt/c" "$out"
wslfix "$WSL2" systemd '[automount]\nroot = /windir\n'
winfix /windir/
wslprobe --sections wsl,wsl-mnt
has "the root is read from wsl.conf (a slash added)" "root=/windir/" "$out"
has "and the profile found there" "FAIL wsl-read: $(id -un) can read the Windows user's files through DrvFs ($W/windir/c/$UD/Win User)" "$out"

echo "── WSL: .wslconfig, read by the account running the probe (root, under --check)"
wslfix "$WSL2" systemd
winfix /mnt/
printf '[general]\r\ninstanceIdleTimeout = -1\r\n[wsl2]\r\nnetworkingMode=mirrored\r\n' > "$P/.wslconfig"
wslprobe --sections wslconfig
expect_exit "informational: exit 0" 0 "$st"
has "instanceIdleTimeout=-1 is INFO (CRLF read)" "INFO wsl-idle: $P/.wslconfig: [general] instanceIdleTimeout=-1 (documented" "$out"
has "the VM's timeout, unset" "[wsl2] vmIdleTimeout=unset (default 60000 ms)" "$out"
has "the network mode" "INFO wsl-network: $P/.wslconfig: [wsl2] networkingMode=mirrored" "$out"
hasnt "no warning" "WARN" "$out"
printf '[wsl2]\nvmIdleTimeout=-1\n' > "$P/.wslconfig"
wslprobe --sections wslconfig
has "vmIdleTimeout=-1 alone is a WARN on the distribution's timeout" "WARN wsl-idle: $P/.wslconfig: [general] instanceIdleTimeout=unset (default 15000 ms)" "$out"
has "and reports the VM's" "[wsl2] vmIdleTimeout=-1 (the VM's own idle timeout; what -1 does there is undocumented" "$out"
expect_exit "a WARN is not a failure" 0 "$st"
if command -v iconv >/dev/null 2>&1; then
    { printf '\377\376'; printf '[general]\r\ninstanceIdleTimeout=-1\r\n' | iconv -f UTF-8 -t UTF-16LE; } > "$P/.wslconfig"
    wslprobe --sections wslconfig
    has "a UTF-16 .wslconfig (Notepad's 'Unicode') is read" "INFO wsl-idle: $P/.wslconfig: [general] instanceIdleTimeout=-1" "$out"
fi
rm "$P/.wslconfig"
wslprobe --sections wslconfig
has "none in a readable profile: WARN, defaults apply" "WARN wsl-idle: $P has no .wslconfig: WSL's defaults apply" "$out"
chmod 0000 "$W/mnt/c"
wslprobe --sections wslconfig
has "unreadable: WARN, and where to look" "WARN wsl-idle: no .wslconfig readable to $(id -un) (under /mnt/c/Users): cannot tell" "$out"
expect_exit "still not a failure" 0 "$st"
chmod 0755 "$W/mnt/c"

echo "── usage"
bash "$probe" --bogus >/dev/null 2>&1; expect_exit "an unknown argument is exit 2" 2 "$?"

echo
echo "worker-probe-test: $pass passed, $fail failed"
[ "$fail" = 0 ]
