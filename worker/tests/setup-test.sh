#!/bin/bash
# Hermetic test for worker-setup.sh: the parts that run without root. A dry run reads this
# machine's account database and files and must change NOTHING; a real run without root must
# refuse and print the sudo line; bad input must be refused before anything happens. The real
# run is ci-linux.sh and ci-macos.sh beside this file, on throwaway CI runners only.
#
# Run: worker/tests/setup-test.sh   (exit 0 = all pass)
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"
setup="$here/worker-setup.sh"
sb="$(mktemp -d)"; trap 'rm -rf "$sb"' EXIT
me="$(id -un)"

pass=0 fail=0
has()   { if printf '%s' "$3" | grep -qF -- "$2"; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1: no '$2' in:"; printf '%s\n' "$3" | sed 's/^/         /' | tail -30; fail=$((fail+1)); fi; }
expect_exit() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1: expected exit $2, got $3"; fail=$((fail+1)); fi; }

if [ "$(id -u)" = 0 ]; then echo "worker-setup-test: run it as a normal user, not root"; exit 2; fi
ssh-keygen -q -t ed25519 -N '' -C test -f "$sb/k"

# Pick names no machine has, so a dry run plans every step.
H="hubdiagtest$$" A="agenttest$$"

# The script reads a SCRATCH mount table, never this machine's /etc/fstab: on a machine that is
# already a worker, the real one mounts the view at /srv/hubdiag/work from a different source,
# and the script rightly refuses a mount point it does not own (ledger 1233). The test asks for
# it with --fstab, a seam used only here; on macOS the flag is accepted and never read (see the
# mount-table block below, and the comment on it).
fstab="$sb/fstab"; : > "$fstab"

echo "── a dry run plans every step and changes nothing"
snap() { ls -la /etc/ssh/sshd_config.d 2>/dev/null; cat /etc/fstab 2>/dev/null; ls /Library/LaunchDaemons 2>/dev/null | grep hubdiag; }
before="$(snap)"
out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --fstab "$fstab" 2>&1)"; st=$?
expect_exit "exit 0" 0 "$st"
has "says it is a dry run" "DRY RUN" "$out"
has "plans the hub's account" "would: create account $H" "$out"
has "plans the worker's account" "would: create account $A" "$out"
has "plans the key" "authorized_keys" "$out"
has "plans the work tree" "/work (0755, the worktrees)" "$out"
has "plans the sshd drop-in" "would: sshd drop-in /etc/ssh/sshd_config.d/10-ikigai-worker.conf" "$out"
has "the drop-in limits forwarding to remote" "AllowTcpForwarding remote" "$out"
has "the drop-in names the one port" "PermitListen localhost:1062" "$out"
has "the drop-in keeps the worker off ssh" "DenyUsers $A" "$out"
has "the drop-in ends its Match block" "Match all" "$out"
has "the default view is the agent's work" "view work: " "$out"
has "closes the admin's home" "the admin's home" "$out"
has "counts and promises nothing changed" "nothing was changed" "$out"
case "$(uname -s)" in
    Darwin)
        has "macOS: an own primary group, so not staff" "its own primary group, so it is NOT in staff" "$out"
        has "macOS: an ACL deny on the admin's home" "deny list,search" "$out"
        has "macOS: the view is a LaunchDaemon copy" "LaunchDaemon /Library/LaunchDaemons/dev.ikigai-rs.hubdiag-view-work.plist" "$out"
        # Ledger item 1450: one id per account for its group and itself, chosen when applied. A
        # dry run creates nothing, so a number here would be the same for every account.
        has "macOS: the group's id is chosen when applied" "would: create group $H (gid <$H-id>: the first id from 550 free as both a uid and a gid, chosen when applied)" "$out"
        has "macOS: the account's uid is that same id" "would: create account $H (uid <$H-id>: the first id from 550" "$out"
        has "macOS: the group command takes it" "dseditgroup -o create -i <$H-id> " "$out"
        has "macOS: and so does the account's" "dscl . -create /Users/$A UniqueID <$A-id> " "$out"
        case "$out" in *"gid 550"*|*"uid 550"*) echo "  FAIL the dry run printed a number for an id it does not choose"; fail=$((fail+1)) ;;
            *) echo "  ok   macOS: no id number printed for an account not yet made"; pass=$((pass+1)) ;; esac ;;
    Linux)
        has "Linux: a read-only bind mount" "none bind,ro 0 0" "$out" ;;
esac
id "$H" >/dev/null 2>&1 && { echo "  FAIL the dry run created $H"; fail=$((fail+1)); } || { echo "  ok   no account created"; pass=$((pass+1)); }
[ "$before" = "$(snap)" ] && { echo "  ok   sshd.d, fstab and LaunchDaemons unchanged"; pass=$((pass+1)); } || { echo "  FAIL system state changed"; fail=$((fail+1)); }

echo "── the dry run needs no --admin when not under sudo, and the wording is plain"
out="$(env -u SUDO_USER bash "$setup" --dry-run --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --fstab "$fstab" 2>&1)"; st=$?
expect_exit "no --admin, no sudo: exit 0" 0 "$st"
has "says which admin it assumed" "assuming the admin is $me (you)" "$out"
has "and uses it" "admin=$me" "$out"
has "the closing line is labeled as the NEXT step" "next, apply it:  sudo $setup --admin $me" "$out"
if [ "$(uname -s)" = Linux ]; then
    has "agent: no password AND no ssh login" "$A password state (unreadable without root; setup sets it to '*': no password, no ssh login: sudo -iu $A / machinectl shell $A@ only)" "$out"
    has "hubdiag: no password, key-only ssh" "no password, key-only ssh" "$out"
fi
out="$(env -u SUDO_USER bash "$setup" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" 2>&1)"; st=$?
expect_exit "a real run without root still refuses (exit 2)" 2 "$st"
has "naming the sudo line, not asking who the admin is" "sudo $setup" "$out"

echo "── views are configurable"
out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --fstab "$fstab" --view work=/x/work --view src=/x/src 2>&1)"
has "both views planned" "view src: /x/src" "$out"
out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --fstab "$fstab" --view relative 2>&1)"; st=$?
expect_exit "a view without NAME=/abs is refused" 1 "$st"
has "with the reason" "NAME=/absolute/dir" "$out"

# The mount table is Linux's: the views step reads /etc/fstab and bind-mounts (worker-setup.sh's
# linux_view). macOS has neither — its views are a LaunchDaemon's rsync copy, checked end to end
# by worker/tests/ci-macos.sh — so a Mac never reads the file and this block cannot hold there.
# Asked only of Linux, and said so, rather than asserted unguarded and red on every Mac.
if [ "$(uname -s)" = Linux ]; then
    echo "── the mount table is injectable; a foreign mount at the point is refused"
    printf '%s\n' '/some/other/src /srv/hubdiag/work none bind,ro 0 0' > "$fstab"
    out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --fstab "$fstab" 2>&1)"; st=$?
    expect_exit "a different source at the mount point is refused" 1 "$st"
    has "naming the file it read" "$fstab already mounts something else at /srv/hubdiag/work: fix it by hand: /some/other/src /srv/hubdiag/work none bind,ro 0 0" "$out"
    : > "$fstab"
else
    echo "  ok   skipped on macOS: /etc/fstab is Linux's (a bind mount);"
    echo "         macOS's views are a LaunchDaemon's rsync copy, checked by ci-macos.sh"
    pass=$((pass+1))
fi

echo "── WSL2: /etc/wsl.conf (systemd, the Windows drives) and trees on DrvFs refused (a stub root)"
# A stub stands in for the WSL machine (--wsl-root): /proc/version, the interop binfmt entry,
# PID 1's name and /etc/wsl.conf. A dry run writes nothing, so the stub's wsl.conf must be
# unchanged afterward; what a real run would write is printed, line by line, and asserted here.
W="$sb/wsl"
wslfix() { # pid1-name [wsl.conf content]
    rm -rf "$W"; mkdir -p "$W/proc/1" "$W/proc/sys/fs/binfmt_misc" "$W/etc"
    printf 'Linux version 6.6.87-microsoft-standard-WSL2 (root@builder) (gcc 11.2.0)\n' > "$W/proc/version"
    printf '%s\n' "$1" > "$W/proc/1/comm"; printf 'enabled\n' > "$W/proc/sys/fs/binfmt_misc/WSLInterop"
    if [ "$#" -ge 2 ]; then printf "$2" > "$W/etc/wsl.conf"; fi
}
U="$(id -u)" G="$(id -g)"
wdry() { out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --fstab "$fstab" --wsl-root "$W" "$@" 2>&1)"; st=$?; }

wslfix init
wdry
expect_exit "a fresh WSL2: the dry run plans it (exit 0)" 0 "$st"
has "detected" "── WSL2 (binfmt_misc has WSLInterop, /proc/version names Microsoft)" "$out"
has "one wsl.conf change, both edits named" "would: $W/etc/wsl.conf: [boot] systemd=true; [automount] options=metadata,uid=$U,gid=$G,umask=077 (the Windows drives become $me's alone) (read when WSL next starts)" "$out"
has "the file it would write: systemd" "| systemd=true" "$out"
has "the file it would write: the drives the admin's alone (the default)" "| options=\"metadata,uid=$U,gid=$G,umask=077\"" "$out"
has "a real run stops until WSL restarts" "systemd is not PID 1 (PID 1 is init): a real run makes the change above and stops here, until WSL restarts: from PowerShell, wsl --shutdown" "$out"
has "and the Windows side is named, not touched" "the Windows side, which this cannot see or change" "$out"
[ ! -e "$W/etc/wsl.conf" ] && { echo "  ok   the dry run wrote no wsl.conf"; pass=$((pass+1)); } || { echo "  FAIL the dry run wrote $W/etc/wsl.conf"; fail=$((fail+1)); }

wdry --wsl-automount off
has "--wsl-automount off: no Windows drive at all" "| enabled=false" "$out"
case "$out" in *"| options="*) echo "  FAIL off also wrote options"; fail=$((fail+1)) ;; *) echo "  ok   and no options line"; pass=$((pass+1)) ;; esac
out="$(bash "$setup" --dry-run --admin "$me" --key "$sb/k.pub" --wsl-automount sideways 2>&1)"; st=$?
expect_exit "an unknown --wsl-automount is refused" 2 "$st"

wslfix init '# kept\n[network]\nhostname = box\n\n[automount]\noptions = "case=off,uid=1000,fmask=11"\nroot = /mnt/\n'
before="$(cat "$W/etc/wsl.conf")"
wdry
has "the admin's options are merged, not replaced" "| options=\"case=off,fmask=11,metadata,uid=$U,gid=$G,umask=077\"" "$out"
has "comments kept" "| # kept" "$out"
has "other sections kept" "| hostname = box" "$out"
has "other keys of the section kept" "| root = /mnt/" "$out"
has "a missing section is added" "| [boot]" "$out"
[ "$before" = "$(cat "$W/etc/wsl.conf")" ] && { echo "  ok   the stub's wsl.conf is unchanged"; pass=$((pass+1)); } || { echo "  FAIL the dry run changed wsl.conf"; fail=$((fail+1)); }

wslfix systemd "[boot]\r\nsystemd=true\r\n[automount]\r\noptions=\"metadata,uid=$U,gid=$G,umask=077\"\r\n"
wdry
expect_exit "already set up" 0 "$st"
has "systemd=true is seen (CRLF)" "  ok $W/etc/wsl.conf: [boot] systemd=true" "$out"
has "the restriction is seen" "  ok $W/etc/wsl.conf: [automount] options=metadata,uid=$U,gid=$G,umask=077 (the Windows drives are $me's alone)" "$out"
has "systemd is PID 1" "  ok systemd is PID 1" "$out"
case "$out" in *"would: $W/etc/wsl.conf"*) echo "  FAIL it would rewrite a wsl.conf that is right"; fail=$((fail+1)) ;; *) echo "  ok   nothing to change in wsl.conf"; pass=$((pass+1)) ;; esac

wslfix systemd '[boot]\nsystemd=true\n[automount]\nenabled=false\n'
wdry
has "automount already off is enough" "  ok $W/etc/wsl.conf: [automount] enabled=false (no Windows drive is mounted)" "$out"

wslfix systemd '[boot]\nsystemd=true\n'
wdry
has "systemd PID 1, drives open: the restriction, and when it takes effect" "the [automount] change takes effect when WSL restarts" "$out"

wslfix init
wdry --view work=/mnt/c/work
expect_exit "a view on the Windows drive is refused" 1 "$st"
has "with the reason" "view work: /mnt/c/work is under /mnt/, where WSL mounts the Windows drives (DrvFs): Linux permissions do not hold there" "$out"
case "$out" in *"would:"*) echo "  FAIL it planned changes before refusing"; fail=$((fail+1)) ;; *) echo "  ok   refused before any change is planned"; pass=$((pass+1)) ;; esac
wslfix init '[automount]\nroot=/windir\n'
wdry --view src=/windir/c/src
expect_exit "under a moved [automount] root too" 1 "$st"
has "naming that root" "is under /windir/, where WSL mounts" "$out"
wdry --view src=/mnt/data/src
expect_exit "and /mnt is an ordinary directory when the drives are elsewhere" 0 "$st"

rm -rf "$W"; mkdir -p "$W/proc"; printf 'Linux version 6.8.0-1021-azure (buildd@lcy02-amd64)\n' > "$W/proc/version"
wdry --view src=/mnt/data/src
expect_exit "not WSL: a view under /mnt is allowed" 0 "$st"
case "$out" in *WSL*) echo "  FAIL a Linux that is not WSL got a WSL step"; fail=$((fail+1)) ;; *) echo "  ok   and no WSL step"; pass=$((pass+1)) ;; esac

wslfix init
printf 'Linux version 4.4.0-19041-Microsoft (Microsoft@Microsoft.com) (gcc version 5.4.0 (GCC) )\n' > "$W/proc/version"
wdry
expect_exit "WSL1 is refused" 1 "$st"
has "with the conversion" "WSL1 cannot run a worker (no systemd, no VM of its own): convert it from PowerShell, wsl --set-version <distro> 2" "$out"

echo "── --check's walk: build output and caches pruned, macOS ~/Library too, and progress shown"
# Ledger item 1451: on tweezer the walk sat for minutes in ~/Library with nothing printed.
# --list-candidates runs the walk alone, as this user, so it is tested without root.
Hm="$sb/home"
mkdir -p "$Hm/.ssh" "$Hm/Documents" "$Hm/src/x/target/debug" "$Hm/Library/Mail/V10" \
         "$Hm/Library/Mobile Documents/com~apple~CloudDocs" "$Hm/Library/Caches/c" "$Hm/.cache/x" \
         "$Hm/Pictures/Photos Library.photoslibrary/originals"
for f in .bash_history .ssh/id_ed25519 Documents/notes.txt src/x/main.rs src/x/target/debug/out \
         Library/Mail/V10/msg.emlx "Library/Mobile Documents/com~apple~CloudDocs/doc.txt" \
         "Pictures/Photos Library.photoslibrary/originals/p.heic" Library/Caches/c/blob .cache/x/blob; do printf 'x\n' > "$Hm/$f"; done
out="$(bash "$setup" --list-candidates "$Hm" 2>"$sb/err")"; st=$?
err="$(cat "$sb/err")"
expect_exit "--list-candidates exits 0, without root" 0 "$st"
has "the shell history is sampled" "$Hm/.bash_history" "$out"
has "a key is sampled" "$Hm/.ssh/id_ed25519" "$out"
has "the person's documents are sampled" "$Hm/Documents/notes.txt" "$out"
has "the person's trees are sampled" "$Hm/src/x/main.rs" "$out"
has "the total is reported" "files to try" "$err"
case "$out" in *target/debug*|*/Library/Caches/*|*/.cache/*) echo "  FAIL build output or a cache was walked"; fail=$((fail+1)) ;;
    *) echo "  ok   build output and caches are pruned"; pass=$((pass+1)) ;; esac
if [ "$(uname -s)" = Darwin ]; then
    case "$out" in *"/Library/"*) echo "  FAIL macOS: ~/Library was walked"; fail=$((fail+1)) ;;
        *) echo "  ok   macOS: ~/Library (Mail, Mobile Documents) is pruned"; pass=$((pass+1)) ;; esac
    case "$out" in *"/Pictures/"*) echo "  FAIL macOS: ~/Pictures was walked"; fail=$((fail+1)) ;;
        *) echo "  ok   macOS: ~/Pictures (Photos library) is pruned"; pass=$((pass+1)) ;; esac
else
    has "Linux: the walk is unchanged (a Library directory is an ordinary one)" "$Hm/Library/Mail/V10/msg.emlx" "$out"
    has "Linux: ~/Pictures is an ordinary directory" "$Hm/Pictures/Photos Library.photoslibrary/originals/p.heic" "$out"
fi
# A walk that takes longer than five seconds says how far it has got: a find that prints one name,
# sleeps six seconds and prints another stands in for a slow home.
mkdir -p "$sb/slow"
printf '#!/bin/sh\necho /slow/one\nsleep 6\necho /slow/two\n' > "$sb/slow/find"; chmod +x "$sb/slow/find"
out="$(PATH="$sb/slow:$PATH" bash "$setup" --list-candidates "$Hm" 2>"$sb/err")"; st=$?
err="$(cat "$sb/err")"
expect_exit "a slow walk still exits 0" 0 "$st"
has "a slow walk prints its progress" "... 1 files so far, " "$err"
has "and its total" "2 files to try (" "$err"
has "and the list is whole" "/slow/two" "$out"

echo "── without root it refuses and says how"
out="$(bash "$setup" --admin "$me" --key "$sb/k.pub" --hubdiag "$H" --agent "$A" --fstab "$fstab" 2>&1)"; st=$?
expect_exit "exit 2" 2 "$st"
has "prints the sudo line" "sudo $setup --admin $me --key $sb/k.pub" "$out"
out="$(bash "$setup" --admin "$me" --hubdiag "$H" --agent "$A" --check 2>&1)"; st=$?
expect_exit "--check without root: exit 2" 2 "$st"

echo "── bad input is refused before anything happens"
out="$(bash "$setup" --dry-run --admin "$me" --key "$sb/k" 2>&1)"; st=$?
expect_exit "a private key file is refused" 1 "$st"
printf '%s\n%s\n' "$(cat "$sb/k.pub")" "$(cat "$sb/k.pub")" > "$sb/two.pub"
out="$(bash "$setup" --dry-run --admin "$me" --key "$sb/two.pub" 2>&1)"; st=$?
expect_exit "two keys in one file are refused" 1 "$st"
out="$(bash "$setup" --dry-run --admin root --key "$sb/k.pub" 2>&1)"; st=$?
expect_exit "--admin root is refused" 1 "$st"
out="$(bash "$setup" --dry-run --admin "$me" --agent "$me" --key "$sb/k.pub" 2>&1)"; st=$?
expect_exit "the admin cannot be the agent" 1 "$st"
out="$(bash "$setup" --dry-run --admin nosuchuser$$ --key "$sb/k.pub" 2>&1)"; st=$?
expect_exit "an unknown admin is refused" 1 "$st"

echo
echo "worker-setup-test: $pass passed, $fail failed"
[ "$fail" = 0 ]
