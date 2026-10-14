#!/usr/bin/env bash
# The gates CI runs, in CI's order: lint, format check, tests, then the generated sample's tests.
#
#   scripts/gates.sh
#
# ONE source of truth: .github/workflows/ci.yml calls this, and the justfile's `gates`
# recipe calls it locally (which is also what `just -f ~/git-personal/ikigai-devtools/justfile
# gates` runs in a repository with no Cargo.toml). Change the gates here and both follow.
#
# The tools are standard-library Python, so the only thing to find is ruff, a development
# tool: .venv/bin/ruff first, then ruff on PATH. Without it this REFUSES rather than running
# the tests alone, because a gate that silently covers less than CI is the failure it exists
# to stop. Install it with: python3 -m venv .venv && .venv/bin/pip install ruff==0.16.1
#
# The integration tests start a scratch gonk when `ikigai-gonk` and `ikigai` are on PATH,
# and skip, saying why, when they are not (as in CI); tests/test_worker_gonk.py runs the worker
# loop against one the same way. The worker loop's own tests (tests/test_worker_loop.py) need
# only git: an in-memory ledger, a stub gh and a fake agent. The unittest step also runs the worker
# scripts' hermetic tests (tests/test_worker.py: worker/tests/probe-test.sh and setup-test.sh,
# no root, nothing changed) and the public-tree guard (tests/test_tree_guard.py). The worker's
# end-to-end tests are CI jobs of their own, on throwaway runners only.

set -u
cd "$(dirname "$0")/.." || exit 2

if [ -x .venv/bin/ruff ]; then
    RUFF=.venv/bin/ruff
elif command -v ruff >/dev/null 2>&1; then
    RUFF=ruff
else
    echo "gates: no ruff (.venv/bin/ruff or on PATH): NOTHING WAS GATED" >&2
    echo "  install with: python3 -m venv .venv && .venv/bin/pip install ruff==0.16.1" >&2
    exit 2
fi
PY=python3
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
    echo "gates: $PY is older than the 3.11 these tools need (tomllib)" >&2
    exit 2
}

# Every target the generator offers (templates/<target>/, beside the language-neutral
# templates/library/), and every process profile (profiles/), generated into a scratch
# directory and tested by its own entry point,
# scripts/test.py, as that repository's CI would run it. A stale template fails here. Each
# target's test.py needs only that language's toolchain: python/ is standard-library Python,
# rust/ is one cargo workspace on the standard library alone (no fetch, no lock to resolve),
# deno/ is Deno with no imports from a registry, go/ is one Go module on the standard library.
# `cargo`, `deno` and `go` must be on PATH for those
# targets' gates to mean anything; each test.py fails closed, naming a tool that is missing,
# rather than reading a missing toolchain as a passing suite.
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/gonk-starter-gates.XXXXXX") || exit 2
trap 'rm -rf "$SCRATCH"' EXIT

# The deno target's toolchain. CI's ubuntu runner does not carry Deno, and a worker's token may
# not push a `.github/workflows/` change at all (worker/README.md: a worker must not edit CI),
# so the gate provisions the pinned release itself rather than depending on a workflow setup
# step: the archive comes from Deno's own GitHub release, its published SHA-256 is checked, and
# the binary is unpacked into the scratch directory and put on PATH. A machine that already has
# `deno` keeps its own, and a CI given a `denoland/setup-deno` step skips all of this. The
# digests are the `.zip.sha256sum` files published with that release.
DENO_VERSION=2.9.7
deno_asset() {
    case "$(uname -s)/$(uname -m)" in
        Linux/x86_64 | Linux/amd64) echo "deno-x86_64-unknown-linux-gnu" ;;
        Linux/aarch64 | Linux/arm64) echo "deno-aarch64-unknown-linux-gnu" ;;
        Darwin/x86_64 | Darwin/amd64) echo "deno-x86_64-apple-darwin" ;;
        Darwin/arm64 | Darwin/aarch64) echo "deno-aarch64-apple-darwin" ;;
        *) return 1 ;;
    esac
}
deno_sha256() {
    case "$1" in
        deno-x86_64-unknown-linux-gnu) echo "c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490" ;;
        deno-aarch64-unknown-linux-gnu) echo "c832298b1ad4422481334855f6003e0f54145762c5a134f20a489511d2f65bbf" ;;
        deno-x86_64-apple-darwin) echo "95daaff11c116a52ad54785e7914c8e9c9cdcaba793c5ed929c74ca2d8e6259a" ;;
        deno-aarch64-apple-darwin) echo "5cd46d6268f6f78f5d88bdc7159d20bd44cdaa4b3303474839f87ec6fe7ae25c" ;;
    esac
}
if ! command -v deno >/dev/null 2>&1; then
    asset=$(deno_asset) || {
        echo "gates: no deno on PATH, and no pinned release for $(uname -s)/$(uname -m)" >&2
        exit 2
    }
    echo "gates: no deno on PATH: fetching $asset v$DENO_VERSION (checked against its SHA-256)"
    url="https://github.com/denoland/deno/releases/download/v$DENO_VERSION/$asset.zip"
    TOOLCHAIN="$SCRATCH/.toolchain"
    mkdir -p "$TOOLCHAIN" || exit 2
    curl -fsSL -o "$TOOLCHAIN/$asset.zip" "$url" || {
        echo "gates: could not fetch $url" >&2
        exit 2
    }
    got=$("$PY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$TOOLCHAIN/$asset.zip")
    want=$(deno_sha256 "$asset")
    if [ "$got" != "$want" ]; then
        echo "gates: $asset.zip is not the release: sha256 $got, expected $want" >&2
        exit 2
    fi
    "$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$TOOLCHAIN/$asset.zip" "$TOOLCHAIN/deno" || exit 2
    chmod +x "$TOOLCHAIN/deno/deno" || exit 2
    PATH="$TOOLCHAIN/deno:$PATH"
    export PATH
fi

# The go target's toolchain, provisioned the same way and for the same reason (no setup step
# in the workflow, which a worker may not edit): the pinned release from go.dev, its published
# SHA-256 checked, unpacked into the scratch directory and put on PATH. A machine that already
# has `go` keeps its own. The digests are the ones go.dev/dl lists for that release.
GO_VERSION=1.26.9
go_asset() {
    case "$(uname -s)/$(uname -m)" in
        Linux/x86_64 | Linux/amd64) echo "go$GO_VERSION.linux-amd64.tar.gz" ;;
        Linux/aarch64 | Linux/arm64) echo "go$GO_VERSION.linux-arm64.tar.gz" ;;
        Darwin/x86_64 | Darwin/amd64) echo "go$GO_VERSION.darwin-amd64.tar.gz" ;;
        Darwin/arm64 | Darwin/aarch64) echo "go$GO_VERSION.darwin-arm64.tar.gz" ;;
        *) return 1 ;;
    esac
}
go_sha256() {
    case "$1" in
        "go$GO_VERSION.linux-amd64.tar.gz") echo "42d158b4d8f7b61ac0a830567c940a86098fb7aac52e467a5ebec03ef5cc2f8d" ;;
        "go$GO_VERSION.linux-arm64.tar.gz") echo "4a97373d49fcacdcf3694fea368a500b00ee3e963974f3e7514132717632f052" ;;
        "go$GO_VERSION.darwin-amd64.tar.gz") echo "00c29e3d4c8562f547410daa18d2959b641bd691ab00c4a525ea817a0253d4bd" ;;
        "go$GO_VERSION.darwin-arm64.tar.gz") echo "f9bb7c0a02506c5d9bf0d1eb1f7ee6c7684f844ae49558308a0427b830e022cc" ;;
    esac
}
if ! command -v go >/dev/null 2>&1; then
    asset=$(go_asset) || {
        echo "gates: no go on PATH, and no pinned release for $(uname -s)/$(uname -m)" >&2
        exit 2
    }
    echo "gates: no go on PATH: fetching $asset (checked against its SHA-256)"
    TOOLCHAIN="$SCRATCH/.toolchain"
    mkdir -p "$TOOLCHAIN" || exit 2
    curl -fsSL -o "$TOOLCHAIN/$asset" "https://go.dev/dl/$asset" || {
        echo "gates: could not fetch https://go.dev/dl/$asset" >&2
        exit 2
    }
    got=$("$PY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$TOOLCHAIN/$asset")
    want=$(go_sha256 "$asset")
    if [ "$got" != "$want" ]; then
        echo "gates: $asset is not the release: sha256 $got, expected $want" >&2
        exit 2
    fi
    "$PY" -c 'import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2], filter="data")' "$TOOLCHAIN/$asset" "$TOOLCHAIN/go-root" || exit 2
    PATH="$TOOLCHAIN/go-root/go/bin:$PATH"
    export PATH
fi

# One generated repository, running its own tests: generate <name> <target> [<process>].
generate_and_test() {
    echo "-- the sample, generated for $2 with process ${3:-(the default)}, runs its own tests" &&
        "$PY" tools/new.py "$SCRATCH/$1" --target "$2" ${3:+--process "$3"} >/dev/null &&
        "$PY" "$SCRATCH/$1/scripts/test.py"
}

# Every target with the default process; every process profile (profiles/profiles.toml) on
# the default target, python, whose tests take seconds; and on each other target one
# combination that between them carries every profile's files (CI time stays sane: a cargo
# or deno build per profile would multiply the slowest gate by six for files the target's
# tests never read).
PROFILES=$("$PY" -c 'import tomllib; print(" ".join(tomllib.load(open("profiles/profiles.toml", "rb"))["profiles"]))') || exit 2
generated() {
    for dir in templates/*/; do
        target=$(basename "$dir")
        [ "$target" = library ] && continue
        generate_and_test "$target" "$target" || return 1
    done
    for profile in $PROFILES; do
        generate_and_test "python-$profile" python "$profile" || return 1
    done
    generate_and_test rust-combined rust "workers,hub,review-every-commit" &&
        generate_and_test deno-combined deno "solo,spec,audits" &&
        generate_and_test go-combined go "workers,spec,review-every-commit"
}

echo "gates: $("$PY" --version 2>&1), $("$RUFF" --version)"
echo "-- ruff check" &&
    "$RUFF" check . &&
    echo "-- ruff format --check" &&
    "$RUFF" format --check . &&
    echo "-- unittest" &&
    "$PY" -m unittest discover -s tests -v &&
    generated
