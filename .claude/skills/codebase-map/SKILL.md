---
name: codebase-map
description: Where each part of SimuBench lives and which file to open for a given change (engine, SCPI, definitions, server/API, MCP, TUI, web, tests, docs). Use instead of exploring the tree or reading architecture.md end to end.
---

# Codebase map

Package layers (D87): `base (units, jsonsafe, errors, paths)` → `scpi` → `models` (file formats
and their pure transforms) → `engine` → `library` → `api`/`replay`/`verdicts`/`cases` →
`contracts` → `access` → `server` (with `server/assistant/`, `server/ops.py`), `mcp_tools` →
`tui`/`cli`. Imports point down only and form no cycle, late imports included:
`scripts/check_layers.py` (in `check.sh`) holds the table and fails otherwise; a new top-level
package gets a row there.

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
| Session (facade: actions, clock, events, instrument I/O), action queue, virtual clock | `engine/session.py`; idle modes (D47) `engine/sleep_mode.py` |
| Real-time budget of a read, cancelling one, the `abort` event and how a replay reproduces it (D88) | `engine/budget.py`, `Session.checkpoint` / `cancel` (`engine/session.py`), `resend` (`replay.py`) |
| The session's collaborators (D60): evaluation caches + sample fan-out count (`Session.cache`) / operating point, mode iteration (`Session.solver`, reads the session through `SolveHost`) / one-driver-per-net feed for non-circuit media (`nets.feed`) | `engine/evalcache.py` / `engine/solver.py` / `engine/nets.py` |
| Instrument runtime, SCPI command layer | `engine/instrument.py`; static lookup `engine/commands.py`; why a header is not executed and the closest one (coverage table) `engine/coverage_advice.py`; operations pending until a later instant, `*OPC`/`*WAI` bookkeeping `engine/pending.py`; the settings' specs, values, coercion, `*RST` reset and run-time bounds `engine/settings.py` (also `validate_value`, `snap_ladder`); channel numbers `engine/channels.py`; internal signal wires `engine/wiring.py`; parsing and executing a program message (dispatch, settings / channel-list / operation commands, value parse and format) `engine/command_runner.py`; the ESR/ESE/SRE registers and the status byte `engine/status_registers.py`; `*IDN?`/`*STB?`/`*TRG`/clock built-ins `engine/builtin_commands.py`; device errors, -113/-104 classification `engine/error_reporter.py` |
| Component base class, `@component`, `@op` | `engine/component.py` |
| Evaluation context (tuning point, evaluation instant) — the frozen value both caches key on; `Session.evaluating` / `tuned` / `at_instant` change it (D67, #1509) | `engine/evalcontext.py`, `engine/session.py` |
| Circuit (MNA per frequency) / signals / noise | `engine/circuit.py`, `engine/signal.py`, `engine/noise.py` |
| Sample streams after an ADC: `FrameRequest`, `SampleFrame`, limits; the frame cache is `Session.cached_frames` (D67) | `engine/stream.py` |
| Oscilloscope components (#1386): the channel chain (probe, vertical/front end, ADC, decimator) and the timebase / the trigger (edge and advanced types, the trigger search) / run control, records, averaging, segments / the measurement component (op pipeline, screen list, statistics, cursors, markers; the algorithms are `measurements.py`) | `library/components/scope.py` / `scope_trigger.py` / `scope_acquisition.py` / `scope_measurements.py` |
| A scope's record (`Record`, `channel_index`); deep records computed where read — the chain's captured views (`AdcView`, `DecimatorView`), tiles, envelope, chunked measurements, `EAGER_POINTS` (#1012, real-models.md 10) | `library/components/scope_record.py` |
| Cascaded RF figures (Friis, intercepts, distortion products, compression) of the `RfStage` blocks; the tuning context is `Session.tuned` | `engine/rfcascade.py` |
| Internal probe: what a port inside an instrument carries (analog lines, a samples frame and its spectrum, the "tuned to" of a receiver), shared by HTTP and MCP; hidden in validation mode | `server/services/internal_probe.py` |
| Bench validation / definition validation | `engine/validate.py`, `engine/validate_definition.py` |
| Bench edits (canvas + MCP share them) | `engine/edit.py` |
| Faults / run record / replay | `engine/faults.py`, `engine/record.py`, `replay.py` |
| Event contract (stream shape, kinds registry, WS projection) | `engine/events.py` (re-exported by `contracts/api.py`, D57) |
| Calibration, deviation, verdicts | `engine/calibration.py`, `engine/deviation.py`, `verdicts.py` |
| SCPI parse / typed values / error codes | `scpi/parse.py`, `scpi/values.py`, `scpi/errors.py` |
| Built-in component types (one module per class) | `library/components/*.py` (register in `__init__.py`) |
| Built-in real models + coverage tables | `library/instruments/<id>.yaml`, `<id>.coverage.yaml` |
| Load definitions/benches, macros, panels, migrations, templates | `library/loader.py`, `macros.py`, `panels.py`, `migrate.py`, `templates/` |
| Instrument classes in one place: label key, readings polling, row kind, panel policy, scriptscan hints (D79) | `models/categories.py` |
| Category default blocks (per class) and the definition deep merge (D59) | `library/class_defaults.py` |
| Script scanner (bench/definition drafts from a PyVISA script) | `library/scriptscan.py` |
| Coverage tables & manual cases (D42): the formats, `check`, finding a model's cases / running them on a fresh bench (`run_cases`, `junit_xml`) | `library/coverage.py` / `cases.py` |
| The packaged Hello World bench, copied into a workspace by `POST /api/benches/hello` (D65) | `library/examples/hello.bench.yaml`, `server/benchfiles.py` (`create_hello`) |
| File models (pydantic) → JSON Schema | `models/bench.py` (`simubench.bench/2`), `models/definition.py` (`simubench.instrument/2`) |
| HTTP/WS message models → OpenAPI | `contracts/api.py` |
| FastAPI app (assembly, middleware, SPA) / session lifecycle / component catalog | `server/app.py`, `server/manager.py` (`SessionManager`: start, reset, stop, try sessions), `server/catalog.py` |
| Read-only session DTOs (describe, internals, scope screen, readings, nets, faults) / actions (SCPI, configure, panel) | `server/views.py` (`manager.views` for the ones that need the manager), `server/drive.py` |
| Read-only projection over a bench *file* (build without running, leave out the broken parts) / the benches list DTO | `server/preview.py`, `server/views.py` (`SessionViews.bench_files`) |
| Workspace event stream (`/api/events`: file_changed, clients_changed, sessions_changed; kinds registry, fan-out hub, external-change watcher) | `server/workspace_events.py` (D85; write points: `BenchFiles._write`/`DefinitionFiles.save`, `on_change` on the four access servers, manager start/stop) |
| HTTP routes, one router per area (each `build_router` only calls module-level `_<group>_routes(ctx, router)` functions of ≤ 80 lines) (benches, ops, definitions, acceptance/try, sessions, events); shared auth and error mapping | `server/routes/*.py` (`context.py`: `ApiContext`) |
| Shared application services: logic both the routes and the MCP tools call (faults, clock, session start / reset / stop, the internal probe `internal_probe.py`, a new definition from a template or blank `new_definition.py`); raise domain errors only, never `HTTPException`/`ToolError`. Domain errors `Forbidden` / `NotFound`, `FILE_ERRORS`: `server/errors.py`. Error → HTTP row: `ApiContext.error_row` / `http_error` (`routes/context.py`); error → tool text: `_ERROR_ROWS` (`mcp_tools/context.py`). A route catches a tuple and calls the table; it does not map an exception itself. Bench / definition files are shared stores (`BenchFiles`, `DefinitionFiles`), not wrapped again. | `server/services/*.py`, `server/errors.py` (tests: `tests/test_services.py`) |
| MCP tools, one module per area (bench, definitions, sessions); shared guard / errors | `mcp_server.py` (assembly), `mcp_tools/*.py` (`context.py`: `McpContext`) |
| Per-user folders (plugin trust, console history, the assistant's key and settings overrides), never inside a workspace; `SIMUBENCH_HOME` overrides; keep them small (#1237) | `paths.py` (`user_data_dir`, `user_state_dir`), `engine/plugins.py` (`trust_workspace`), `cli.py` (`simubench trust`), `server/assistant/config.py` (`OVERRIDES`) |
| What a workspace's `.simubench/` holds, orphans (data whose file is gone), `simubench storage [--clean]`, the `doctor` line, the API `GET /api/storage` / `POST /api/storage/clean` and the Web "workspace data" dialog (#1238) | `storage.py`, `cli.py` (`_storage`), `server/ops.py` (`doctor`), `server/routes/ops.py`, `web/src/components/app/StorageDialog.tsx` (logic: `storage.ts`) |
| Web AI assistant (D66, `docs/ai-assistant.md` 11): LLM settings, PydanticAI models, tools and their server-side rules, turn runner / events / rollback, compaction, loop guard | `server/assistant/` (`config.py`, `llm.py`, `tools.py`, `service.py`, `compaction.py`, `guard.py`, `store.py`), routes `server/routes/assistant.py`; tests use `tests/helpers.py` `ScriptedLLM` |
| A definition's structure for the Web build area (effective components/wiring/ports with extends merged in; a whole structure written back keeping only what the file does not inherit, #1201) | `server/definitionstructure.py` (`DefinitionFiles.structure` / `save_structure`) |
| Writing an edited bench back: only the change, comments and number spelling kept (D73, #1254) | `server/yamlpatch.py`, `server/benchfiles.py` (`save`, `_patched`) |
| Copy a bench, "save as my template" (snapshot, no `extends`), definition restore, bench diff ("what changed") | `server/benchfiles.py` (`copy`, `remove_created`), `server/mytemplates.py`, `server/definitionfiles.py` (`restore`, `expanded`), `server/benchchanges.py` |
| Access servers (raw socket / HiSLIP / VXI-11 / bench link): one registry row per protocol (#1475, D84 — `connections`/`addresses`/`stop`, `access/__init__.py` `AccessServer`; port semantics stay in each server; LOCAL is judged from the connections) | `access/scpi_server.py`, `access/hislip_server.py`, `access/vxi11_server.py`, `access/bench_link.py`, `access/pyvisa_backend.py` (in-process), registry `server/manager.py` (`ServedSession.servers`), locks `access/bus.py` |
| In-process Python API, pytest plugin | `api.py`, `pytest_plugin.py` |
| CLI subcommands (serve, tui, mcp, validate, migrate, coverage, cases, schemas, replay, doctor…) | `cli.py`; the shared serving options, `build_manager` and the address-table line live in `service.py` — add a service start parameter there once, all entries get it |
| TUI (Textual) | `tui/` (screens in `tui/screens/`, wording in `tui/i18n/`). A new instrument screen = subclass `tui/screens/editing.py`'s `ControlEditingScreen` (all write/edit orchestration: toggle with confirmation, in-place field editing, focus, flash) + render row functions in `tui/rows.py` |

## Elsewhere

- `web/` — React + Vite + TanStack Query + xyflow; see the `web-ui` skill.
- `schemas/` — generated (`simubench schemas --out schemas`), committed, never hand-edited.
- `validation/instruments/`, `validation/benches/` — `sb-*` validation instruments (not shipped);
  `acceptance/` — scenario tests + benches; `refcalc/` — reference calculations.
- `examples/benches` — real-model benches only.
- `manuals/` — vendor manuals the real models follow (PDF in Git LFS, `.txt` beside it; index `manuals/README.md`).
- Version and changelog (D58): version computed from the commit history, no tags (`scripts/version.py`,
  called by `pyproject.toml` `[tool.hatch.version]`; `hatch_build.py` writes `src/simubench/_version.py`,
  git-ignored); changelog `scripts/changelog.py` (one section per build), written into the
  wheel and `dist/CHANGELOG.md` by `scripts/build-wheel.sh` (`simubench changelog`).
- Windows portable bundle (D20, #1188): `scripts/build-windows.sh` → `scripts/windows_bundle.py`
  (embeddable CPython + Windows wheels + launcher exes, built on Linux), smoke test
  `scripts/smoke_windows_bundle.py` (CI `windows-bundle` job on main). The double-click start is
  `simubench launch` = `serve --mcp` (MCP over HTTP at `/mcp`, `server/app.py`).
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
| Was this already decided? | `decisions.md` (D1–D67; `grep -n '^## D'`) |
| Is it done already? | `progress.md` (milestones); details: `grep -rn` over `docs/progress-log.md` (archive) and `docs/progress-log/` |
| What is the product for, and how did it stand on 2026-11-11? | `positioning-review.md` (internal tool; shortage + own-instrument scenarios; no plans) |
| What comes next, in what order, who does each step, what counts as done? | `product-plan.md` — the **only** place future work is planned (draft): owner-only items H1–H7, near-term P1/P2, the five-year scope and analyzer targets with capability gaps C1–C17 and phases, issue-ready rows. Never add a plan to another doc |
| Signal chain inside instruments: sample streams, the ADC boundary, internal probes, the two evaluations, the issue split (D67) | `signal-chain.md` (implemented, first version); how to write one in a definition: `instrument-authoring.md` 2.11 |
| Definition YAML format, declarative commands, replies, mappings | `instrument-authoring.md` 2–5 |
| Real-model rules | `real-models.md`; meters/calibrators/SMUs: `metrology-authoring.md` |
| Functional blocks per instrument class (D50) | `component-map.md`, `docs/blocks/<class>.yaml` |
| Terms | `glossary.md` |
| Web look & interaction | `ui-design.md`; TUI: `tui-design.md`, `tui-screens.md` |
| Security / tokens | `security.md` |
| Per-class numeric validation | `validation-<class>.md` |
