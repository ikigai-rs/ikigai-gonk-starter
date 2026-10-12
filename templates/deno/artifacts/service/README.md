# service

The library-lending service: the rules (`src/domain.ts`), an HTTP JSON API over them
(`src/http.ts`), and the two things they are built on, dates (`src/date.ts`) and the JSON type
(`src/json.ts`). Deno's standard library only, no imports from a registry.

```sh
deno run --allow-net artifacts/service/serve.ts --port 8080                    # dated by today
deno run --allow-net artifacts/service/serve.ts --port 8080 --today 2026-10-01 # clock pinned
```

| method | path                      | body                          | does                                         |
| ------ | ------------------------- | ----------------------------- | -------------------------------------------- |
| POST   | `/titles`                 | `{"isbn", "title", "author"}` | catalog a title                              |
| POST   | `/copies`                 | `{"copy", "isbn"}`            | add a copy of a cataloged title              |
| POST   | `/members`                | `{"member", "name"}`          | register a member                            |
| GET    | `/copies/{copy}`          |                               | a copy, and its loan if it is out            |
| POST   | `/loans`                  | `{"member", "copy"}`          | lend a copy, due in 21 days                  |
| POST   | `/loans/{copy}/renewal`   |                               | renew it from its due date (twice at most)   |
| POST   | `/loans/{copy}/return`    |                               | return it; a late return owes 25 cents a day |
| GET    | `/members/{member}/loans` |                               | a member's loans, the one due first first    |
| GET    | `/loans/overdue`          |                               | every overdue loan, the latest first         |

A refusal is `{"error": "<code>", "message": "…"}`: 404 for something unknown, 400 for a malformed
request, 409 for a rule (`copy-on-loan`, `loan-limit`, `member-has-overdue`, `renewal-limit`,
`loan-overdue`, …). The codes are the ones the specs name.

**Time is an input.** No rule reads a clock: every operation that depends on the date takes it as an
argument, and the server dates each request with the clock it was given. That is why the scenario
vectors, the tests and a book chapter get the same due dates on any day they run, and why a client
can never choose the date of its own loan.

The state is in memory and goes when the process does.

## Tests

```sh
deno test --allow-net=127.0.0.1 --allow-read artifacts/service/tests
```

`tests/vectors_test.ts` runs every scenario in `openspec/vectors.json` against the rules and again
over HTTP; `tests/http_test.ts` covers what the HTTP layer adds.
