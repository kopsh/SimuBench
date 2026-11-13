---
name: real-model
description: Add or extend a real instrument model (a built-in definition with `reference`, D42) — definition YAML, coverage table, manual cases, example bench. Use when working on a "真实型号" issue.
---

# Adding a real model

The rules are in `docs/real-models.md` (D42) and `docs/instrument-authoring.md`; this file is the
order of work and the commands. Read `real-models.md` first. `instrument-authoring.md` is long
(~28k tokens): list it with `grep -n '^#'` and read only the sections you need (2: declarative
commands, replies, mappings; 2.6: channel lists; 3: waveform transfer). For meters, calibrators and
SMUs, read the matching section of `docs/metrology-authoring.md` the same way.

## The one rule that stops you

**A model is a definition, not code.** Do not add or change anything in
`src/simubench/library/components/` for a model issue. `tests/test_model_components.py` fails
when a built-in real model uses a component marked `generic = False`, and only the uses listed
there are allowed (the list only shrinks).

When the platform cannot express something the manual requires:

1. open a capability issue labelled `核心` under the umbrella issue it belongs to — the
   first-wave category-expansion umbrella is #181 (what the manual needs, which commands, why the
   existing components cannot do it — see the sub-issues of #181 for the shape); capability work
   belongs to the lead agent (`AGENTS.md`);
2. comment on the model issue that it is blocked by that issue, and stop or continue with what
   does not depend on it. Do not write a model-specific component to get round it.

## Steps

1. **Claim** the issue (comment "working on this"; only a `可认领` one), branch from the latest `main` (`AGENTS.md`).
   **Manual**: look in `manuals/` first (`manuals/README.md` lists them; grep the `.txt`, the
   PDF needs `git lfs pull --include "manuals/**"`). A manual not there yet goes in with the
   model: `manuals/<document number>/<number>.pdf` plus `pdftotext -layout` text and an index row.
2. **Command list**: copy the command headers from the manual's command index into a text file,
   one per line, `# <section>` lines for the manual's sections. Only command names — the
   manual's text stays in `manuals/` (real-models.md 7).
3. **Coverage skeleton**:
   ```bash
   uv run simubench coverage <id> --draft commands.txt \
       --out src/simubench/library/instruments/<id>.coverage.yaml
   ```
   Every command starts as `not_modeled`; rerun after the definition grows (hand-written levels
   and notes are kept). Then set `implemented` / `accepted` and the notes by hand. For statistics
   across models and gap analysis, use the `command-coverage` skill.
4. **Components**: list what exists and pick generic ones.
   ```bash
   uv run python -c "from simubench.server.catalog import components; [print(c.type, c.generic, c.category) for c in components()]"
   ```
   (or the MCP tool `list_components`, then `list_components(types=[...])` for the full fields).
   Parameters bind to settings with `$path` and map with `{from, map, scale, offset, default}`.
5. **Definition** `src/simubench/library/instruments/<id>.yaml`: `reference`, `identity`,
   `errors`, `format`, state, components, commands. A hardware option the data sheet lists (a
   preamplifier) goes in `options` — the parameters of a unit that has it, and `option: <id>` on
   its commands (D68; fields in `docs/real-models.md`); the bench instance lists `options: [P03]`.
   Validate as you go:
   ```bash
   uv run simubench validate src/simubench/library/instruments/<id>.yaml
   ```
6. **Manual cases** `tests/models/<id>/cases.yaml`: every implemented and accepted command at
   least once, each case with `manual:` naming the section. Expectations say what the reply
   means, never a regex (D49, #926; `docs/real-models.md` 4 lists the keys): `expect_prefix` for
   an error, `expect_value(s)` + `tolerance` (k·σ of the model's noise) for readings,
   `expect_fields` / `expect_format` / `expect_block` for lists, written formats and blocks.
   Command-level coverage lands here
   first — the per-model test file is for what a transcript cannot see, added as acts in flow
   tests (D49, #704), not as sibling test functions (#706).
   ```bash
   uv run simubench cases tests/models/<id>/cases.yaml
   uv run simubench coverage <id>
   ```
   A cases bench that takes its signal from an `sb-*` validation instrument (the WavePro's and
   the SDS's do: `sb-awg-2`) needs the definition path, which `pytest` sets itself (`conftest.py`)
   but the CLI does not — otherwise every case fails with `no instrument definition 'sb-awg-2'`:
   ```bash
   SIMUBENCH_DEFINITIONS=validation/instruments uv run simubench cases tests/models/<id>/cases.yaml
   ```
7. **Example bench** in `examples/benches/` with real models only (`sb-*` benches go to
   `validation/benches/`). A script written the way a real-instrument script is written, run in a test.
8. **Check and record**: `scripts/check.sh`; append a self-check section to the end of `docs/progress-log.md`
   (`docs/progress.md` says how).
9. **Pull request** with `Closes #<n>`; it merges itself when CI is green.

**Spectrum analyzers**: the front-end figures (noise figure from the DANL, intercepts, 1 dB
compression, phase noise, flatness, images and spurs) come from the model's **data sheet**, put in
`manuals/` like a manual; `specs` quotes each figure with its table and page, and each parameter
says beside it how it was converted (DANL → noise figure, SHI → `iip2_dbm`). Figures the data sheet
does not give are not declared and not estimated; banded figures use a `table` on `$freq.center`
(`instrument-authoring.md` 2.10). The coverage note of the trace and marker commands names the
evaluation and what it includes (`real-models.md` 6).

## What tends to go wrong

- A coverage `header` must be written as the definition writes it (case, `[optional]` nodes,
  `<n>` suffixes); the check compares them normalized.
- A real model answers undefined commands with `-113 … not implemented in this definition` — silence is
  never acceptable (real-models.md 2).
- Defaults: `default` is the power-on value, `rst` the value after `*RST`; check both against the
  manual's table.
