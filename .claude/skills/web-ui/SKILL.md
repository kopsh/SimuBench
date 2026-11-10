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
