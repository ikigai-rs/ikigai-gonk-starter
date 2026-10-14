#!/bin/bash
# The worker boundary on macOS, END TO END on a THROWAWAY GitHub-hosted macOS runner. Run by
# ci.yml's `worker boundary (macos)` job; NEVER on a Mac anyone uses (it creates
# accounts, edits an admin's home ACL and sshd's configuration, and loads a LaunchDaemon).
#
# The macOS half of worker-setup.sh was written from Apple's man pages (dscl, dseditgroup,
# chmod's ACL grammar, openrsync, sshd_config) without a spare Mac. This runs it on one:
#   - accounts made with dscl, each with its OWN primary group: dsmemberutil must say neither is
#     in staff, the group that may read every standard home
#     (/Users/<name> is drwxr-x--- <name>:staff); a new account's uid is its group's gid, and an
#     account an earlier setup made with different ids keeps them (never renumbered)
#   - the ACL deny on the admin's home holds even when the account IS put in staff
#   - the view: a LaunchDaemon's rsync copy that hubdiag reads and cannot write
#   - --check green, then RED when the deny is removed and hubdiag is in staff (macOS's form of
#     the traverse leak: the home becomes readable by group and .bash_history opens)
#   - sshd -T reads the drop-in; a real key login is attempted and reported, not required
#     (Remote Login on a hosted runner is not ours to rely on)
set -u

if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ] || [ "$(uname -s)" != Darwin ]; then
    echo "ci-macos.sh: refusing to run outside a GitHub-hosted macOS runner: it creates accounts" >&2
    echo "  and edits a home's ACL, sshd and launchd on the machine it runs on." >&2
    exit 2
fi
[ "$(id -u)" = 0 ] || { echo "ci-macos.sh: run under sudo" >&2; exit 2; }

repo="$(cd "$(dirname "$0")/../.." && pwd)"
setup="$repo/worker/worker-setup.sh"
T="$(mktemp -d)"; chmod 0755 "$T"
pass=0 fail=0
ok()   { echo "  ok   $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL $1"; fail=$((fail + 1)); }
expect_exit() { if [ "$2" = "$3" ]; then ok "$1 (exit $3)"; else bad "$1: expected exit $2, got $3"; fi; }
has()  { if printf '%s' "$3" | grep -qF -- "$2"; then ok "$1"; else bad "$1: no '$2' in:"; printf '%s\n' "$3" | sed 's/^/         /' | tail -40; fi; }

echo "── fixture: a standard admin-like user (primary group staff, home 0750)"
dscl . -create /Users/fixadmin
dscl . -create /Users/fixadmin UniqueID 701
dscl . -create /Users/fixadmin PrimaryGroupID 20
dscl . -create /Users/fixadmin UserShell /bin/zsh
dscl . -create /Users/fixadmin NFSHomeDirectory /Users/fixadmin
dscl . -create /Users/fixadmin Password '*'
install -d -m 0750 -o fixadmin -g staff /Users/fixadmin
install -d -o fixadmin -g staff /Users/fixadmin/.config/gh /Users/fixadmin/src/demo
printf 'ls\n' > /Users/fixadmin/.bash_history
printf 'github.com:\n  user: fixture\n' > /Users/fixadmin/.config/gh/hosts.yml
printf '# demo\n' > /Users/fixadmin/src/demo/README.md
chown -R fixadmin:staff /Users/fixadmin
ssh-keygen -q -t ed25519 -N '' -C hub-fixture -f "$T/hub"
echo "   sshd_config Include line: $(grep -n '^Include' /etc/ssh/sshd_config || echo NONE)"

echo "── fixture: hubdiag as an earlier setup made it, uid one above its group's gid (ledger item 1450)"
# What tweezer got before the fix (hubdiag 551/550): the setup must keep it, never renumber it.
# Two adjacent ids free as both a uid and a gid, so the fixture collides with nothing here.
taken() { dscl . -search /Users UniqueID "$1" 2>/dev/null | grep -q . || dscl . -search /Groups PrimaryGroupID "$1" 2>/dev/null | grep -q .; }
g0=550; while taken "$g0" || taken "$((g0 + 1))"; do g0=$((g0 + 1)); done
dseditgroup -o create -i "$g0" -r "hub diagnostics (read-only)" hubdiag
dscl . -create /Users/hubdiag
dscl . -create /Users/hubdiag UniqueID "$((g0 + 1))"
dscl . -create /Users/hubdiag PrimaryGroupID "$g0"
dscl . -create /Users/hubdiag UserShell /bin/zsh
dscl . -create /Users/hubdiag NFSHomeDirectory /Users/hubdiag
dscl . -create /Users/hubdiag Password '*'
dscl . -create /Users/hubdiag IsHidden 1
echo "   hubdiag: uid $((g0 + 1)), group gid $g0"

views=(--view work=/Users/agent/work --view src=/Users/fixadmin/src)
echo "── dry run, setup, setup again"
out="$("$setup" --dry-run --admin fixadmin --key "$T/hub.pub" "${views[@]}" 2>&1)"; st=$?
expect_exit "dry run" 0 "$st"
id agent >/dev/null 2>&1 && bad "dry run created agent" || ok "dry run created no account"
has "the dry run says the new account's id is chosen when applied" "chosen when applied" "$out"
out="$("$setup" --admin fixadmin --key "$T/hub.pub" "${views[@]}" 2>&1)"; st=$?
expect_exit "setup" 0 "$st"; printf '%s\n' "$out" | sed 's/^/     | /'
gid_of() { dscl . -read "/Groups/$1" PrimaryGroupID 2>/dev/null | awk '{print $2}'; }
au="$(id -u agent)" ag="$(gid_of agent)" ap="$(id -g agent)"
if [ -n "$au" ] && [ "$au" = "$ag" ] && [ "$ap" = "$ag" ]; then ok "agent: one id for the account and its group ($au)"
else bad "agent: uid $au, primary gid $ap, group agent's gid $ag: wanted one id for all three (ledger item 1450)"; fi
hu="$(id -u hubdiag)" hg="$(gid_of hubdiag)"
if [ "$hu/$hg" = "$((g0 + 1))/$g0" ]; then ok "hubdiag kept its ids (uid $hu, gid $hg): an existing account is never renumbered"
else bad "hubdiag was renumbered: uid $hu, gid $hg (it had $((g0 + 1))/$g0)"; fi
out="$("$setup" --admin fixadmin --key "$T/hub.pub" "${views[@]}" 2>&1)"; st=$?
expect_exit "second setup" 0 "$st"
has "second setup changes nothing" "0 change(s) made" "$out"
has "and says why hubdiag's ids differ" "never renumbered" "$out"

for u in hubdiag agent; do
    m="$(dsmemberutil checkmembership -U "$u" -G staff 2>&1)"
    has "$u is not in staff ($m)" "not a member" "$m"
done
echo "   home ACL: $(ls -led /Users/fixadmin | tail -n +2 | tr '\n' ' ')"
sudo -u agent sh -c 'mkdir -p /Users/agent/work/demo/item-1 && printf "work\n" > /Users/agent/work/demo/item-1/README.md'
launchctl kickstart -k system/dev.ikigai-rs.hubdiag-view-work 2>/dev/null || true
for i in $(seq 1 30); do
    [ -f /Users/Shared/hubdiag/work/demo/item-1/README.md ] && [ -f /Users/Shared/hubdiag/src/.hubdiag-view-stamp ] && break
    sleep 1
done
if [ -f /Users/Shared/hubdiag/work/demo/item-1/README.md ]; then ok "the LaunchDaemon copied the agent's work"; else bad "no copy of the agent's work after 30 s"; cat /tmp/ikigai-hubdiag-view-work.log 2>/dev/null; fi
echo "   view: $(ls -le /Users/Shared/hubdiag | tr '\n' ' ')"

check() { out="$("$setup" --admin fixadmin --check "${views[@]}" 2>&1)"; st=$?; }
echo "── --check: green"
check
expect_exit "check passes" 0 "$st"; printf '%s\n' "$out" | sed 's/^/     | /'
has "hubdiag reads the work view" "PASS view: hubdiag reads /Users/Shared/hubdiag/work" "$out"
has "hubdiag cannot write it" "PASS view-write: hubdiag cannot write /Users/Shared/hubdiag/work" "$out"

echo "── the deny holds with hubdiag IN staff (the check still fails, on membership)"
dseditgroup -o edit -a hubdiag -t user staff
check
expect_exit "check fails" 1 "$st"
has "membership named" "FAIL staff: hubdiag is a member of staff" "$out"
has "but the home stays closed" "PASS closed: hubdiag cannot traverse /Users/fixadmin" "$out"

echo "── RED: no deny, and hubdiag in staff (macOS's form of the traverse leak)"
chmod -a "user:hubdiag deny list,search,readattr,readextattr,readsecurity" /Users/fixadmin
check
expect_exit "check fails" 1 "$st"
has "names the traverse" "FAIL closed: hubdiag can TRAVERSE /Users/fixadmin" "$out"
has "finds .bash_history" "/Users/fixadmin/.bash_history" "$out"
dseditgroup -o edit -d hubdiag -t user staff
"$setup" --admin fixadmin --key "$T/hub.pub" "${views[@]}" > "$T/repair" 2>&1
check
expect_exit "setup repairs it" 0 "$st"

echo "── what this runner tells us that the man pages did not"
echo "   sleep, as hubdiag: $(cd / && sudo -u hubdiag /bin/bash -c "$(cat "$repo/worker/probe.sh")" probe.sh --sections sleep 2>&1 | tr '\n' ' ')"
echo "   log show as hubdiag: $(sudo -u hubdiag log show --last 5s --style compact 2>&1 | head -2 | tr '\n' ' ')"
echo "   sshd -T for hubdiag: $(sshd -T -C user=hubdiag,host=x,addr=127.0.0.1 2>&1 | grep -iE '^(allowtcpforwarding|permitlisten|clientaliveinterval|denyusers)' | tr '\n' ' ')"
launchctl load -w /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || launchctl bootstrap system /System/Library/LaunchDaemons/ssh.plist 2>/dev/null || true
sleep 2
who="$(ssh -i "$T/hub" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR hubdiag@127.0.0.1 id -un 2>&1)"
echo "   key login as hubdiag (informational, Remote Login may be off here): $who"

echo
echo "ci-macos: $pass passed, $fail failed"
[ "$fail" = 0 ]
