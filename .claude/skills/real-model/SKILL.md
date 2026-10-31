---
name: real-model
description: Add or extend a real instrument model (a built-in definition with `reference`, D42) — definition YAML, coverage table, manual cases, example bench. Use when working on a "真实型号" issue.
---

# Adding a real model

The rules are in `docs/real-models.md` (D42) and `docs/instrument-authoring.md`; this file is the
order of work and the commands. Read both docs first, and `docs/metrology-authoring.md` for meters,
calibrators and SMUs.

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
2. **Command list**: copy the command headers from the manual's command index into a text file,
   one per line, `# <section>` lines for the manual's sections. Only command names — never the
   manual's text (real-models.md 7).
3. **Coverage skeleton**:
   ```bash
   uv run simubench coverage <id> --draft commands.txt \
       --out src/simubench/library/instruments/<id>.coverage.yaml
   ```
   Every command starts as `not_modeled`; rerun after the definition grows (hand-written levels
   and notes are kept). Then set `implemented` / `accepted` and the notes by hand.
4. **Components**: list what exists and pick generic ones.
   ```bash
   uv run python -c "from simubench.server.catalog import components; [print(c.type, c.generic, c.category) for c in components()]"
   ```
   (or the MCP tool `list_components`, then `list_components(types=[...])` for the full fields).
   Parameters bind to settings with `$path` and map with `{from, map, scale, offset, default}`.
5. **Definition** `src/simubench/library/instruments/<id>.yaml`: `reference`, `identity`,
   `errors`, `format`, state, components, commands. Validate as you go:
   ```bash
   uv run simubench validate src/simubench/library/instruments/<id>.yaml
   ```
6. **Manual cases** `tests/models/<id>/cases.yaml`: every implemented and accepted command at
   least once, each case with `manual:` naming the section.
   ```bash
   uv run simubench cases tests/models/<id>/cases.yaml
   uv run simubench coverage <id>
   ```
7. **Example bench** in `examples/benches/` with real models only (`sb-*` benches go to
   `validation/benches/`). A script written the way a real-instrument script is written, run in a test.
8. **Check and record**: `scripts/check.sh`; add the self-check rows to `docs/progress.md`.
9. **Pull request** with `Closes #<n>`; it merges itself when CI is green.

## What tends to go wrong

- A coverage `header` must be written as the definition writes it (case, `[optional]` nodes,
  `<n>` suffixes); the check compares them normalized.
- A real model answers undefined commands with `-113 … not modeled by SimuBench` — silence is
  never acceptable (real-models.md 2).
- Defaults: `default` is the power-on value, `rst` the value after `*RST`; check both against the
  manual's table.
