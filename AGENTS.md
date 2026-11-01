# Working on SimuBench

Any coding agent (or person) works the same way. Tasks are GitHub issues; a pull request that
passes CI merges itself into `main`, with no review.

## Who takes which issue

Every issue carries exactly one of two labels, set when the issue is opened:

- **`核心`** (core) — done by the lead agent only: **capability building** (new or generic
  components, engine semantics, file formats, the API and MCP, validation rules, design docs and
  decisions) and **Web user interaction** (UI, interaction, wording). These need judgment that
  later work builds on.
- **`可认领`** (claimable) — any agent may claim it: the design is settled and the acceptance
  criteria are written, so the work is following them. Real-model definitions (YAML, coverage,
  cases, example benches), contract cases, a bug with a clear reproduction, documentation fixes.

Claim only a `可认领` issue unless you are the lead agent. When a claimable issue turns out to need
a capability the platform lacks, do not build it inside that issue: open a `核心` issue for the
capability (under the umbrella issue it belongs to, e.g. #86) and comment on yours that it is
blocked by it. Whoever opens an issue labels it; one without a label is not claimable.

## Steps

1. **Claim** the issue: comment "working on this" first, so two agents don't do the same task.
2. **Branch** from the latest `main`: `issue-<number>-<short-name>`.
3. **Stay in scope**: change only what the issue asks for.
4. **Check** before every push: merge the latest `main` into the branch, then run `scripts/check.sh`
   (lint, format, tests, generated schemas and API types). If the full test run exceeds 60 s, fix
   that immediately as part of the current task: profile with `pytest --durations`, address the
   slowest cases (waits, timeouts) or add parallelism — never leave the overrun for a later issue.
5. **Pull request** into `main` with `Closes #<number>` in the description. It is squash-merged
   and its branch deleted once CI is green on its latest commit and that commit contains the
   latest `main` (otherwise merge `main` and push again); open it as a draft if it is not ready
   yet. If CI fails, push a fix: the new commit runs CI again. Pull requests run the fast checks
   (ubuntu: Python and web); Windows, the wheel, end-to-end and visual tests run on `main` after
   the merge, and a red one there is fixed first, before new work.
6. Never push to `main` directly, and never weaken, skip or delete tests to get CI green.

Before changing behaviour, read `docs/decisions.md` (the recorded decisions) and
`docs/progress.md` (what is done). A decision entry is written only for a choice that satisfies
both:

1. **Real alternatives existed** — either path would have worked, and someone may want to reverse
   it later;
2. **The impact reaches beyond one feature** — file formats, APIs, terminology, product scope,
   the security model, and the like.

Feature-level descriptions belong in issues, pull requests and their docs (`docs/real-models.md`,
`docs/architecture.md`, `docs/glossary.md`, …). To change an existing decision, edit its entry; do
not add a new one.
