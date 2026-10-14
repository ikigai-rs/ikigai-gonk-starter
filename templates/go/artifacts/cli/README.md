# cli

The library's command line, a client of the [service](../service/README.md)'s HTTP JSON API. Go's
standard library only, no dependencies. `library.go` is the command; `client/` holds the parsing,
the HTTP client and the words it answers in.

```sh
go run ./artifacts/cli add-title 9780141439518 "Pride and Prejudice" "Jane Austen"
go run ./artifacts/cli add-copy pp-1 9780141439518
go run ./artifacts/cli add-member ada "Ada Lovelace"
go run ./artifacts/cli lend ada pp-1        # lent pp-1 to ada, due …
go run ./artifacts/cli loans ada
go run ./artifacts/cli return pp-1
```

It talks to `http://127.0.0.1:8080` unless `--service <URL>` names another. It never sends a date:
the service's clock dates every request. Exit status 0 is done, 1 is a refusal by the service (its
code and message on stderr), 2 is a service that did not answer or a wrong command line.

## Tests

```sh
go test ./artifacts/cli/...
```

They start the real service in process, on a free port, with a clock the test sets.
