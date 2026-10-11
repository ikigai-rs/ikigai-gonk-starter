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

# Take an item (or renew this tree's lease on it): just claim 12 [--lease 2h]
claim item *flags:
    python3 tools/gonk.py claim {{item}} {{flags}}

# Hand an item back: just release [12] [--note "why"]
release *args:
    python3 tools/gonk.py release {{args}}

# Post the evidence and hand the item back: just report 12 https://github.com/owner/repo/pull/34
report item evidence *flags:
    python3 tools/gonk.py report {{item}} --evidence "{{evidence}}" {{flags}}

# The dispatcher closes, once the evidence holds: just close 12 https://github.com/owner/repo/pull/34 [--reason …]
close item evidence *flags:
    python3 tools/gonk.py close {{item}} --evidence "{{evidence}}" {{flags}}

# Does this working tree hold a live claim?
status:
    python3 tools/gonk.py status

# Generate a repository with the library-lending sample in it: just new <dir> --target python
new dir *flags:
    python3 tools/new.py "{{dir}}" {{flags}}
