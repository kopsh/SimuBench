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
- `web/src/i18n/{zh,en}.ts` — all text; `t("area.key")`. zh is primary (D23).
- `web/e2e/*.spec.ts` (Playwright, served by `e2e/serve.mjs`), `web/visual/` (story snapshots).

## Rules

- API changed in Python (`src/simubench/models/api.py`, `server/app.py`) →
  `uv run simubench schemas --out schemas` then `cd web && pnpm gen:api`; commit `schemas/` and
  `web/src/api/generated/`. `pnpm check:api` fails on stale output.
- New text: add the key to **both** `zh.ts` and `en.ts`. If the TUI shows the same wording, the
  TUI copy (`src/simubench/tui/i18n/`) must match exactly — `scripts/check_tui_i18n.py`.
- Wording follows `docs/glossary.md`; errors read "what happened → where → what to do".
- Interaction rules: `docs/ui-design.md` 6 (selection sync, ⌘K palette, feedback within 100 ms,
  progress after 1 s, empty states with a next step, read-only on disconnect,
  `prefers-reduced-motion`). Layout/visual language: `ui-design.md` 3–5.
- Front-panel readings/operations do not go through SCPI (D32).

## Commands

```bash
cd web
pnpm install
pnpm dev                       # with: uv run simubench serve --workspace validation
pnpm -s typecheck && pnpm -s lint && pnpm -s test     # what PR CI runs (also check:api)
pnpm e2e                       # Playwright; runs on main after merge
pnpm visual                    # screenshot diffs; runs on main after merge
```
