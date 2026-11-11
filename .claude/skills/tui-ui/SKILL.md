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
- **Colour**: only the tokens in `theme.py` (`fg muted focus bad ok sim ch1–ch4 hud line bar`, the HUD palette,
  `tui-screens.md` 1.2). Red marks only the offending words, never a whole card or row. State is
  always also text or a glyph (`●` `○` `▲` `✗` `→` `·` `—` `…`) — `NO_COLOR` turns every token
  into the default foreground and the screen must still read.
- **HUD look** (`widgets.py`): the bars are `bar_text` (on `$bar`; under `NO_COLOR` reverse video
  — `is_mono(app)`), box titles wear amber brackets through `decorate_title` (cards, and every
  `_Box` title not already padded with `─`), box lines are `$line`, titles `$hud`. Never hardcode a
  hex colour in a widget: add a token to `COLORS` (it must also be in `MONO_VARS`; a test checks).
- **Overview** is status-first: a card's light, its alerts and the attention strip all come from
  `cards.py` (`CardData.alerts`, `status_light`) — add a new kind of alert there, never in the
  screen. `w` (overview and instrument screens alike) goes through `reasons.explain_instrument`.
- The scope screen's settings column is fields too (issue 680), not `[Name]` buttons: same
  `ControlEditingScreen` editing, `label value` at a capped width — the value keeps its cells,
  the label ellipsises (`_field_width()`; the instrument screen's grid passes None).
- **Fields** (`ControlButton`, kind switch / select / number) read `label value` and are edited
  where they stand (`controls.FieldEdit` is the pure state; `editing.edit_key` the keys): the
  button consumes *every* key while `edit` is set (`check_consume_key`), so screen bindings
  (`e`, `w`, `L`) cannot fire mid-edit. Reuse `parse_input` / `range_text`; a value under an
  `_auto` switch shows `AUTO`. Actions (panel buttons, softkeys) stay `[label]`. `[` `]` swap the
  screen for the next instrument through `app.open_instrument(name, replace=True)`.
- **Buttons** are a `ButtonGrid` (`screens/editing.py`): equal columns, wrapping rows, never cut
  mid-word; `fit(width, max_rows)` returns the rows shown, and the screen's row budget uses that.
  Readings come before buttons — a short screen shows one row, the rest scroll with the focus.
- **Sizes come from the data, not from the layout pass** (the layout has not run on the first
  refresh): instrument boxes take their width from `_channel_width()` (inner width: less the
  border and padding — a row wider than the Static *wraps*, it does not truncate), their height
  from `_channel_row_budget()` / `_side_height()`, and the body height from `_body_rows()` (it
  already excludes the demo's caption row). `call_after_refresh(_relayout)` redraws once real
  widths exist. No scrollbars anywhere: a scrollbar steals a column and wraps every row. The scope
  screen's trace is the same trap: its width is the wave box's inner width, and its frame is
  fetched at twice that (`app.scope_points` keeps it across refetches).
- **Numbers**: use `fmt.fmt` (SI prefixes, right-aligned); never hand-format a value. Use
  `cell_len` / `pad` / `ralign`, not `len`, for widths.
- **No model names** in `src/simubench/tui` (D42), comments and docstrings included:
  `tests/test_tui_no_model_names.py` scans for every definition's `id` and `model`, no allow-list; write
  examples as `awg · MODEL-A`.
- **Overview log drawer**: shut by default (`DRAWER_ROWS = 3`), `L` opens it (`l` stays the full-screen
  log); a shut drawer marks its title `✗ N new` for errors not yet seen but never opens itself
  (`tui-screens.md` 4.5).
- **State tree** (`t`, `screens/statetree.py` + pure `tree.py`): all state variables by path group, edited
  through the same `ControlEditingScreen` layer; rebuild only when the *shape* changes (folds live on
  `app.tree_open`), `h` adds hidden values (`deviations`, hidden faults) marked `◆ hidden` in text, not
  colour alone. `L` is local control on the instrument screen, so hidden is `h` (`tui-screens.md` 5.6).
- **Palette** (`palette.py`, `Ctrl+K` anywhere, `/` on an instrument screen): commands are generated from
  the app's real `BINDINGS`, the bench's instruments and their state variables — never listed by hand. A
  setting is never written from the palette: it jumps to the field (`go_to_field`) and types into it, so
  range check, units and the dangerous confirm stay the field's (`tui-screens.md` 2.5). Textual's own
  palette is off (`ENABLE_COMMAND_PALETTE = False`).
- **Keys**: table in `docs/tui-design.md` 4. `?` is the key help everywhere (`screens/help.py`,
  built from the real `BINDINGS`: a binding needs a line in `help.actions` in `i18n/en.py` or
  `tests/test_tui_help.py` fails); `w` is "why" (the reason chain) on every screen; the nets
  waveform is `v`. Mouse is optional. A key that clashes with the Web's wins for the Web.
- **States** (`tui-design.md` 7): connecting, disconnected (read-only, auto-reconnect), bad
  token, session gone, remote-held (read-only, `L` takes over), file changed, build errors — a
  new screen handles each, none shows a blank.
- **Wording**: new text goes in `tui/i18n/en.py`; wording shared with the Web must equal
  `web/src/i18n/en.ts` exactly (`scripts/check_tui_i18n.py`). Names follow `docs/glossary.md`;
  errors read "what happened → where → what to do".
- Front-panel readings/operations do not go through SCPI (D32). TUI-originated actions carry the
  client type "tui" so replays show who acted.

## Textual traps

- `DEFAULT_CSS` is **scoped** to the widget: a rule that starts outside it (`.-mono ControlButton`,
  the `NO_COLOR` rule) needs `SCOPED_CSS = False` on the widget.
- `VerticalScroll` defaults to `height: 1fr`; inside an `auto` row it swallows the screen.
- `export_screenshot()` titles the SVG with `App.title` — keep `TITLE = "SimuBench"`.
- The README screenshots (`docs/tui/*.svg`) and the snapshots must be regenerated together with a
  visible change: `tests/test_tui_t4.py` compares the docs, `tests/test_tui_snapshots.py` the rest.
  Both start servers on fixed ports (the identity box shows the addresses). The snapshot fixtures
  wait for their ports to be free (`wait_for_free_ports` in `tests/helpers.py`, #857) instead of
  failing with `address already in use`, so a module that follows another, or another xdist
  worker, no longer races; still don't give a new TUI module a port these use.
- Steps files (`script.py`, `--script`): `key` `scpi` `advance` `wait` `caption` `highlight`
  `screenshot`, plus the direct-navigation fields `open:` (open an instrument's screen),
  `reveal:` (focus and highlight a field, `go_to_field(…, edit=False)`), `home: true`
  (pop to the overview); `--size COLSxROWS` on the command line overrides the file's `size`
  (issue 684). `validate(steps, instruments)` simulates the steps in order and names the step
  number of every problem.

## Tests (D49)

| What | Where | Marker |
|---|---|---|
| row/card/format logic, key handling on `FakeClient`, screens' interface-level pieces | `tests/test_tui_units.py` (one file for all milestones, #699), `tests/test_tui_rows.py`, `tests/tui_mock.py` | `unit` (≤100 ms, no server) |
| screens + keys against the mock or the real server | `tests/test_tui_t2.py` / `t3.py` / `t4.py`, `tests/test_tui_e2e.py`, `tests/test_tui.py` | `tui_boot` (every pull request and `main`, #858) |
| SVG snapshots, 100×32 and 80×24 | `tests/test_tui_snapshots.py`, baselines in `tests/__snapshots__/` | `tui_boot` |

Shared pilot helpers (`fresh`/`settle`/`wait_for`/`wait_flat`/`flat`) live in `tests/tui_mock.py`;
the real-server bed (`Served`, `wait_connected`, `wait_quiet`) lives in `tests/test_tui.py` and is
imported by the e2e file. Don't copy either into a new file. Anything that boots a Textual app or
a real server is `tui_boot`-marked — a milestone file without the marker is a bug (#699 fixed two).
A screen that shows a reading with noise (the load, the power supply, a calibrator) is captured with
`Component.panel_frame` pinned (`pinned_panel_frame` in `tests/test_tui_snapshots.py`, #937): the noise
is a function of (seed, component, frame) and the frame counts every `readings()` call, so without the
pin a machine that polls once more captures other digits. A new snapshot of such a screen needs no
extra step (the fixture is autouse); regenerate baselines with the pin in place.

Prefer a unit test on the pure function over a snapshot. A snapshot changes only when the screen
is meant to change: review the SVG diff, then refresh with
`uv run pytest tests/test_tui_snapshots.py --snapshot-update` and commit the baselines.
Snapshots use fixed SCPI ports and `NO_COLOR` is cleared by the test — don't reintroduce either.
Every wait for a screen to come up ends on its readiness signal and is bounded by `SETTLE_S` (90 s,
below the 120 s test timeout) — a cold start under load needs it; don't shorten it, don't add sleeps.

## Commands

```bash
uv sync --extra tui                                        # Textual + httpx
uv run simubench tui validation/benches/smu.bench.yaml     # run it (or --attach URL)
uv run simubench tui <bench> --script docs/tui/find-the-trip.steps.yaml --out /tmp/shots   # headless SVGs
# README screenshots: the exact command in README.md ("截图与演示可复现"), output to docs/tui
uv run pytest tests/test_tui_units.py tests/test_tui_rows.py -q                           # fast loop
uv run python scripts/check_tui_i18n.py
```
