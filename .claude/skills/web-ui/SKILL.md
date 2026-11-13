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
  instant, and its baselines, alone).
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
  ("JetBrains Mono", …) come from the machine's installed fonts (`src/styles/globals.css`). The CI
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

## Commands

```bash
cd web
pnpm install
pnpm dev                       # with: uv run simubench serve --workspace validation
pnpm -s typecheck && pnpm -s lint && pnpm -s test     # what PR CI runs (also check:api)
pnpm e2e                       # Playwright; runs on main after merge
pnpm visual                    # screenshot diffs; runs on main after merge
```
