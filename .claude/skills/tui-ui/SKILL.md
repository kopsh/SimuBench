---
name: tui-ui
description: Work on the SimuBench TUI (Textual, `src/simubench/tui/`) — screen layout, colour tokens, glyphs, keys, shared wording, snapshot and unit tests. TUI interaction is lead-agent (核心) work; fixes and tests of an agreed screen are claimable.
---

# TUI (terminal console)

The TUI is a **client of the HTTP/WebSocket API**, like the Web Bench (D7, `docs/tui-design.md` 2):
it never calls the engine. English only (D23). Spec: `docs/tui-screens.md` (per-screen layout,
~675 lines — read one section: `grep -n '^#' docs/tui-screens.md`); rationale: `docs/tui-design.md`.

## Where

- `src/simubench/tui/app.py` app and connection loop; `screens/` one file per screen
  (`overview.py`, `instrument.py`, `scope.py`, `nets.py`, `runs.py`, dialogs).
- Pure logic lives **outside** the screens so it unit-tests without a terminal: `rows.py` (row
  renderers), `cards.py` (overview cards), `controls.py` / `screens/editing.py` (control editing),
  `fmt.py` (engineering units, cell width, padding — CJK-safe), `log.py`, `reasons.py`. Put new
  decisions there, keep screens to layout and keys.
- `theme.py` semantic colour tokens; `i18n/en.py` all text.

## Rules

- **Size**: designed at 100×32, minimum 80×24; smaller shows one "window too small" line, no
  squeezed layout (`tui-screens.md` 1.1). Every screen = 1-line top bar + content + 1-line bottom bar.
- **Colour**: only the tokens in `theme.py` (`fg muted focus bad ok sim ch1–ch4`,
  `tui-screens.md` 1.2). Red marks only the offending words, never a whole card or row. State is
  always also text or a glyph (`●` `○` `▲` `✗` `→` `·` `—` `…`) — `NO_COLOR` turns every token
  into the default foreground and the screen must still read.
- **Numbers**: use `fmt.fmt` (SI prefixes, right-aligned); never hand-format a value. Use
  `cell_len` / `pad` / `ralign`, not `len`, for widths.
- **Keys**: table in `docs/tui-design.md` 4. Every action is also reachable from the command
  palette (`Ctrl+P`); mouse is optional. A key that clashes with the Web's wins for the Web.
- **States** (`tui-design.md` 7): connecting, disconnected (read-only, auto-reconnect), bad
  token, session gone, remote-held (read-only, `L` takes over), file changed, build errors — a
  new screen handles each, none shows a blank.
- **Wording**: new text goes in `tui/i18n/en.py`; wording shared with the Web must equal
  `web/src/i18n/en.ts` exactly (`scripts/check_tui_i18n.py`). Names follow `docs/glossary.md`;
  errors read "what happened → where → what to do".
- Front-panel readings/operations do not go through SCPI (D32). TUI-originated actions carry the
  client type "tui" so replays show who acted.

## Tests (D49)

| What | Where | Marker |
|---|---|---|
| row/card/format logic, key handling on `FakeClient` | `tests/test_tui_units.py`, `tests/test_tui_rows.py`, `tests/tui_mock.py` | `unit` (≤100 ms, no server) |
| screen + keys against a real server | `tests/test_tui_t2.py` … `t4*.py`, `*_e2e.py` | `tui_boot` (runs on `main`) |
| SVG snapshots, 100×32 and 80×24 | `tests/test_tui_snapshots.py`, baselines in `tests/__snapshots__/` | `tui_boot` |

Prefer a unit test on the pure function over a snapshot. A snapshot changes only when the screen
is meant to change: review the SVG diff, then refresh with
`uv run pytest tests/test_tui_snapshots.py --snapshot-update` and commit the baselines.
Snapshots use fixed SCPI ports and `NO_COLOR` is cleared by the test — don't reintroduce either.

## Commands

```bash
uv sync --extra tui                                        # Textual + httpx
uv run simubench tui validation/benches/smu.bench.yaml     # run it (or --attach URL)
uv run simubench tui <bench> --script docs/tui/find-the-trip.steps.yaml --out /tmp/shots   # headless SVGs
uv run pytest tests/test_tui_units.py tests/test_tui_rows.py -q                           # fast loop
uv run python scripts/check_tui_i18n.py
```
