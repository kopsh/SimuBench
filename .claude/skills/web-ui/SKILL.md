---
name: web-ui
description: Work on the SimuBench Web Bench (web/) — layout of the React app, generated API types, i18n, shared TUI wording, UI rules and which checks run where. Web interaction is lead-agent (核心) work.
---

# Web Bench

Stack (D21): React + Vite + TypeScript, TanStack Query, `@xyflow/react` (canvas), shadcn-style
primitives, i18next, sonner toasts, vitest, Playwright (e2e + visual), Storybook.

## Where

- `web/src/api/` — `client.ts` (openapi-fetch, types `Schemas["X"]`), `hooks.ts` (all queries and
  mutations), `events.ts` (WebSocket), `generated/` (**never hand-edit**).
- `web/src/features/{home,instrument,runs,workbench}/` — pages; pure logic in `model.ts` next to
  `model.test.ts` (unit-test logic there, not components).
- `web/src/components/ui/` — primitives (button, dialog, tabs, table, select…): reuse, don't add a
  new one for a one-off. `components/app/` — app-level pieces.
- `web/src/components/app/DraftInput.tsx` — the one editing lifecycle of a front-panel field (focus
  starts a draft, Enter/blur enters it, Esc restores). New editable fields use it and pass their
  parse rule (`commit` / `SKIP` / `INVALID`: unit stripping, INF/high-Z, plain trim) as a prop; never
  copy the focus/blur/Esc handlers.
- `web/src/features/workbench/classes.tsx` — the one list of instrument classes (icon + own screen).
  A new class is added there, plus `acceptance.class.<id>` in both i18n files (`classes.test.ts`
  checks they agree); do not add icon maps or `if (template === …)` chains elsewhere.
- `web/src/features/home/` — the product page (D65): `HelloPreview.tsx` plays engine data from
  `hello-preview.json` (regenerate: `uv run python scripts/capture_hello_preview.py`), `Showcase.tsx`
  shows real screenshots in `assets/` (TUI: `docs/tui/hello.steps.yaml`; web: the workbench on the
  Hello World bench, dark, 1440×900, WebP). Never hand-draw an instrument picture on the home page.
- `web/src/features/assistant/` — the AI assistant panel (#877): `model.ts` (event reducer, step
  wording keys, SSE parser; unit-tested), `stream.ts` (follows a turn's events with fetch, token in a
  header), `AssistantView.tsx` (presentation, stories), `AssistantPanel.tsx`, settings, restore and
  save-as-template dialogs. Its wording (`assistant.*`, `template.*`) is registered Web-only in
  `scripts/check_tui_i18n.py`. The e2e test `e2e/assistant.spec.ts` talks to a scripted
  OpenAI-compatible LLM, `e2e/fake-llm.mjs`, which `e2e/serve.mjs` starts on the port after the service's.
- `web/src/features/workbench/internals/` — the internals view (#20, #739) and the internal probe
  (#953, signal-chain.md 1.2–1.3): `layout.ts` (columns from the signal wires; refs only place
  off-path blocks), `trace.ts` (lit chains), `probe.ts` (wire labels, probe pictures, the per-port thumbnails
  `miniOf`; unit-tested), `ProbeView.tsx` (the panel and its states), `InternalsView.tsx` (opens at
  a readable zoom, not fitted: off-screen blocks are reached by the overview map, the fit-view
  control, or focus — an e2e test clicks `.react-flow__controls-fitview` before clicking across the
  graph; thumbnails come from `useInternalProbes`, one probe per output on screen). A receiver's "tuned to" (#959):
  the context comes with the internals (`InstrumentInternals.tuning`), the choice (`Tune` in `probe.ts`) is state of
  `InternalsPanel` so it stays while the probe moves from port to port, and `ProbeView.tsx` draws it (`TuneBar`) and the
  spectra one can read with a cursor and click to retune. Their fixtures are captured from the engine
  (`internals.fixture.json`: the oscilloscope; `specan.fixture.json`: the analyzer of
  `web/e2e/fixtures/benches/sa-two-tone.bench.yaml`) — regenerate after a model or API change:
  `uv run python scripts/capture_internals_fixture.py [specan.fixture.json]` (name one to leave the other's
  instant, and its baselines, alone). Full screen is the same tree in-app (`fixed inset-0 z-50`,
  `data-fullscreen` on `data-testid=internals-panel`, Esc listened for on `window`); the assistant
  sidebar belongs to the page, so it stays open across the switch (`e2e/workbench.spec.ts`, internals
  full screen).
- `web/src/features/instrument/structure/` — the acceptance page's build area (#1201, D3,
  instrument-authoring.md 9): `model.ts` (the structure's edits — add, wire, delete, parameters, panel
  ports, settings, commands; layout — chains in columns, the rest in a grid; the references and followed
  settings the canvas draws, #1212; SI-prefixed numbers, table rows, library families, #1214;
  unit-tested), `BuildTab.tsx` (library, canvas, side panel; `SettingsPanel.tsx`
  holds the settings and commands pages, #1206; each edit sends the whole
  structure to `PUT /api/definitions/{id}/structure`, one save at a time; undo/redo saves the earlier
  structure as a new version, #1207). Dragged positions go to `instruments/<id>.layout.json` through
  `PUT /api/definitions/{id}/layout` (no version, #1207). e2e: `e2e/build.spec.ts`. (Not `build/`: `.gitignore` hides it.)
- `web/src/features/workbench/store.ts` — the workbench's UI state (zustand): tabs, selection, the
  locate request (`reveal`, #800), and the inspector shown or folded per kind of tab
  (`INSPECTOR_SHOWN`, `lentTo`: a selection or locate shows a folded inspector until the tab
  changes, #1221) and its width per kind (`INSPECTOR_SIZE`, narrower beside a screen, #1229).
  Keep such rules in store actions (unit-tested in `store.test.ts`), not in effects that call
  `setState`: the React compiler lint rejects those, and refs written during render.
- `web/src/features/workbench/onScreen.ts` — which settings a screen shows as fields: a
  `StateField` inside a screen tab counts its path while mounted, and the inspector beside that
  screen leaves those paths out (#1229). A new screen gets this for free by using `StateField`; a
  field that writes SCPI itself calls `useCountOnScreen(instrument, path)` (the analyzer's
  `ScpiField`, #1252). Enum options go through `distinctOptions` (model.ts) so an alias sharing a
  label is not offered twice.
- `web/src/lib/motion.ts` — instrument-screen motion (scope persistence, analyzer sweep line); every
  motion checks `prefersReducedMotion()`, and the e2e config runs with `reducedMotion: "reduce"`.
- `web/src/lib/enums.ts` — `oneOf` guards and tuples (`CLOCK_MODES`, `PANEL_ACTIONS`) for i18n key
  families built from API strings; use them instead of `as "a" | "b"` casts.
- `web/src/i18n/{zh,en}.ts` — all text; `t("area.key")`. zh is primary (D23).
- `web/e2e/*.spec.ts` (Playwright, served by `e2e/serve.mjs`), `web/visual/` (story snapshots);
  full-app screenshots at 1280/1440 live in `web/e2e/layout-visual.spec.ts` (baselines beside it,
  `pnpm e2e e2e/layout-visual.spec.ts --update-snapshots` after an intended change).
- Visual baselines are **Linux/Chromium** (the CI e2e runner): `--update-snapshots` on a Mac writes
  wrong-platform PNGs. To refresh, take the `playwright-report` artifact's `*-actual.png` from the
  failed run (two runs producing byte-identical actuals = the render is stable). Both screenshot
  specs force-load every declared `FontFace` before capture — text metrics decide how cards wrap,
  and a wrap flip shifts the whole canvas fit (#618): keep that wait when adding new screenshot
  tests. The Canvas itself refits once fonts settle (#645) — keep that refit intact too.
- The fonts are not bundled: `--font-sans` ("Inter", "Noto Sans SC", …) and `--font-mono`
  ("JetBrains Mono", …) come from the machine's installed fonts (`web/src/styles/globals.css`). The CI
  runner has neither Inter nor JetBrains Mono and falls back (DejaVu Sans Mono is wider), and the
  baselines are its renders. A machine that has them installed renders narrower text: on such a
  machine `pnpm visual` fails exactly four stories whose canvas refits on text width (Assistant/Panel
  Stuck and Workbench/Live canvas Counter, light and dark, ~2 % of pixels) — expected, not a regression
  (#984). Judge baselines by CI's actuals only.
- WCAG AA contrast in an e2e test: `expectContrastAA(locator)` in `e2e/judge.ts` (colors read back
  through a canvas, so the oklch tokens and alpha count; SVG wire labels on their `edge-textbg`). The
  internals view checks its probe panel and wire labels in both themes (#984).

## Rules

- API changed in Python (`src/simubench/models/api.py`, `server/routes/*.py`) →
  `uv run simubench schemas --out schemas` then `cd web && pnpm gen:api`; commit `schemas/` and
  `web/src/api/generated/`. `pnpm check:api` fails on stale output.
- New text: add the key to **both** `zh.ts` and `en.ts`. If the TUI shows the same wording, the
  TUI copy (`src/simubench/tui/i18n/en.py`, English only, D23) must match `en.ts` exactly —
  `scripts/check_tui_i18n.py`.
- Wording follows `docs/glossary.md`; errors read "what happened → where → what to do".
- Interaction rules: `docs/ui-design.md` 6 (selection sync, ⌘K palette, feedback within 100 ms,
  progress after 1 s, empty states with a next step, read-only on disconnect,
  `prefers-reduced-motion`). Layout/visual language: `ui-design.md` 3–5.
- Front-panel readings/operations do not go through SCPI (D32).

## Before you ship a screen (checklist from `docs/ui-design.md` 4–6)

- **Every state is built**, not just the happy path: loading, empty (says what appears here + a
  next step), error ("what happened → where → what to do"), disconnected (read-only banner,
  auto-reconnect), long text (English is longer than Chinese, D23).
- **Tokens, not literals**: colours, spacing and radii come from the design tokens in
  `web/src/styles/`; light and dark both checked. Channel colours CH1 yellow / CH2 cyan / CH3
  magenta / CH4 blue are the same on card, trace, wire and legend; purple is reserved for
  simulation-only info (true values, fidelity). State colour is always paired with text or an icon.
- **Numbers** in the mono font with tabular digits, SI prefixes, right-aligned, resolution as the
  instrument's; Chinese body line-height 1.6, a space between CJK and Latin.
- **Density**: 4 px base, controls 28–32 px, table rows 28 px.
- **Interaction**: selection syncs across canvas / inspector / log / timeline; the action is in
  the ⌘K / Ctrl+K palette; feedback ≤100 ms, progress after 1 s, cancel for long work;
  keyboard reachable with a visible focus ring; honour `prefers-reduced-motion`.
- **The TUI shows the same thing?** Its wording and behaviour must agree — see the `tui-ui` skill.
- Add or update the story (Storybook) for a new component state; the stories are the design source (D23).

## Reviewing and improving the UI (walkthrough)

How the lead agent reviews the Web Bench and turns findings into work (#1220–#1222). Interaction
changes are `核心`; a finding becomes a `可认领` issue only with its fix settled and written down.

1. **Walk the real app on Hello World**, as a new user would: home → 「从 Hello World 开始」 → canvas →
   each instrument's screen → internals → acceptance page → build mode → bottom panel → ⌘K; then
   dark mode and 1024 px. Build (`pnpm build`), serve a scratch workspace
   (`uv run simubench serve --workspace <tmp> --token t --port <p>`), and script the walk with
   Playwright from `web/` (it resolves `@playwright/test` there; keep the script in your scratchpad
   and copy it in to run). Each run is a fresh browser: per-browser memory (open screen tabs, the
   inspector fold, `localStorage`) starts empty, so "lost on refresh" must be checked in one context.
2. **Check each finding against the engine before calling it a UI bug.** A number that looks wrong
   may be the model being right: the UXR's Vpp of 1.25 V for a 1 Vpp setting is its data-sheet
   noise, so the fix was an amplitude column, not the bench (#1220). A behaviour may be a recorded
   choice: the scope's live frame while the command clock stands still is D44 — read
   `docs/decisions.md` before "fixing" it.
3. **Know what a panel holds before folding it.** The inspector also carries the SCPI console and
   the settings a screen lacks (beside a screen it shows only those, #1229); the bottom panel
   already starts as its tab bar (#1221).
4. **Hello World feeds the home page.** Changing `src/simubench/library/examples/hello.bench.yaml`
   or a model it uses means regenerating, in the same pull request: `hello-preview.json`
   (`scripts/capture_hello_preview.py`), the web showcase screenshots (the workbench on Hello World,
   dark, 1440×900, PNG → WebP, e.g. `ffmpeg -i x.png -c:v libwebp -quality 82 x.webp`; wait for the
   "已创建" toast to go) and the TUI ones (`docs/tui/hello.steps.yaml`; stop any service holding the
   SCPI ports first, or the run fails with 409). `tests/test_hello_bench.py` pins its numbers.
5. **Traps met on the way:**
   - No regular expressions in e2e (D49): `expectText(locator, predicate, what)` or a string
     `name` from `e2e/judge.ts` / Playwright.
   - Clicking across the internals graph: click `.react-flow__controls-fitview` first, or the view
     can pan while the click is aimed and it lands beside the port.
   - Do not transition an inherited `color`: text inheriting it is briefly the background's colour
     on a theme switch (`scroll-area.tsx` did, contrast 1.03 in `expectContrastAA`). Transition
     `box-shadow`/`opacity`, or give the text its own colour.
   - The full-app visual baselines mask the measurement table and other live values: a new column
     does not change them, so assert it in an e2e test (`meas-ch1-<key>`).
6. **Deliver**: one issue per batch (content, layout, polish), fix the content batch first (smallest
   change, biggest effect on first impressions), and say in the pull request where the result
   differs from the issue and why.

## Commands

```bash
cd web
pnpm install
pnpm dev                       # with: uv run simubench serve --workspace validation
pnpm -s typecheck && pnpm -s lint && pnpm -s test     # what PR CI runs (also check:api)
pnpm e2e                       # Playwright (end-to-end + full-app visual); gates pull requests
pnpm visual                    # Storybook screenshot diffs; gates pull requests
```
