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
# templates/library/), generated into a scratch directory and tested by its own entry point,
# scripts/test.py, as that repository's CI would run it. A stale template fails here.
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/gonk-starter-gates.XXXXXX") || exit 2
trap 'rm -rf "$SCRATCH"' EXIT
generated() {
    for dir in templates/*/; do
        target=$(basename "$dir")
        [ "$target" = library ] && continue
        echo "-- the sample, generated for $target, runs its own tests" &&
            "$PY" tools/new.py "$SCRATCH/$target" --target "$target" >/dev/null &&
            "$PY" "$SCRATCH/$target/scripts/test.py" || return 1
    done
}

echo "gates: $("$PY" --version 2>&1), $("$RUFF" --version)"
echo "-- ruff check" &&
    "$RUFF" check . &&
    echo "-- ruff format --check" &&
    "$RUFF" format --check . &&
    echo "-- unittest" &&
    "$PY" -m unittest discover -s tests -v &&
    generated
