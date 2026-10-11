#!/bin/bash
# worker-setup.sh: make this machine an agentic WORKER, run ON it by its admin.
#
#   sudo ./worker-setup.sh --key hub.pub                        set up (idempotent)
#   ./worker-setup.sh --dry-run --key hub.pub                   print every change, make none
#   sudo ./worker-setup.sh --check                              the boundary self-test
#
# What it builds (Linux with systemd, and macOS), each step skipped when already in place:
#
#   hubdiag   the HUB's account: read-only by Unix permissions, not by convention. No sudo, no
#             password (key-only ssh, the key file you pass), on Linux in systemd-journal so it
#             reads every unit's journal. Its authorized_keys is ROOT-owned, so a stolen hub key
#             cannot add a second key. sshd lets it forward one remote port (the gonk tunnel) and
#             nothing else.
#   agent     the WORKER's account (by design, the worker never works in a person's
#             checkouts). No sudo, no password, no ssh login. It will hold the fine-grained
#             GitHub token and a fresh worktree per claimed item under ~agent/work. Linux: linger
#             on, so its user units run with nobody logged in.
#   views     what hubdiag may read: by default ~agent/work, optionally more (--view). NEVER by
#             granting traverse of a home. Linux: a read-only BIND MOUNT under /srv/hubdiag.
#             macOS has no bind mount: a copy under /Users/Shared/hubdiag, refreshed every five
#             minutes by a LaunchDaemon running as the source's owner (README: why not the rest).
#   homes     the admin's home is closed to both accounts: Linux by mode (o-rwx) and by removing
#             any ACL entry naming them; macOS by an ACL DENY of traverse for each (a standard
#             macOS user is in `staff`, which may read a home; these two are not in staff, and
#             the deny holds even if one is ever added).
#   sshd      ClientAliveInterval 10 / CountMax 3 (a dead tunnel frees its port in ~30 s), and a
#             Match block for hubdiag, in a drop-in under /etc/ssh/sshd_config.d, validated with
#             `sshd -t` before it is kept.
#
# THE LEAK THIS EXISTS TO PREVENT (the first worker, 2026-10-10): its setup granted hubdiag
# `setfacl -m u:hubdiag:--x ~` so it could reach a repo tree in it. Pop!_OS homes are closed to
# others, so the files inside are created world-readable, and traverse made every GUESSABLE one
# readable: ~/.bash_history first. `--check` hunts exactly that class (see probe.sh).
#
# It never types a password: run it as root (sudo). Without root it prints the command it needs
# and exits 2, except under --dry-run, which only reads (and, not under sudo, takes the invoking
# user as the admin unless --admin says otherwise, and says so). It installs no
# tools and stores no token: those are the runbook's (README.md), by hand, as the account that
# owns them.
#
# Options:
#   --key FILE        hubdiag's public key (one line). Required the first time.
#   --admin USER      the person whose home is closed (default: $SUDO_USER; not under sudo and
#                     not root, the invoking user)
#   --hubdiag NAME    default hubdiag        --agent NAME   default agent
#   --view NAME=DIR   a read-only view for hubdiag; repeatable. Default: work=<agent home>/work.
#                     e.g. --view work=/home/agent/work --view src=/home/<admin>/src
#                     A view whose source is under the admin's home is also checked, as the
#                     agent, to be unwritable by the agent.
#   --listen PORT     the remote forward hubdiag may open (default 1060, gonk's tunnel); repeatable
#   --dry-run         print what would change; change nothing
#   --check           the boundary self-test (needs root: it acts AS hubdiag and AS agent)
#   --fstab FILE      read the mount table from FILE, not /etc/fstab. TEST-ONLY, used by
#                     tests/setup-test.sh so a dry run never reads the host's real /etc/fstab.
#
# ⚠ bash 3.2 (macOS /bin/bash): no associative arrays, no mapfile.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
os="$(uname -s)"
orig=("$@")
key="" admin="${SUDO_USER:-}" hubdiag=hubdiag agent=agent dry=0 check=0 fstab=/etc/fstab
views=() listens=()
while [ $# -gt 0 ]; do
    case "$1" in
        --key) key="$2"; shift 2 ;;
        --admin) admin="$2"; shift 2 ;;
        --hubdiag) hubdiag="$2"; shift 2 ;;
        --agent) agent="$2"; shift 2 ;;
        --view) views+=("$2"); shift 2 ;;
        --listen) listens+=("$2"); shift 2 ;;
        --dry-run) dry=1; shift ;;
        --check) check=1; shift ;;
        --fstab) fstab="$2"; shift 2 ;;   # tests: the mount table the views step reads
        -h|--help) sed -n '2,57p' "$0"; exit 0 ;;
        *) echo "worker-setup: unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done
[ "${#listens[@]}" -gt 0 ] || listens=(1060)

die() { echo "worker-setup: $*" >&2; exit 1; }
case "$os" in Linux|Darwin) ;; *) die "unsupported OS $os (Linux with systemd, or macOS)" ;; esac
# Not under sudo and not root (the documented first step, `--dry-run`, needs neither): the
# person running it is the admin. Root with no SUDO_USER (a root login) still has to say who.
if [ -z "$admin" ] && [ "$(id -u)" != 0 ]; then
    admin="$(id -un)"
    echo "worker-setup: --admin not given and SUDO_USER is unset: assuming the admin is $admin (you)"
fi
[ -n "$admin" ] || die "who is the admin? pass --admin <user> (SUDO_USER is unset)"
[ "$admin" != root ] || die "--admin is the PERSON whose home is closed, not root"
id "$admin" >/dev/null 2>&1 || die "no such user: $admin"
[ "$admin" != "$hubdiag" ] && [ "$admin" != "$agent" ] && [ "$hubdiag" != "$agent" ] \
    || die "admin, hubdiag and agent must be three different accounts"

if [ "$(id -u)" != 0 ] && [ "$dry" = 0 ]; then
    echo "worker-setup: needs root to $( [ "$check" = 1 ] && echo 'act as hubdiag and agent' || echo 'create accounts and mounts' ). Run:"
    echo "    sudo $0 ${orig[*]+${orig[*]}}"
    echo "(or --dry-run, which needs no root and changes nothing)"
    exit 2
fi

home_of() { # the home of an account, or the platform default for one not created yet
    local h
    if [ "$os" = Darwin ]; then
        h="$(dscl . -read "/Users/$1" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
        echo "${h:-/Users/$1}"
    else
        h="$(getent passwd "$1" | cut -d: -f6)"
        echo "${h:-/home/$1}"
    fi
}
admin_home="$(home_of "$admin")"
agent_home="$(home_of "$agent")"
hub_home="$(home_of "$hubdiag")"
[ "${#views[@]}" -gt 0 ] || views=("work=$agent_home/work")

if [ "$os" = Darwin ]; then view_root=/Users/Shared/hubdiag; else view_root=/srv/hubdiag; fi
facts="$view_root/facts"
dropin=/etc/ssh/sshd_config.d/10-ikigai-worker.conf

# ── the change vocabulary: every mutation goes through `change`, so --dry-run is complete ──
changes=0
change() { # description, then the command
    local what="$1"; shift
    changes=$((changes + 1))
    if [ "$dry" = 1 ]; then
        echo "would: $what"
        echo "       \$ $*"
    else
        echo "  +  $what"
        "$@" || die "failed: $*"
    fi
}
have() { echo "  ok $1"; }

user_exists() { id "$1" >/dev/null 2>&1; }

# ── views: NAME=DIR, validated once ──
for v in "${views[@]}"; do
    case "$v" in
        *=/*) n="${v%%=*}"; case "$n" in ""|*/*|.*) die "bad view name in $v" ;; esac ;;
        *) die "--view wants NAME=/absolute/dir, got $v" ;;
    esac
done

sshd_dropin_content() {
    cat <<EOF
# Written by worker/worker-setup.sh (ikigai-gonk-starter). Re-run that script
# rather than editing this file; it is replaced whole.
#
# A dead session must release a forwarded port quickly (the gonk tunnel re-binds it): ~30 s.
ClientAliveInterval 10
ClientAliveCountMax 3
# The worker account never logs in over ssh; its admin reaches it with sudo -iu $agent.
DenyUsers $agent

# The hub's read-only account: a key, a shell, one remote forward, nothing else.
Match User $hubdiag
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    X11Forwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTunnel no
    AllowTcpForwarding remote
    PermitListen $(for p in "${listens[@]}"; do printf 'localhost:%s\n' "$p"; done | paste -sd' ' -)
# End the Match block here, so nothing read after this file is captured by it.
Match all
EOF
}

# sshd -t/-T on a socket-activated Ubuntu refuses with "Missing privilege separation directory:
# /run/sshd" until sshd has run once since boot; that directory is runtime state ssh.service
# itself creates (RuntimeDirectory=sshd), so creating it empty changes nothing persistent.
privsep() { if [ "$os" = Linux ] && [ ! -d /run/sshd ] && [ "$(id -u)" = 0 ]; then mkdir -p -m 0755 /run/sshd; fi; }

# ════════════════════════════════════════════════════════════════════════════════════════════
# --check: the boundary self-test, as each account, plus facts the hub cannot read for itself.
# ════════════════════════════════════════════════════════════════════════════════════════════
as_user() { # user, then a command; never prompts
    local u="$1"; shift
    if [ "$os" = Linux ] && command -v runuser >/dev/null 2>&1; then
        (cd / && runuser -u "$u" -- "$@")
    else
        (cd / && sudo -n -H -u "$u" "$@")
    fi
}
probe_as() { # user, then probe arguments; the probe's stdin is this function's stdin
    local u="$1"; shift
    as_user "$u" /bin/bash -c "$(cat "$here/probe.sh")" probe.sh "$@"
}
enumerate() { # every regular file under the given homes, minus build output and caches
    local h
    for h in "$@"; do
        [ -d "$h" ] || continue
        find "$h" \( -name target -o -name node_modules -o -path "$h/.rustup/toolchains" \
                     -o -path "$h/.cargo/registry" -o -path "$h/.cache" -o -path "$h/Library/Caches" \) -prune \
             -o -type f -print 2>/dev/null
    done
}

newest_bin() { # home → "<UTC mtime> <path>" of the newest file in its ~/.cargo/bin and ~/.local/bin
    local h="$1" f m best="" bm=0
    for f in "$h"/.cargo/bin/* "$h"/.local/bin/*; do
        [ -e "$f" ] || continue
        if [ "$os" = Darwin ]; then m="$(stat -f %m "$f" 2>/dev/null)"; else m="$(stat -c %Y "$f" 2>/dev/null)"; fi
        [ -n "$m" ] && [ "$m" -gt "$bm" ] && { bm="$m"; best="$f"; }
    done
    [ -n "$best" ] || return 0
    if [ "$os" = Darwin ]; then echo "$(date -u -r "$bm" +%Y-%m-%dT%H:%M:%SZ) $best"
    else echo "$(date -u -d "@$bm" +%Y-%m-%dT%H:%M:%SZ) $best"; fi
}

if [ "$check" = 1 ]; then
    [ "$(id -u)" = 0 ] || die "--check needs root"
    user_exists "$hubdiag" || die "no $hubdiag account: run the setup first"
    status=0 out="" tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    chmod 0755 "$tmp"
    vdirs=() srcs=()
    for v in "${views[@]}"; do vdirs+=(--view "$view_root/${v%%=*}"); srcs+=(--no-write "${v#*=}"); done
    if [ "$os" = Linux ]; then
        forbid="sudo admin wheel adm docker lxd libvirt staff"; journal=(--expect-group systemd-journal)
    else
        forbid="admin wheel staff _developer"; journal=()
    fi
    fg=(); for g in $forbid; do fg+=(--forbid-group "$g"); done

    echo "── as $hubdiag: the hub's view of this machine"
    echo "   (enumerating $admin_home and $agent_home as root; the list goes to the probe on stdin)"
    enumerate "$admin_home" "$agent_home" > "$tmp/cands"
    echo "   $(wc -l < "$tmp/cands" | tr -d ' ') files to try"
    probe_as "$hubdiag" --sections identity,closed,view,nowrite,journal \
        --closed "$admin_home" --closed "$agent_home" "${vdirs[@]}" "${srcs[@]}" \
        "${fg[@]}" ${journal[@]+"${journal[@]}"} --candidates < "$tmp/cands" | tee "$tmp/hubdiag.out"
    grep -q '^FAIL' "$tmp/hubdiag.out" && status=1

    if user_exists "$agent"; then
        echo; echo "── as $agent: the worker cannot read its person's home or write their trees"
        enumerate "$admin_home" > "$tmp/cands-admin"
        # The admin's trees the hub may read (views whose source is in the admin's home) are
        # the ones the agent must also not write.
        aw=()
        for v in "${views[@]}"; do
            case "${v#*=}" in "$admin_home"/*) aw+=(--no-write "${v#*=}") ;; esac
        done
        probe_as "$agent" --sections identity,closed,nowrite \
            --closed "$admin_home" ${aw[@]+"${aw[@]}"} "${fg[@]}" \
            --candidates < "$tmp/cands-admin" | tee "$tmp/agent.out"
        grep -q '^FAIL' "$tmp/agent.out" && status=1
        if [ "$os" = Linux ]; then
            probe_as "$agent" --sections linger --linger "$agent" < /dev/null | grep -v '^summary' | tee -a "$tmp/agent.out"
            grep -q '^FAIL linger' "$tmp/agent.out" && status=1
        fi
    else
        echo; echo "FAIL agent: no $agent account"; status=1
    fi

    echo; echo "── sshd: the effective configuration for $hubdiag"
    privsep
    eff="$(sshd -T -C "user=$hubdiag,host=worker-check,addr=127.0.0.1" 2>&1)"
    if [ $? -ne 0 ]; then
        echo "FAIL sshd: sshd -T refused: $(echo "$eff" | head -2 | tr '\n' ' ')"; status=1
    else
        for want in "clientaliveinterval 10" "clientalivecountmax 3" "passwordauthentication no" \
                    "x11forwarding no" "allowagentforwarding no" "allowtcpforwarding remote" \
                    "permittunnel no" "allowstreamlocalforwarding no"; do
            if echo "$eff" | grep -qix "$want"; then echo "PASS sshd: $want"
            else echo "FAIL sshd: wanted '$want', effective: $(echo "$eff" | grep -i "^${want%% *} " | head -1)"; status=1; fi
        done
        if echo "$eff" | grep -i '^denyusers' | grep -qw "$agent"; then echo "PASS sshd: denyusers $agent"
        else echo "FAIL sshd: $agent is not in DenyUsers"; status=1; fi
    fi
    if [ "$os" = Darwin ]; then
        for u in "$hubdiag" "$agent"; do
            user_exists "$u" || continue
            if dsmemberutil checkmembership -U "$u" -G staff 2>/dev/null | grep -q 'is a member'; then
                echo "FAIL staff: $u is a member of staff (dsmemberutil), which may read every home"; status=1
            else echo "PASS staff: $u is not a member of staff"; fi
        done
        if dscl . -read /Groups/com.apple.access_ssh >/dev/null 2>&1; then
            if dsmemberutil checkmembership -U "$hubdiag" -G com.apple.access_ssh 2>/dev/null | grep -q 'is a member'; then
                echo "PASS ssh-acl: $hubdiag may use Remote Login"
            else echo "FAIL ssh-acl: $hubdiag is not in com.apple.access_ssh (Remote Login 'only these users')"; status=1; fi
        fi
    fi

    # Facts the hub cannot read for itself (it is hubdiag, and the homes are closed to it):
    # which account holds a gh token and of what kind, and what each account has installed.
    echo; echo "── facts for the hub (written to $facts)"
    {
        echo "# worker facts, written by worker-setup.sh --check as root. No credential appears here:"
        echo "# a gh token is reported by its prefix class only."
        echo "checked: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "result: $( [ "$status" = 0 ] && echo PASS || echo FAIL )"
        grep -h '^FAIL' "$tmp"/*.out 2>/dev/null | sed 's/^/  /'
        for u in "$agent" "$admin"; do
            user_exists "$u" || continue
            echo "[$u]"
            probe_as "$u" --sections gh,tools,agents --home "$(home_of "$u")" < /dev/null 2>/dev/null | grep '^INFO'
            # The newest program in this account's own bin directories, now. hubdiag cannot see
            # them (the home is closed to it), so this is how worker-check can say what an
            # install made after this check would NOT be reflected in.
            newest="$(newest_bin "$(home_of "$u")")"
            [ -n "$newest" ] && echo "newest-bin: $newest"
        done
    } > "$tmp/facts"
    cat "$tmp/facts" | grep -E '^(checked|result|\[|INFO gh-token)'
    if [ -d "$view_root" ]; then
        install -m 0640 -o root -g "$(id -gn "$hubdiag")" "$tmp/facts" "$facts" && echo "   wrote $facts"
    else
        echo "   (no $view_root yet: facts not written)"
    fi
    echo
    if [ "$status" = 0 ]; then echo "worker-setup --check: PASS"; else echo "worker-setup --check: FAIL"; fi
    exit "$status"
fi

# ════════════════════════════════════════════════════════════════════════════════════════════
# Setup. Each step tests the state first and changes only what differs.
# ════════════════════════════════════════════════════════════════════════════════════════════
scratch="$(mktemp -d)"; trap 'rm -rf "$scratch"' EXIT
[ "$dry" = 1 ] && echo "worker-setup: DRY RUN on $(hostname) ($os): nothing below is changed"
echo "admin=$admin ($admin_home)  hubdiag=$hubdiag  agent=$agent ($agent_home)"
for v in "${views[@]}"; do echo "view ${v%%=*}: ${v#*=} → $view_root/${v%%=*}"; done

if [ -n "$key" ]; then
    [ -f "$key" ] || die "no key file $key"
    [ "$(grep -cv '^[[:space:]]*\(#.*\)\{0,1\}$' "$key")" = 1 ] || die "$key must hold exactly one public key line"
    ssh-keygen -l -f "$key" >/dev/null 2>&1 || die "$key is not a public key (ssh-keygen -l refused it)"
    grep -q 'PRIVATE KEY' "$key" && die "$key is a PRIVATE key; pass the .pub"
fi

# ── Linux ────────────────────────────────────────────────────────────────────────────────────
linux_account() { # name, comment, supplementary groups (comma list, may be empty)
    local u="$1" c="$2" groups="$3" cur
    if user_exists "$u"; then have "account $u"
    else
        # useradd, not adduser: adduser on Ubuntu/Pop also puts the account in `users`
        # (a hubdiag made that way by hand was), and its flags moved (--gecos → --comment in 24.04).
        change "create account $u" useradd --create-home --shell /bin/bash --comment "$c" "$u"
    fi
    # `*` is no valid password (no password login), and unlike `!` it is not "locked", which
    # sshd without PAM would refuse even for a key.
    # hubdiag logs in with its key; agent has no ssh login at all (DenyUsers, below), so its
    # admin reaches it with sudo -iu or machinectl shell ("key-only" misled once).
    local how="key-only ssh"
    [ "$u" = "$agent" ] && how="no ssh login: sudo -iu $u / machinectl shell $u@ only"
    if [ "$(id -u)" != 0 ]; then echo "  ?  $u password state (unreadable without root; setup sets it to '*': no password, $how)"
    elif [ "$(getent shadow "$u" 2>/dev/null | cut -d: -f2)" = '*' ]; then have "$u has no password ($how)"
    else change "$u: no password ($how)" usermod -p '*' "$u"; fi
    cur="$(id -Gn "$u" 2>/dev/null | tr ' ' '\n' | grep -vx "$u" | sort | paste -sd, -)"
    if [ "$cur" = "$groups" ] && user_exists "$u"; then have "$u supplementary groups: ${groups:-none}"
    else change "$u supplementary groups: ${cur:-none} → ${groups:-none} (exactly)" usermod -G "$groups" "$u"; fi
}
linux_home_closed() { # the admin's home: no other bits, and no ACL entry for our two accounts
    local mode u
    mode="$(stat -c %a "$admin_home")"
    case "$mode" in
        *0) have "$admin_home has no 'other' bits ($mode)" ;;
        *) change "$admin_home: remove 'other' access ($mode → o-rwx)" chmod o-rwx "$admin_home" ;;
    esac
    if command -v getfacl >/dev/null 2>&1; then
        for u in "$hubdiag" "$agent"; do
            if getfacl -cp "$admin_home" 2>/dev/null | grep -q "^\(default:\)\{0,1\}user:$u:"; then
                change "$admin_home: remove ACL entries for $u (the traverse leak)" setfacl -x "u:$u" -x "d:u:$u" "$admin_home"
            else have "$admin_home has no ACL entry for $u"; fi
        done
    fi
}
linux_view() { # name, source
    local n="$1" src="$2" dst="$view_root/$1" line
    line="$src $dst none bind,ro 0 0"
    [ -d "$src" ] || [ "$dry" = 1 ] || die "view source $src does not exist"
    if [ -d "$dst" ]; then have "mount point $dst"; else change "mount point $dst" install -d -m 0755 "$dst"; fi
    if awk -v d="$dst" '$2 == d' "$fstab" | grep -q .; then
        if awk -v d="$dst" '$2 == d' "$fstab" | grep -q "^$src $dst none bind,ro "; then have "fstab: $line"
        else die "$fstab already mounts something else at $dst: fix it by hand: $(awk -v d="$dst" '$2 == d' "$fstab")"; fi
    else
        change "fstab: $line" sh -c "printf '%s\n' '$line' >> $fstab"
        change "systemd reads the new fstab" systemctl daemon-reload
    fi
    local opts; opts="$(findmnt -no OPTIONS "$dst" 2>/dev/null)"
    if [ -z "$opts" ]; then change "mount $dst (read-only bind of $src)" mount "$dst"
    elif echo ",$opts," | grep -q ',ro,'; then have "$dst mounted read-only"
    else change "remount $dst read-only" mount -o remount,bind,ro "$dst"; fi
}

# ── macOS ────────────────────────────────────────────────────────────────────────────────────
free_id() { # first id >= $1 free as BOTH a uid and a gid, so an account's group can share it
    local i="$1"
    while dscl . -search /Users UniqueID "$i" 2>/dev/null | grep -q . \
       || dscl . -search /Groups PrimaryGroupID "$i" 2>/dev/null | grep -q .; do i=$((i + 1)); done
    echo "$i"
}
mac_account() { # name, real name
    local u="$1" rn="$2" id gid
    if dscl . -read "/Groups/$u" PrimaryGroupID >/dev/null 2>&1; then have "group $u"
    else
        id="$(free_id 550)"
        change "create group $u (gid $id$( [ "$dry" = 1 ] && echo ', or the next free one'): its own primary group, so it is NOT in staff" \
            dseditgroup -o create -i "$id" -r "$rn" "$u"
    fi
    if user_exists "$u"; then have "account $u"
    else
        gid="$(dscl . -read "/Groups/$u" PrimaryGroupID 2>/dev/null | awk '{print $2}')"
        [ -n "$gid" ] || gid="(the new group's gid)"
        id="$(free_id 550)"
        # dscl, not sysadminctl: sysadminctl has no man page to verify against and always sets
        # a password; these records give an account with NO password (`*`), hidden from the
        # login window, whose primary group is its own.
        change "create account $u (uid $id)" sh -c "
            dscl . -create /Users/$u &&
            dscl . -create /Users/$u RealName '$rn' &&
            dscl . -create /Users/$u UniqueID $id &&
            dscl . -create /Users/$u PrimaryGroupID \$(dscl . -read /Groups/$u PrimaryGroupID | awk '{print \$2}') &&
            dscl . -create /Users/$u UserShell /bin/zsh &&
            dscl . -create /Users/$u NFSHomeDirectory /Users/$u &&
            dscl . -create /Users/$u Password '*' &&
            dscl . -create /Users/$u IsHidden 1"
    fi
    if [ -d "/Users/$u" ]; then have "home /Users/$u"
    else change "home /Users/$u" install -d -m 0750 -o "$u" -g "$u" "/Users/$u"; fi
    if dscl . -read /Groups/staff GroupMembership 2>/dev/null | tr ' ' '\n' | grep -qx "$u"; then
        change "remove $u from staff (staff may read every home)" dseditgroup -o edit -d "$u" -t user staff
    else have "$u is not listed in staff"; fi
}
mac_home_closed() {
    local u
    for u in "$hubdiag" "$agent"; do
        if ls -led "$admin_home" | grep -q "user:$u deny .*search"; then have "$admin_home denies $u traverse (ACL)"
        else
            change "$admin_home: ACL deny $u traverse and listing" \
                chmod +a "user:$u deny list,search,readattr,readextattr,readsecurity" "$admin_home"
        fi
    done
}
mac_view() { # name, source
    local n="$1" src="$2" dst="$view_root/$1" owner label plist tmpl
    [ -d "$src" ] || [ "$dry" = 1 ] || die "view source $src does not exist"
    owner="$(stat -f %Su "$src" 2>/dev/null || echo "$agent")"
    label="dev.ikigai-rs.hubdiag-view-$n"
    plist="/Library/LaunchDaemons/$label.plist"
    if [ -d "$dst" ]; then have "view dir $dst"
    else change "view dir $dst (owner $owner, group $hubdiag, 0750)" install -d -m 0750 -o "$owner" -g "$hubdiag" "$dst"; fi
    tmpl="$(mac_view_plist "$label" "$owner" "$src" "$dst")"
    if [ -f "$plist" ] && [ "$(cat "$plist")" = "$tmpl" ]; then have "LaunchDaemon $label"
    else
        printf '%s\n' "$tmpl" > "$scratch/$label.plist"
        change "LaunchDaemon $plist: refresh $dst from $src every 5 min, as $owner" \
            install -m 0644 -o root -g wheel "$scratch/$label.plist" "$plist"
        # bootout returns before the job is gone and fails when it was not loaded, so never
        # chain it to bootstrap with && (field guide 9h).
        change "load $label" sh -c "launchctl bootout system/$label 2>/dev/null; sleep 2; launchctl bootstrap system '$plist'"
    fi
}
mac_view_plist() { # label owner src dst: the refresh job. ⚠ no `--` inside the XML comment.
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Written by worker/worker-setup.sh (ikigai-gonk-starter): hubdiag's read-only copy of $3.
     macOS has no bind mount; this copies, as $2, into a directory only $2 and hubdiag can open.
     New files take the directory's group (hubdiag) on macOS; the copy drops every write bit but
     the owner's and every "other" bit, so hubdiag reads by group and nobody else reads at all.
     The find is not redundant: openrsync (macOS 15+) applies chmod to FILES only, whatever its
     man page says of the D prefix, so a directory keeps its source mode (measured 2026-10-10).
     Up to five minutes stale; the stamp says when. -->
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>UserName</key><string>$2</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>/usr/bin/rsync -rlpt --delete --chmod=go-w,o-rwx --exclude target/ --exclude node_modules/ '$3/' '$4/' &amp;&amp; /usr/bin/find '$4' -perm +0027 -exec /bin/chmod go-w,o-rwx {} + &amp;&amp; date -u +%Y-%m-%dT%H:%M:%SZ &gt; '$4/.hubdiag-view-stamp'</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>/tmp/ikigai-hubdiag-view-$(basename "$4").log</string>
  <key>StandardErrorPath</key><string>/tmp/ikigai-hubdiag-view-$(basename "$4").log</string>
</dict>
</plist>
EOF
}

# ── hubdiag's key: root-owned, so the account cannot add keys to itself ──
install_key() {
    local d="$hub_home/.ssh" f="$hub_home/.ssh/authorized_keys"
    if [ -d "$d" ] && [ "$(stat_owner "$d")" = root ]; then have "$d is root-owned"
    else change "$d: root-owned 0755 (sshd StrictModes accepts root)" sh -c "install -d -m 0755 '$d' && chown root '$d'"; fi
    if [ -z "$key" ]; then
        if [ -f "$f" ]; then have "$f (no --key given; kept)"; else die "first run needs --key <public key file>"; fi
        return
    fi
    if [ -f "$f" ] && cmp -s "$key" "$f"; then have "$f holds the given key"
    else
        [ -f "$f" ] && echo "     replacing $(ssh-keygen -l -f "$f" 2>/dev/null | head -1)"
        change "$f ← $(ssh-keygen -l -f "$key" | awk '{print $2, $3}')" install -m 0644 -o root "$key" "$f"
    fi
}
stat_owner() { if [ "$os" = Darwin ]; then stat -f %Su "$1"; else stat -c %U "$1"; fi; }

install_sshd() {
    [ -d /etc/ssh/sshd_config.d ] || die "no /etc/ssh/sshd_config.d: is OpenSSH server installed? (Linux: apt install openssh-server)"
    grep -Eq '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*' /etc/ssh/sshd_config \
        || die "/etc/ssh/sshd_config does not Include sshd_config.d/*; add 'Include /etc/ssh/sshd_config.d/*.conf' as its FIRST line, by hand"
    local want; want="$(sshd_dropin_content)"
    if [ -f "$dropin" ] && [ "$(cat "$dropin")" = "$want" ]; then have "sshd drop-in $dropin"; return; fi
    if [ "$dry" = 1 ]; then
        change "sshd drop-in $dropin" true
        echo "$want" | sed 's/^/       | /'
        return
    fi
    [ -f "$dropin" ] && cp -p "$dropin" "$dropin.prev"
    printf '%s\n' "$want" > "$dropin" && chmod 0644 "$dropin"
    privsep
    if ! out="$(sshd -t 2>&1)"; then
        if [ -f "$dropin.prev" ]; then mv "$dropin.prev" "$dropin"; else rm -f "$dropin"; fi
        die "sshd -t refused the drop-in, restored the previous state: $out"
    fi
    rm -f "$dropin.prev"
    echo "  +  sshd drop-in $dropin (sshd -t accepts it)"
    if [ "$os" = Linux ]; then
        # Ubuntu 24.04 socket-activates ssh; try-reload-or-restart touches only a running sshd.
        systemctl try-reload-or-restart ssh 2>/dev/null || systemctl try-reload-or-restart sshd 2>/dev/null || true
        echo "  +  sshd reloaded (if it was running)"
    else
        echo "     (macOS: launchd starts sshd per connection, so the next login reads it; no reload)"
    fi
}

if [ "$os" = Linux ]; then
    getent group systemd-journal >/dev/null || die "no systemd-journal group: this needs a systemd distribution"
    echo; echo "── hubdiag"
    linux_account "$hubdiag" "hub diagnostics (read-only)" systemd-journal
    install_key
    echo; echo "── agent"
    linux_account "$agent" "ikigai worker agent" ""
    if user_exists "$agent"; then
        m="$(stat -c %a "$agent_home" 2>/dev/null)"
        if [ "$m" = 700 ]; then have "$agent_home is 0700"; else change "$agent_home: 0700" chmod 0700 "$agent_home"; fi
    else
        change "$agent_home: 0700" chmod 0700 "$agent_home"
    fi
    if [ -d "$agent_home/work" ]; then have "$agent_home/work"
    else change "$agent_home/work (0755, the worktrees)" install -d -m 0755 -o "$agent" -g "$agent" "$agent_home/work"; fi
    if [ -e "/var/lib/systemd/linger/$agent" ]; then have "$agent lingers"
    else change "linger for $agent (user units run with nobody logged in)" loginctl enable-linger "$agent"; fi
    echo; echo "── the admin's home ($admin_home)"
    linux_home_closed
    echo; echo "── views"
    if [ -d "$view_root" ] && [ "$(stat -c '%U:%G %a' "$view_root")" = "root:$hubdiag 750" ]; then have "$view_root root:$hubdiag 0750"
    else change "$view_root: root:$hubdiag 0750 (only hubdiag traverses to the views)" \
        sh -c "install -d '$view_root' && chown root:'$hubdiag' '$view_root' && chmod 0750 '$view_root'"; fi
    for v in "${views[@]}"; do linux_view "${v%%=*}" "${v#*=}"; done
else
    echo; echo "── hubdiag"
    mac_account "$hubdiag" "hub diagnostics (read-only)"
    install_key
    if dscl . -read /Groups/com.apple.access_ssh >/dev/null 2>&1; then
        if dscl . -read /Groups/com.apple.access_ssh GroupMembership 2>/dev/null | tr ' ' '\n' | grep -qx "$hubdiag"; then
            have "$hubdiag in com.apple.access_ssh"
        else change "$hubdiag may use Remote Login (com.apple.access_ssh)" dseditgroup -o edit -a "$hubdiag" -t user com.apple.access_ssh; fi
    else
        echo "  !  no com.apple.access_ssh group: Remote Login is off, or allows ALL users."
        echo "     Turn it on for 'Only these users' in System Settings > General > Sharing, then re-run."
    fi
    echo; echo "── agent"
    mac_account "$agent" "ikigai worker agent"
    if [ -d "/Users/$agent" ] && [ "$(stat -f %Lp "/Users/$agent")" = 700 ]; then have "/Users/$agent is 0700"
    else change "/Users/$agent: 0700" chmod 0700 "/Users/$agent"; fi
    if [ -d "/Users/$agent/work" ]; then have "/Users/$agent/work"
    else change "/Users/$agent/work (0755, the worktrees)" install -d -m 0755 -o "$agent" -g "$agent" "/Users/$agent/work"; fi
    echo; echo "── the admin's home ($admin_home)"
    mac_home_closed
    echo; echo "── views"
    if [ -d "$view_root" ] && [ "$(stat -f '%Su:%Sg %Lp' "$view_root")" = "root:$hubdiag 751" ]; then have "$view_root root:$hubdiag 0751"
    else change "$view_root: root:$hubdiag 0751 (owners traverse to refresh, only hubdiag lists)" \
        sh -c "install -d '$view_root' && chown root:'$hubdiag' '$view_root' && chmod 0751 '$view_root'"; fi
    for v in "${views[@]}"; do mac_view "${v%%=*}" "${v#*=}"; done
fi

echo; echo "── sshd"
install_sshd

echo
if [ "$dry" = 1 ]; then
    echo "worker-setup: DRY RUN: $changes change(s) listed above; nothing was changed."
    echo "  next, apply it:  sudo $0 --admin $admin${key:+ --key $key}$(for v in "${views[@]}"; do printf ' --view %s' "$v"; done)"
else
    echo "worker-setup: $changes change(s) made."
    echo "  next, the boundary self-test (a different command: --check):"
    echo "    sudo $0 --admin $admin --check$(for v in "${views[@]}"; do printf ' --view %s' "$v"; done)"
fi
