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

echo "── usage"
bash "$probe" --bogus >/dev/null 2>&1; expect_exit "an unknown argument is exit 2" 2 "$?"

echo
echo "worker-probe-test: $pass passed, $fail failed"
[ "$fail" = 0 ]
