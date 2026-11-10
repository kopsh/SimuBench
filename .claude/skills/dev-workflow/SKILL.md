---
name: dev-workflow
description: The SimuBench issue → branch → check → PR loop, the commands, the test tiers (D49) and the CI traps, in one page. Use at the start of any SimuBench task, before reading AGENTS.md or the long docs.
---

# Development loop (short form of AGENTS.md)

## Read only what you need (saves tokens)

- Do **not** read whole long docs (`decisions.md` ~500 lines, `progress-log.md` ~800,
  `architecture.md` ~500, `instrument-authoring.md` ~650). List headings first, then read one
  section:
  ```bash
  grep -n '^#' docs/<file>.md            # outline
  sed -n '<from>,<to>p' docs/<file>.md   # one section
  grep -n '^## D4[0-9]' docs/decisions.md   # one decision by number
  ```
- Where things are: the `codebase-map` skill. Real model: `real-model`. New component: `component`.
  Web UI: `web-ui`.

## Loop

1. Issue label decides who: `可认领` = anyone; `核心` = lead agent only. Unlabelled = not claimable.
   Missing capability inside a claimable issue → open a `核心` issue (under umbrella #181), comment
   "blocked by #N" on yours; never build it inside the claimable issue.
2. Comment "working on this" on the issue.
3. Branch `issue-<n>-<short-name>` from latest `main` (in a cloud session use the branch you were
   given).
4. Change only what the issue asks.
5. Before every push:
   ```bash
   scripts/check.sh
   ```
   Merging latest `main` into the branch first is optional: when the branch is behind, the merge
   bot (`.github/workflows/merge.yml`) merges `main` into it, re-runs CI, and merges — only a
   merge conflict comes back to the agent.
6. PR into `main`, body has `Closes #<n>`. The title is the changelog entry when the PR changes
   the product (D58): what changed for the user, not how. It auto-squash-merges when CI is green on a commit that
   contains latest `main`; the bot self-heals a branch that `main` has passed (merge + re-CI on
   the same fast tier) and comments on a red CI run. While `main`'s CI is red the bot holds other PRs (`等main变绿`, released by ci.yml's main-status job) and a PR that fixes main carries the label `修复main` (#810). Red CI → push a fix commit. Never
   weaken/skip/delete tests. Label the PR `免审查` when the issues it closes need no review: the bot
   copies the label onto them before closing (#614); it retries GitHub API 502s itself.
7. Append a self-check section to the end of `docs/progress-log.md` when the issue asks for it
   (format in `docs/progress.md`; never read the log whole).

## What `scripts/check.sh` runs (run single steps to iterate fast)

```bash
uv run ruff check src tests acceptance refcalc
uv run ruff format src tests acceptance refcalc          # fixes; check.sh only --check
uv run python scripts/check_ty.py                        # type check vs the baseline (#897)
uv run pytest -n 4 --dist loadscope -q -m "not tui_boot and not load_sensitive"
uv run pytest tests/test_x.py -q                          # while iterating
uv run python scripts/check_tui_i18n.py                   # TUI wording == web i18n
uv run python scripts/check_test_rules.py                 # D49 rules
uv run python scripts/check_skills.py                     # skills name only existing paths
uv run python scripts/check_progress_log.py               # progress-log sections only grow (#380)
uv run simubench schemas --out schemas                    # then commit schemas/ if changed
cd web && pnpm -s gen:api && pnpm -s typecheck && pnpm -s lint && pnpm -s test
```

The type check is Astral's `ty` over `src`, against `scripts/ty_baseline.txt` (`scripts/check_ty.py`):
a diagnostic the baseline does not cover fails the check; a batch of fixes deletes its baseline lines
(`--rebuild` rewrites the file, review it like any diff), so the baseline only ever shrinks. Runs in
the `python` CI job only (the `ty` binary is one binary for every OS and Python version).

Every test has a 120 s limit (`timeout` in pyproject.toml, pytest-timeout): a hang fails with thread stacks instead of stalling CI.
The fast tests run on Python 3.12 and, in the `python311` job, on 3.11 (`requires-python` is `>=3.11`); reproduce a 3.11-only failure with `uv run --python 3.11 pytest …`.
Full pytest must stay < 60 s; if it is over, fix it in the same task (`pytest --durations=20`).

## Test rules (D49, enforced)

- Unit tests (default): no Textual import, no server, no network; ≤100 ms per test with the
  `unit` marker (×3 when `CI=true` — a shared runner's load spikes are noise, #482). One
  behaviour per test (unit tier only).
- `tui_boot` marker: boots Textual or a real server; runs on every pull request (the `tui` job, #858) and on `main`.
- `tests/test_*e2e*.py`: ≥5 `assert`s per test; runs on `main` only.
- Engine/model tests that open a real bench: flow tests are the norm (D49, #704) — one test
  walks a manual flow with step-named asserts; merge adjacent small tests of the same flow
  instead of re-opening the bench per test. Assertions are never dropped in a merge.
- Server fixtures must be `module`/`session` scoped (function scope is rejected by the script).
- Shared test skeletons — example-script loading, example-bench script runs, session queries,
  golden-table runners, the MCP tool-result unwrap — live in `tests/helpers.py` (TUI mocks and
  pilot helpers: `tests/tui_mock.py`); don't copy them into new test files (#698, #699, #700).
- Adding tests: reuse first (#706) — before writing a new test or file, check an existing one
  doesn't already cover the behaviour, then extend what exists: an act in the nearest flow test
  (D49's engine-tier flows, #704), a `cases.yaml` row, a parametrized case, a test in the
  nearest existing module. A new test *file* is for a new model (the `real-model` template) or
  a behaviour with no existing home. Never add a case into a flow whose state it breaks —
  state-incompatible coverage gets its own test.
- Windows, wheel, `load_sensitive` run after merge; `tui_boot` (the `tui` job, #858) and e2e/visual gate pull requests. Red on `main` is
  fixed before new work.

## Keep the skills current

Changed a path, command, check, rule or workflow step that a skill states? Update that skill
(`.claude/skills/*/SKILL.md`) in the same PR. A new skill also goes in the `AGENTS.md` Skills list.
`scripts/check_skills.py` catches dead paths and list mismatches; stale wording is yours to catch.

## Traps

- Files hidden by `.gitignore` under `src tests acceptance refcalc web/src web/e2e` fail check.sh
  (except `src/simubench/_version.py`, written by the build).
- The version comes from git (D58): never write a version number into the source. `__version__`
  is what `uv sync` built the editable install with; re-run `uv sync` to refresh it. The version
  `YYYY.M.D.N` is counted from the commit history (`scripts/version.py`); nothing is tagged. A shallow
  clone gives N 0 with a warning and `build-wheel.sh` refuses it: `git fetch --unshallow` first.
- Changing a Pydantic model / API → regenerate `schemas/` **and** `web/src/api/generated`
  (`pnpm gen:api`), commit both.
- Changing TUI wording that is shared with the web → `web/src/i18n/{zh,en}.ts` and
  `src/simubench/tui/i18n/en.py` (the TUI is English only, D23).
- YAML floats follow YAML 1.2 (D28): `1e-3` is a float.
- Built-in instruments are real models only (D42); `sb-*` validation instruments live in
  `validation/instruments/` and are auto-loaded by tests (`conftest.py`).
- Allow-lists `ALLOWED` (`tests/test_model_components.py`) and `EXEMPT`
  (`tests/test_block_coverage.py`) may only shrink.
- Decision entries (`docs/decisions.md`) only for choices with real alternatives **and**
  cross-feature impact; edit the existing entry instead of adding a new one.
- Docs and UI text are Chinese first, English second (D23). Commit messages in the repo are
  Chinese with `(Closes #n)`.
