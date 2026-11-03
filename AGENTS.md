# Working on SimuBench

Any coding agent (or person) works the same way. Tasks are GitHub issues; a pull request that
passes CI merges itself into `main`, with no review.

## Skills: read these first

Short task guides in `.claude/skills/<name>/SKILL.md`. Any agent can read them (Claude Code loads
them itself). Read the one that matches your task before the long docs, and read long docs only by
section (`grep -n '^#' docs/<file>.md`, then `sed -n`). That saves most of the tokens.

- `dev-workflow`: this loop, the check commands, test rules (D49), CI traps. Read it for every task.
- `codebase-map`: which file or doc answers what.
- `real-model`: a real instrument model (definition YAML, coverage, cases, example bench).
- `component`: a new or split generic component (`核心`).
- `web-ui`: the Web Bench (`web/`), API types, i18n.

**Skills must not go stale.** A pull request that changes something a skill states (a path, a
command, a check, a rule, a workflow step, a decision it cites) updates that skill in the same pull
request. A new recurring kind of task gets a new skill, listed here. `scripts/check_skills.py`
(part of `scripts/check.sh`) fails when a skill names a path that no longer exists or this list
and `.claude/skills/` disagree. Wording it cannot check is the author's job.

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
capability (under the umbrella issue it belongs to; the first-wave category-expansion umbrella is #181) and comment on yours that it is
blocked by it. Whoever opens an issue labels it; one without a label is not claimable.

## Steps

1. **Claim** the issue: comment "working on this" first, so two agents don't do the same task.
2. **Branch** from the latest `main`: `issue-<number>-<short-name>`.
3. **Stay in scope**: change only what the issue asks for.
4. **Check** before every push: run `scripts/check.sh` (lint, format, tests, generated schemas and
   API types). Merging the latest `main` into the branch first is optional — when the branch is
   behind, the merge bot merges `main` into it and re-runs CI itself (#481). If the full test run
   exceeds 60 s, fix that immediately as part of the current task: profile with
   `pytest --durations`, address the slowest cases (waits, timeouts) or add parallelism — never
   leave the overrun for a later issue.
5. **Pull request** into `main` with `Closes #<number>` in the description. It is squash-merged
   and its branch deleted once CI is green on its latest commit that contains the latest `main`;
   when `main` has moved, the merge bot merges it into the branch, re-runs CI, and merges — only
   a merge conflict comes back to the agent. Open it as a draft if it is not ready yet. If CI
   fails, push a fix: the new commit runs CI again. Pull requests run the fast checks
   (ubuntu: Python and web); Windows, the wheel, end-to-end and visual tests run on `main` after
   the merge, and a red one there is fixed first, before new work.
6. Never push to `main` directly, and never weaken, skip or delete tests to get CI green.

Before changing behaviour, check `docs/decisions.md` (the recorded decisions) and
`docs/progress.md` (milestone status; details in `docs/progress-log.md`), **by section, not whole**
(`decisions.md` and `progress-log.md` together are about 70k tokens):
`grep -n '^## D' docs/decisions.md` for the list, then read the entries your change touches
(`sed -n '<from>,<to>p'`); `grep -n '<keyword or #issue>' docs/progress-log.md` and read only the
matching rows. A decision entry is written only for a choice that satisfies
both:

1. **Real alternatives existed** — either path would have worked, and someone may want to reverse
   it later;
2. **The impact reaches beyond one feature** — file formats, APIs, terminology, product scope,
   the security model, and the like.

Feature-level descriptions belong in issues, pull requests and their docs (`docs/real-models.md`,
`docs/architecture.md`, `docs/glossary.md`, …). To change an existing decision, edit its entry; do
not add a new one.
