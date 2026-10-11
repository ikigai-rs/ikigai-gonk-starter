# cli

The library's command line, a client of the [service](../service/README.md)'s HTTP JSON API.
Standard-library Python 3.11 or later, no dependencies.

```sh
python3 artifacts/cli/library.py add-title 9780141439518 "Pride and Prejudice" "Jane Austen"
python3 artifacts/cli/library.py add-copy pp-1 9780141439518
python3 artifacts/cli/library.py add-member ada "Ada Lovelace"
python3 artifacts/cli/library.py lend ada pp-1        # lent pp-1 to ada, due …
python3 artifacts/cli/library.py loans ada
python3 artifacts/cli/library.py return pp-1
```

It talks to `http://127.0.0.1:8080` unless `--service <URL>` names another. It never sends a
date: the service's clock dates every request. Exit status 0 is done, 1 is a refusal by the
service (its code and message on stderr), 2 is a service that did not answer or a wrong
command line.

## Tests

```sh
python3 -m unittest discover -s artifacts/cli/tests -v
```

They start the real service in-process, on a free port, with a clock the test sets.
