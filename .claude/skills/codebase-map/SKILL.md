---
name: codebase-map
description: Where each part of SimuBench lives and which file to open for a given change (engine, SCPI, definitions, server/API, MCP, TUI, web, tests, docs). Use instead of exploring the tree or reading architecture.md end to end.
---

# Codebase map

Layers (architecture.md 0): clients (Web / TUI / scripts / agents / pytest) → access layer
(HTTP+WS, PyVISA backend, SCPI sockets + HiSLIP, MCP, Python SDK) → engine (session, action
queue, virtual clock, circuit solve, signals, faults) → model layer (components, instrument
definitions, templates, bench files). Principles: everything is components+ports+wires (D1);
every change to a running bench is an action in the run record; deterministic (same seed + same
actions = same result); **reject rather than guess** (unmodelled → explicit SCPI error, never a
plausible fake).

## `src/simubench/`

| Change | File |
| --- | --- |
| Session, action queue, virtual clock | `engine/session.py`; idle modes (D47) `engine/sleep_mode.py` |
| Instrument runtime, SCPI command layer | `engine/instrument.py`; static lookup `engine/commands.py` |
| Component base class, `@component`, `@op` | `engine/component.py` |
| Circuit (MNA per frequency) / signals / noise | `engine/circuit.py`, `engine/signal.py`, `engine/noise.py` |
| Bench validation / definition validation | `engine/validate.py`, `engine/validate_definition.py` |
| Bench edits (canvas + MCP share them) | `engine/edit.py` |
| Faults / run record / replay | `engine/faults.py`, `engine/record.py`, `engine/replay.py` |
| Event contract (stream shape, kinds registry, WS projection) | `engine/events.py` (re-exported by `models/api.py`, D57) |
| Calibration, deviation, verdicts | `engine/calibration.py`, `engine/deviation.py`, `engine/verdicts.py` |
| SCPI parse / typed values / error codes | `scpi/parse.py`, `scpi/values.py`, `scpi/errors.py` |
| Built-in component types (one module per class) | `library/components/*.py` (register in `__init__.py`) |
| Built-in real models + coverage tables | `library/instruments/<id>.yaml`, `<id>.coverage.yaml` |
| Load definitions/benches, macros, panels, migrations, templates | `library/loader.py`, `macros.py`, `panels.py`, `migrate.py`, `templates/` |
| Category default blocks (per class) and the definition deep merge (D59) | `library/class_defaults.py` |
| Coverage tables & manual cases (D42) | `library/coverage.py` |
| File models (pydantic) → JSON Schema | `models/bench.py` (`simubench.bench/2`), `models/definition.py` (`simubench.instrument/1`) |
| HTTP/WS message models → OpenAPI | `models/api.py` |
| FastAPI app / sessions served / component catalog | `server/app.py`, `server/manager.py`, `server/catalog.py` |
| MCP tools | `mcp_server.py` |
| PyVISA backend / raw socket / HiSLIP | `access/pyvisa_backend.py`, `access/scpi_server.py`, `access/hislip_server.py` |
| In-process Python API, pytest plugin | `api.py`, `pytest_plugin.py` |
| CLI subcommands (serve, tui, mcp, validate, migrate, coverage, cases, schemas, replay, doctor…) | `cli.py` |
| TUI (Textual) | `tui/` (screens in `tui/screens/`, wording in `tui/i18n/`) |

## Elsewhere

- `web/` — React + Vite + TanStack Query + xyflow; see the `web-ui` skill.
- `schemas/` — generated (`simubench schemas --out schemas`), committed, never hand-edited.
- `validation/instruments/`, `validation/benches/` — `sb-*` validation instruments (not shipped);
  `acceptance/` — scenario tests + benches; `refcalc/` — reference calculations.
- `examples/benches` — real-model benches only.
- `manuals/` — vendor manuals the real models follow (PDF in Git LFS, `.txt` beside it; index `manuals/README.md`).
- Version and changelog (D58): version from git date tags (`pyproject.toml` `[tool.hatch.version]`,
  `hatch_build.py` writes `src/simubench/_version.py`, git-ignored); tags by
  `scripts/release_tags.py` in CI on `main`; changelog `scripts/changelog.py`, written into the
  wheel and `dist/CHANGELOG.md` by `scripts/build-wheel.sh` (`simubench changelog`).
- `tests/models/<id>/cases.yaml` — manual cases per model; `tests/definitions/` — test-only defs.
- `tests/helpers.py` — shared test skeletons: example-script loading, example-bench script runs,
  session queries, golden-table runners (`*_migration.py`), the MCP tool-result unwrap; new
  per-model files use these instead of copying (#698). TUI mocks and pilot helpers:
  `tests/tui_mock.py`.
- Key guard tests: `test_model_components.py` (`ALLOWED`), `test_block_coverage.py` (`EXEMPT`),
  `test_docs_examples.py` (doc snippets run), `test_component_catalog.py`.

## Docs: which one answers what

| Question | Doc (read by section) |
| --- | --- |
| Was this already decided? | `decisions.md` (D1–D52; `grep -n '^## D'`) |
| Is it done already? | `progress.md` (milestones); details: grep `progress-log.md` |
| Definition YAML format, declarative commands, replies, mappings | `instrument-authoring.md` 2–5 |
| Real-model rules | `real-models.md`; meters/calibrators/SMUs: `metrology-authoring.md` |
| Functional blocks per instrument class (D50) | `component-map.md`, `docs/blocks/<class>.yaml` |
| Terms | `glossary.md` |
| Web look & interaction | `ui-design.md`; TUI: `tui-design.md`, `tui-screens.md` |
| Security / tokens | `security.md` |
| Per-class numeric validation | `validation-<class>.md` |
