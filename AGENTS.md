# Working on SimuBench

Any coding agent (or person) works the same way. Tasks are GitHub issues; a pull request that
passes CI merges itself into `main`, with no review.

1. **Claim** the issue: comment "working on this" first, so two agents don't do the same task.
2. **Branch** from the latest `main`: `issue-<number>-<short-name>`.
3. **Stay in scope**: change only what the issue asks for.
4. **Check** before every push: `scripts/check.sh` (lint, format, tests, generated schemas and API types).
5. **Pull request** into `main` with `Closes #<number>` in the description. It merges once CI is
   green; open it as a draft if it is not ready yet.
6. Never push to `main` directly, and never weaken, skip or delete tests to get CI green.

Before changing behaviour, read `docs/decisions.md` (the recorded decisions) and
`docs/progress.md` (what is done). A new design choice gets a new decision entry there.
