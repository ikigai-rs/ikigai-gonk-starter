# cli

The library's command line, a client of the [service](../service/README.md)'s HTTP JSON API.
Standard library only, no dependencies.

```sh
cargo run --bin library -- add-title 9780141439518 "Pride and Prejudice" "Jane Austen"
cargo run --bin library -- add-copy pp-1 9780141439518
cargo run --bin library -- add-member ada "Ada Lovelace"
cargo run --bin library -- lend ada pp-1        # lent pp-1 to ada, due …
cargo run --bin library -- loans ada
cargo run --bin library -- return pp-1
```

It talks to `http://127.0.0.1:8080` unless `--service <URL>` names another. It never sends a
date: the service's clock dates every request. Exit status 0 is done, 1 is a refusal by the
service (its code and message on stderr), 2 is a service that did not answer or a wrong
command line.

## Tests

```sh
cargo test -p lending-cli
```

They start the real service in-process, on a free port, with a clock the test sets.
