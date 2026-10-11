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

echo "── a dry run plans every step and changes nothing"
snap() { ls -la /etc/ssh/sshd_config.d 2>/dev/null; cat /etc/fstab 2>/dev/null; ls /Library/LaunchDaemons 2>/dev/null | grep hubdiag; }
before="$(snap)"
out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" 2>&1)"; st=$?
expect_exit "exit 0" 0 "$st"
has "says it is a dry run" "DRY RUN" "$out"
has "plans the hub's account" "would: create account $H" "$out"
has "plans the worker's account" "would: create account $A" "$out"
has "plans the key" "authorized_keys" "$out"
has "plans the work tree" "/work (0755, the worktrees)" "$out"
has "plans the sshd drop-in" "would: sshd drop-in /etc/ssh/sshd_config.d/10-ikigai-worker.conf" "$out"
has "the drop-in limits forwarding to remote" "AllowTcpForwarding remote" "$out"
has "the drop-in names the one port" "PermitListen localhost:1060" "$out"
has "the drop-in keeps the worker off ssh" "DenyUsers $A" "$out"
has "the drop-in ends its Match block" "Match all" "$out"
has "the default view is the agent's work" "view work: " "$out"
has "closes the admin's home" "the admin's home" "$out"
has "counts and promises nothing changed" "nothing was changed" "$out"
case "$(uname -s)" in
    Darwin)
        has "macOS: an own primary group, so not staff" "its own primary group, so it is NOT in staff" "$out"
        has "macOS: an ACL deny on the admin's home" "deny list,search" "$out"
        has "macOS: the view is a LaunchDaemon copy" "LaunchDaemon /Library/LaunchDaemons/dev.ikigai-rs.hubdiag-view-work.plist" "$out" ;;
    Linux)
        has "Linux: a read-only bind mount" "none bind,ro 0 0" "$out" ;;
esac
id "$H" >/dev/null 2>&1 && { echo "  FAIL the dry run created $H"; fail=$((fail+1)); } || { echo "  ok   no account created"; pass=$((pass+1)); }
[ "$before" = "$(snap)" ] && { echo "  ok   sshd.d, fstab and LaunchDaemons unchanged"; pass=$((pass+1)); } || { echo "  FAIL system state changed"; fail=$((fail+1)); }

echo "── the dry run needs no --admin when not under sudo, and the wording is plain"
out="$(env -u SUDO_USER bash "$setup" --dry-run --hubdiag "$H" --agent "$A" --key "$sb/k.pub" 2>&1)"; st=$?
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
out="$(bash "$setup" --dry-run --admin "$me" --hubdiag "$H" --agent "$A" --key "$sb/k.pub" --view work=/x/work --view src=/x/src 2>&1)"
has "both views planned" "view src: /x/src" "$out"
out="$(bash "$setup" --dry-run --admin "$me" --key "$sb/k.pub" --view relative 2>&1)"; st=$?
expect_exit "a view without NAME=/abs is refused" 1 "$st"
has "with the reason" "NAME=/absolute/dir" "$out"

echo "── without root it refuses and says how"
out="$(bash "$setup" --admin "$me" --key "$sb/k.pub" 2>&1)"; st=$?
expect_exit "exit 2" 2 "$st"
has "prints the sudo line" "sudo $setup --admin $me --key $sb/k.pub" "$out"
out="$(bash "$setup" --admin "$me" --check 2>&1)"; st=$?
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
