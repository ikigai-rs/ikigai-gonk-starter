# cli

The library's command line, a client of the [service](../service/README.md)'s HTTP JSON API. Deno's
standard library only, no imports from a registry.

```sh
deno run --allow-net artifacts/cli/library.ts add-title 9780141439518 "Pride and Prejudice" "Jane Austen"
deno run --allow-net artifacts/cli/library.ts add-copy pp-1 9780141439518
deno run --allow-net artifacts/cli/library.ts add-member ada "Ada Lovelace"
deno run --allow-net artifacts/cli/library.ts lend ada pp-1        # lent pp-1 to ada, due …
deno run --allow-net artifacts/cli/library.ts loans ada
deno run --allow-net artifacts/cli/library.ts return pp-1
```

It talks to `http://127.0.0.1:8080` unless `--service <URL>` names another. It never sends a date:
the service's clock dates every request. Exit status 0 is done, 1 is a refusal by the service (its
code and message on stderr), 2 is a service that did not answer or a wrong command line.

## Tests

```sh
deno test --allow-net=127.0.0.1 --allow-read artifacts/cli/tests
```

They start the real service in process, on a free port, with a clock the test sets.
