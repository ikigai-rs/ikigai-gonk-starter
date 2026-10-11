#!/bin/bash
# The worker boundary, END TO END on a real Linux machine: a THROWAWAY GitHub-hosted runner.
# Run by ci.yml's `worker boundary (linux)` job; NEVER on a machine anyone uses.
#
# It creates accounts, edits /etc/fstab and sshd's configuration, and mounts things, because
# that is what it tests: worker-setup.sh run for real, its --check green, and then the check
# turned RED by re-creating each mistake it exists to catch, starting with the first worker's
# own (2026-10-10): `setfacl -m u:hubdiag:--x ~` made every world-readable file in the admin's
# home readable by name, ~/.bash_history first. A boundary test that cannot be shown red on
# that is not the acceptance bar; this shows it red, then green again.
#
# Not run by scripts/gates.sh (which runs probe-test.sh and setup-test.sh beside this file, on
# developers' machines too); only the CI job runs it.
set -u

if [ "${GITHUB_ACTIONS:-}" != true ] || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ] || [ "$(uname -s)" != Linux ]; then
    echo "ci-linux.sh: refusing to run outside a GitHub-hosted Linux runner: it creates accounts and" >&2
    echo "  edits /etc/fstab and sshd on the machine it runs on." >&2
    exit 2
fi
[ "$(id -u)" = 0 ] || { echo "ci-linux.sh: run under sudo" >&2; exit 2; }

repo="$(cd "$(dirname "$0")/../.." && pwd)"
setup="$repo/worker/worker-setup.sh"
probe="$repo/worker/probe.sh"
T="$(mktemp -d)"; chmod 0755 "$T"
pass=0 fail=0
ok()   { echo "  ok   $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL $1"; fail=$((fail + 1)); }
expect_exit() { if [ "$2" = "$3" ]; then ok "$1 (exit $3)"; else bad "$1: expected exit $2, got $3"; fi; }
has()  { if printf '%s' "$3" | grep -qF -- "$2"; then ok "$1"; else bad "$1: no '$2' in:"; printf '%s\n' "$3" | sed 's/^/         /' | tail -40; fi; }
hasnt() { if printf '%s' "$3" | grep -qF -- "$2"; then bad "$1: unexpected '$2'"; else ok "$1"; fi; }

echo "── fixture: an admin like the first worker's (home 0750, world-readable files inside)"
command -v sshd >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server >/dev/null
command -v setfacl >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq acl >/dev/null
useradd --create-home --shell /bin/bash fixadmin
chmod 0750 /home/fixadmin
install -d -o fixadmin -g fixadmin /home/fixadmin/.ssh /home/fixadmin/.config/gh /home/fixadmin/src/demo
printf 'ls\nssh somewhere\n' > /home/fixadmin/.bash_history
printf 'not a real key\n' > /home/fixadmin/.ssh/id_ed25519; chmod 0600 /home/fixadmin/.ssh/id_ed25519
printf 'github.com:\n  user: fixture\n' > /home/fixadmin/.config/gh/hosts.yml   # 0644: a leak if reachable
printf '# demo\n' > /home/fixadmin/src/demo/README.md
chown -R fixadmin:fixadmin /home/fixadmin
ssh-keygen -q -t ed25519 -N '' -C hub-fixture -f "$T/hub"

echo "── dry run changes nothing"
out="$("$setup" --dry-run --admin fixadmin --key "$T/hub.pub" --view work=/home/agent/work --view src=/home/fixadmin/src 2>&1)"; st=$?
expect_exit "dry run" 0 "$st"
has "dry run lists the hubdiag account" "would: create account hubdiag" "$out"
has "dry run lists the bind mount" "bind,ro" "$out"
if id hubdiag >/dev/null 2>&1 || id agent >/dev/null 2>&1; then bad "dry run created an account"; else ok "dry run created no account"; fi
grep -q hubdiag /etc/fstab && bad "dry run edited fstab" || ok "dry run left fstab alone"

echo "── setup, then again (idempotent)"
views=(--view work=/home/agent/work --view src=/home/fixadmin/src)
out="$("$setup" --admin fixadmin --key "$T/hub.pub" "${views[@]}" 2>&1)"; st=$?
expect_exit "setup" 0 "$st"; [ "$st" = 0 ] || printf '%s\n' "$out"
out="$("$setup" --admin fixadmin --key "$T/hub.pub" "${views[@]}" 2>&1)"; st=$?
expect_exit "second setup" 0 "$st"
has "second setup changes nothing" "0 change(s) made" "$out"
runuser -u agent -- sh -c 'mkdir -p ~/work/demo/item-1 && printf "work\n" > ~/work/demo/item-1/README.md'

check() { out="$("$setup" --admin fixadmin --check "${views[@]}" 2>&1)"; st=$?; }
echo "── --check on the correct setup: green"
check
expect_exit "check passes" 0 "$st"; [ "$st" = 0 ] || printf '%s\n' "$out" | grep -E 'FAIL|summary'
has "hubdiag reads the agent's work view" "PASS view: hubdiag reads /srv/hubdiag/work" "$out"
has "the view is a read-only mount" "PASS view-mount: /srv/hubdiag/work is mounted read-only" "$out"
has "hubdiag cannot write the agent's tree" "PASS no-write: hubdiag cannot reach /home/agent/work" "$out"
has "agent cannot traverse the admin's home" "PASS closed: agent cannot traverse /home/fixadmin" "$out"
has "the enumeration ran" "can open none of" "$out"
has "facts written" "wrote /srv/hubdiag/facts" "$out"

echo "── RED: the first worker's mistake, traverse on the admin's home for hubdiag"
setfacl -m u:hubdiag:--x /home/fixadmin
check
expect_exit "check fails" 1 "$st"
has "names the traverse" "FAIL closed: hubdiag can TRAVERSE /home/fixadmin" "$out"
has "finds .bash_history by guessing" ".bash_history" "$(printf '%s' "$out" | grep '^FAIL leak: hubdiag can read in /home/fixadmin')"
has "finds it by enumeration too" "/home/fixadmin/.bash_history" "$out"
has "and gh's hosts.yml" "/home/fixadmin/.config/gh/hosts.yml" "$out"
hasnt "never the 0600 key (mode still protects it)" "/home/fixadmin/.ssh/id_ed25519" "$(printf '%s' "$out" | grep -A30 'FAIL leak: hubdiag can open')"
echo "── setup repairs it"
"$setup" --admin fixadmin --key "$T/hub.pub" "${views[@]}" > "$T/repair.out" 2>&1
has "setup removes the ACL entry" "remove ACL entries for hubdiag" "$(cat "$T/repair.out")"
check
expect_exit "check green again" 0 "$st"

echo "── RED: the agent direction, traverse for agent"
setfacl -m u:agent:--x /home/fixadmin
check
expect_exit "check fails" 1 "$st"
has "agent reaches the admin's history" "FAIL leak: agent can read in /home/fixadmin: .bash_history" "$out"
setfacl -x u:agent /home/fixadmin

echo "── RED: a home left world-traversable (pre-21.04 Ubuntu's 0755)"
chmod 0755 /home/fixadmin
check
expect_exit "check fails" 1 "$st"
has "both accounts flagged" "FAIL closed: agent can TRAVERSE /home/fixadmin" "$out"
chmod 0750 /home/fixadmin

echo "── RED: hubdiag able to write the agent's tree"
chmod 0711 /home/agent; chmod 0777 /home/agent/work/demo/item-1; chmod 0666 /home/agent/work/demo/item-1/README.md
check
expect_exit "check fails" 1 "$st"
has "names the writable tree" "FAIL no-write: " "$out"
chmod 0700 /home/agent; chmod 0755 /home/agent/work/demo/item-1; chmod 0644 /home/agent/work/demo/item-1/README.md

echo "── RED: the view remounted read-write"
mount -o remount,bind,rw /srv/hubdiag/src
check
expect_exit "check fails" 1 "$st"
has "names the mount" "FAIL view-mount: /srv/hubdiag/src is not on a read-only mount" "$out"
mount -o remount,bind,ro /srv/hubdiag/src
check
expect_exit "green after every repair" 0 "$st"

echo "── sshd, for real: hubdiag logs in with the key, forwards 1060 and nothing else"
systemctl start ssh 2>/dev/null || systemctl start sshd 2>/dev/null || true
sshopt=(-i "$T/hub" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR)
who="$(ssh "${sshopt[@]}" hubdiag@127.0.0.1 id -un 2>&1)"
has "key login as hubdiag" "hubdiag" "$who"
ssh "${sshopt[@]}" -N -o ExitOnForwardFailure=yes -R 1060:127.0.0.1:22 hubdiag@127.0.0.1 & fwd=$!
sleep 3
if kill -0 "$fwd" 2>/dev/null; then ok "remote forward on 1060 is allowed"; kill "$fwd"; else bad "remote forward on 1060 was refused"; fi
wait "$fwd" 2>/dev/null
ssh "${sshopt[@]}" -N -o ExitOnForwardFailure=yes -R 2222:127.0.0.1:22 hubdiag@127.0.0.1 > "$T/r2222" 2>&1 & f2=$!
sleep 3
if kill -0 "$f2" 2>/dev/null; then bad "remote forward on 2222 was allowed"; kill "$f2"; else ok "remote forward on 2222 is refused"; fi
wait "$f2" 2>/dev/null
ssh "${sshopt[@]}" -N -L 3333:127.0.0.1:22 hubdiag@127.0.0.1 & lf=$!
sleep 3
banner="$(timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/3333 && head -c 4 <&3' 2>/dev/null)"
kill "$lf" 2>/dev/null; wait "$lf" 2>/dev/null
if [ "$banner" = "SSH-" ]; then bad "a local forward reached sshd"; else ok "a local forward carries nothing (AllowTcpForwarding remote)"; fi
install -d -m 0700 -o agent -g agent /home/agent/.ssh
install -m 0600 -o agent -g agent "$T/hub.pub" /home/agent/.ssh/authorized_keys
if ssh "${sshopt[@]}" agent@127.0.0.1 true 2>/dev/null; then bad "agent logged in over ssh"; else ok "agent cannot log in over ssh (DenyUsers), even with a key"; fi
rm -rf /home/agent/.ssh

echo "── the hub's view: probe.sh over that ssh, as hubdiag (the worker needs no copy of it)"
hubprobe() {
    out="$(ssh "${sshopt[@]}" hubdiag@127.0.0.1 bash -s -- \
        --sections identity,closed,view,nowrite,journal,linger,sleep \
        --closed /home/fixadmin --closed /home/agent \
        --view /srv/hubdiag/work --no-write /home/agent/work \
        --view /srv/hubdiag/src --no-write /home/fixadmin/src \
        --forbid-group sudo --forbid-group adm --forbid-group docker \
        --expect-group systemd-journal --linger agent < "$probe" 2>&1)"; st=$?
}
hubprobe
expect_exit "a runner that can suspend fails the probe" 1 "$st"
has "naming the target" "FAIL sleep: suspend.target is" "$out"
has "it ran as hubdiag" "(hubdiag) gid=" "$out"
echo "── the runbook's fix: mask the sleep targets"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target >/dev/null 2>&1
hubprobe
expect_exit "the probe passes" 0 "$st"; [ "$st" = 0 ] || printf '%s\n' "$out" | grep -E 'FAIL|WARN'
has "masked is the pass state" "PASS sleep: suspend.target is masked" "$out"
has "hubdiag reads the journal" "PASS journal: hubdiag reads the system journal" "$out"
facts="$(ssh "${sshopt[@]}" hubdiag@127.0.0.1 cat /srv/hubdiag/facts 2>&1)"
has "hubdiag reads the facts the root-side check wrote" "result: PASS" "$facts"
has "which name the gh holders, by class" "INFO gh-token" "$facts"
has "with the check's date" "checked: " "$facts"
setfacl -m u:hubdiag:--x /home/fixadmin
hubprobe
expect_exit "the probe fails on the first worker's mistake" 1 "$st"
has "and names .bash_history" ".bash_history" "$(printf '%s' "$out" | grep '^FAIL leak')"
setfacl -x u:hubdiag /home/fixadmin

echo
echo "ci-linux: $pass passed, $fail failed"
[ "$fail" = 0 ]
