## 4. Every change through a pull request with green CI

- Work on a branch, open a pull request, and merge only when every CI check has passed.
  Never merge red, and never merge with checks still running.
- Run the checks locally before you push (`just gates`), and read their output.
- A command's exit status is the status of the LAST command in a pipeline: `tests | tail`
  reports `tail`'s success. Never pipe a command whose result you are trusting.
- Stage the files you changed by name. `git add -A` picks up files that are not yours.
- **Say what a credential is.** A credential ikigai issues or accepts is holder-bound: its
  presenter proves it holds a key (a QUIC client certificate, a signature over the request, a
  WebAuthn assertion, the kernel-attested peer of a Unix socket). A bearer token (a cookie, a
  header value, a code or key in a URL) works for whoever copied it, so a pull request that
  issues or accepts one has a `## Tokens` section in its description: what the token
  authorizes, why it cannot be bound, and what limits it (lifetime, single use, hashed at
  rest, the transport it is accepted on). One that adds a holder-bound credential says so in
  one line; one that adds neither needs no section. `tools/checks.py pr` prints, as advice,
  each added line shaped like a credential in a pull request with no such section.
