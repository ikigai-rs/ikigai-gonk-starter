# service

The library-lending service: the rules (`lending/domain.py`) and an HTTP JSON API over them
(`lending/http.py`). Standard-library Python 3.11 or later, no dependencies.

```sh
python3 artifacts/service/serve.py --port 8080                    # dated by this machine's clock
python3 artifacts/service/serve.py --port 8080 --today 2026-10-01 # the clock pinned to one day
```

| method | path | body | does |
| --- | --- | --- | --- |
| POST | `/titles` | `{"isbn", "title", "author"}` | catalog a title |
| POST | `/copies` | `{"copy", "isbn"}` | add a copy of a cataloged title |
| POST | `/members` | `{"member", "name"}` | register a member |
| GET | `/copies/{copy}` | | a copy, and its loan if it is out |
| POST | `/loans` | `{"member", "copy"}` | lend a copy, due in 21 days |
| POST | `/loans/{copy}/renewal` | | renew it from its due date (twice at most) |
| POST | `/loans/{copy}/return` | | return it; a late return owes 25 cents a day |
| GET | `/members/{member}/loans` | | a member's loans, the one due first first |
| GET | `/loans/overdue` | | every overdue loan, the latest first |

A refusal is `{"error": "<code>", "message": "…"}`: 404 for something unknown, 400 for a
malformed request, 409 for a rule (`copy-on-loan`, `loan-limit`, `member-has-overdue`,
`renewal-limit`, `loan-overdue`, …). The codes are the ones the specs name.

**Time is an input.** No rule reads a clock: every operation that depends on the date takes it
as an argument, and the server dates each request with the clock it was given. That is why the
scenario vectors, the tests and a book chapter get the same due dates on any day they run, and
why a client can never choose the date of its own loan.

The state is in memory and goes when the process does.

## Tests

```sh
python3 -m unittest discover -s artifacts/service/tests -v
```

`tests/test_vectors.py` runs every scenario in `openspec/vectors.json` against the rules and
again over HTTP; `tests/test_http.py` covers what the HTTP layer adds.
