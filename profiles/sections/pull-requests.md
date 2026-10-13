## 4. Every change through a pull request with green CI

- Work on a branch, open a pull request, and merge only when every CI check has passed.
  Never merge red, and never merge with checks still running.
- Run the checks locally before you push (`just gates`), and read their output.
- A command's exit status is the status of the LAST command in a pipeline: `tests | tail`
  reports `tail`'s success. Never pipe a command whose result you are trusting.
- Stage the files you changed by name. `git add -A` picks up files that are not yours.
