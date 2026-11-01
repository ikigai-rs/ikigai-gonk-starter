# Thin wrappers over tools/gonk.py. Every recipe is one command, so what `just` runs is
# what an agent without `just` types.

# Lint, format check and tests, exactly as CI runs them.
gates:
    scripts/gates.sh

# Write what the flags say (.gonk/gonk.toml, .gonk/local.toml), then report what is missing.
init *flags:
    python3 tools/gonk.py init {{flags}}

# File a ledger item: just file "Title" [--body …] [--priority 1] [--labels a,b] [--key k]
file title *flags:
    python3 tools/gonk.py file "{{title}}" {{flags}}

# File each open task of the changes in flight as a ledger item; a rerun files nothing new.
seed *flags:
    python3 tools/gonk.py seed {{flags}}

# The ready set, ranked, with the reasons.
next:
    python3 tools/gonk.py next

# Take an item (or renew this tree's lease on it): just claim 12 [--lease 2h] [--renew]
claim item *flags:
    python3 tools/gonk.py claim {{item}} {{flags}}

# Hand an item back: just release [12] [--note "why"]
release *args:
    python3 tools/gonk.py release {{args}}

# Post the evidence and hand the item back: just report 12 https://github.com/owner/repo/pull/34
report item evidence *flags:
    python3 tools/gonk.py report {{item}} --evidence "{{evidence}}" {{flags}}

# The evidence did not hold, so the item is ready again: just reject 12 --note "why"
reject item *flags:
    python3 tools/gonk.py reject {{item}} {{flags}}

# The dispatcher closes, once the evidence holds: just close 12 https://github.com/owner/repo/pull/34 [--reason …]
close item evidence *flags:
    python3 tools/gonk.py close {{item}} --evidence "{{evidence}}" {{flags}}

# Does this working tree hold a live claim?
status:
    python3 tools/gonk.py status

# Load this repository's harness and this tree's firings into gonk, and ask its coverage questions.
harness *flags:
    python3 tools/gonk.py harness {{flags}}

# Generate a repository with the library-lending sample in it: just new <dir> --target python
new dir *flags:
    python3 tools/new.py "{{dir}}" {{flags}}

# The worker loop (worker/README.md, step 8), configured by .gonk/worker.toml. The loop: just worker
worker *flags:
    python3 tools/worker.py run {{flags}}

# One item, then exit (0 handed back, 3 nothing to take, 4 taken but not handed back with evidence).
worker-once *flags:
    python3 tools/worker.py run --once {{flags}}

# What the worker would take now, and why it skips the rest. Claims nothing.
worker-plan *flags:
    python3 tools/worker.py plan {{flags}}

# Let this worker take an item it handed back before: just worker-forget 12
worker-forget item:
    python3 tools/worker.py forget {{item}}
