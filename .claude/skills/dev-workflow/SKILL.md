---
name: dev-workflow
description: The SimuBench issue → branch → check → PR loop, the commands, the test tiers (D49) and the CI traps, in one page. Use at the start of any SimuBench task, before reading AGENTS.md or the long docs.
---

# Development loop (short form of AGENTS.md)

## Read only what you need (saves tokens)

- Do **not** read whole long docs (`decisions.md` ~500 lines, `progress.md` ~800,
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
   git fetch origin main && git merge origin/main
   scripts/check.sh
   ```
6. PR into `main`, body has `Closes #<n>`. It auto-squash-merges when CI is green on a commit that
   contains latest `main`. Red CI → push a fix commit. Never weaken/skip/delete tests.
7. Record self-check rows in `docs/progress.md` when the issue type asks for it.

## What `scripts/check.sh` runs (run single steps to iterate fast)

```bash
uv run ruff check src tests acceptance refcalc
uv run ruff format src tests acceptance refcalc          # fixes; check.sh only --check
uv run pytest -n 4 --dist loadscope -q -m "not tui_boot and not load_sensitive"
uv run pytest tests/test_x.py -q                          # while iterating
uv run python scripts/check_tui_i18n.py                   # TUI wording == web i18n
uv run python scripts/check_test_rules.py                 # D49 rules
uv run python scripts/check_skills.py                     # skills name only existing paths
uv run simubench schemas --out schemas                    # then commit schemas/ if changed
cd web && pnpm -s gen:api && pnpm -s typecheck && pnpm -s lint && pnpm -s test
```

Full pytest must stay < 60 s; if it is over, fix it in the same task (`pytest --durations=20`).

## Test rules (D49, enforced)

- Unit tests (default): no Textual import, no server, no network; ≤100 ms per test with the
  `unit` marker. One behaviour per test.
- `tui_boot` marker: boots Textual or a real server; runs on `main` only.
- `tests/test_*e2e*.py`: ≥5 `assert`s per test; runs on `main` only.
- Server fixtures must be `module`/`session` scoped (function scope is rejected by the script).
- Windows, wheel, e2e, visual, `tui_boot`, `load_sensitive` run after merge. Red on `main` is
  fixed before new work.

## Keep the skills current

Changed a path, command, check, rule or workflow step that a skill states? Update that skill
(`.claude/skills/*/SKILL.md`) in the same PR. A new skill also goes in the `AGENTS.md` Skills list.
`scripts/check_skills.py` catches dead paths and list mismatches; stale wording is yours to catch.

## Traps

- Files hidden by `.gitignore` under `src tests acceptance refcalc web/src web/e2e` fail check.sh.
- Changing a Pydantic model / API → regenerate `schemas/` **and** `web/src/api/generated`
  (`pnpm gen:api`), commit both.
- Changing TUI wording that is shared with the web → both `web/src/i18n/{zh,en}.ts` and
  `src/simubench/tui/i18n/{zh,en}.py`.
- YAML floats follow YAML 1.2 (D28): `1e-3` is a float.
- Built-in instruments are real models only (D42); `sb-*` validation instruments live in
  `validation/instruments/` and are auto-loaded by tests (`conftest.py`).
- Allow-lists `ALLOWED` (`tests/test_model_components.py`) and `EXEMPT`
  (`tests/test_block_coverage.py`) may only shrink.
- Decision entries (`docs/decisions.md`) only for choices with real alternatives **and**
  cross-feature impact; edit the existing entry instead of adding a new one.
- Docs and UI text are Chinese first, English second (D23). Commit messages in the repo are
  Chinese with `(Closes #n)`.
