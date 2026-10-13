## The loop

```sh
python3 tools/gonk.py next                         # what is ready, ranked, and why
python3 tools/gonk.py claim 12                     # take item 12, on a 30-minute lease
# reproduce, fix, open a PR, get CI green
python3 tools/gonk.py report 12 --evidence <PR URL>   # the evidence as a comment; hand it back
python3 tools/gonk.py close 12 --evidence <PR URL>    # the dispatcher, once the evidence holds
```

Two roles, and one person may hold both. **Whoever does the work** claims the item, does it,
and reports: the evidence goes on the item as a comment and the claim is handed back.
**Whoever dispatched it** checks that evidence and closes the item.
