"""The oscilloscope screen (``Enter`` on a scope card, tui-screens.md 6): braille traces at the
terminal's dot width, a settings column, silence reasons, run/stop/single via POST panel.

The frame is requested with ``points=<2×width>`` so the server returns per-bin min/max instead
of every sample (the /screen contract of this PR); while the scope runs the screen re-reads it
every 200 ms (tui-screens.md 1.6), otherwise only after acquisition/state events.
"""

from __future__ import annotations

from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from .. import controls as C
from .. import i18n
from ..cards import opposite_ends
from ..fmt import fmt, pad
from ..reasons import ReasonReport, silence_report
from ..widgets import box_text, style_of
from .dialogs import ScpiConsole
from .editing import ControlButton, ControlEditingScreen
from .instrument import _Box

SETTINGS_WIDTH = 26  # the right settings column's total width (tui-screens.md 6.1)


def trace_frame(trace: dict[str, Any], cols: int, rows: int) -> tuple[list[list[int]], bool]:
    """One trace's dot grid as per-character bit ORs, plus whether it was clipped (6.1).

    Each braille character covers 2 dot columns × 4 dot rows; the server returned per-bin min
    and max (2 values per bin, bins == 2×cols). ``y`` maps volts to divisions by the channel's
    scale and offset; parts beyond the screen draw pinned to the top/bottom row.
    """
    grid = [[0] * cols for _ in range(rows)]
    minmax = trace.get("minmax") or []
    clipped = False
    if not minmax:
        return grid, clipped
    bins = len(minmax) // 2
    if bins == 0:
        return grid, clipped
    scale = float(trace.get("scale") or 1.0) or 1.0
    offset = float(trace.get("offset") or 0.0)
    total_rows = rows * 4
    for col in range(cols * 2):
        bin_index = min(bins - 1, col * bins // (cols * 2))
        lo = minmax[bin_index * 2]
        hi = minmax[bin_index * 2 + 1]
        y_top = (max(lo, hi) - offset) / scale  # divisions above the centre
        y_bottom = (min(lo, hi) - offset) / scale
        if y_top > 4.0 or y_bottom < -4.0:
            clipped = True
        r_top = round((4.0 - min(y_top, 4.0)) / 8.0 * (total_rows - 1))
        r_bottom = round((4.0 - max(y_bottom, -4.0)) / 8.0 * (total_rows - 1))
        char_col, side = divmod(col, 2)
        if char_col >= cols:
            continue
        # Dot row 0 is the top: the higher voltage paints the smaller row index.
        for r in range(max(0, r_top), min(total_rows - 1, r_bottom) + 1):
            grid[r // 4][char_col] |= _BRAILLE[r % 4][side]
    return grid, clipped


_BRAILLE = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def render_wave(
    app: Any,
    screen: dict[str, Any] | None,
    cols: int,
    rows: int,
    state: dict[str, dict],
    lang: str,
    open_channels: list[int],
) -> tuple[Text, list[int]]:
    """The braille trace area (tui-screens.md 6.1): grid dots, then the channels overdrawn.

    Returns the text and the channels that ran past the top or bottom (the title shows them).
    """
    mono = app.theme == "simubench-mono"
    grid: list[list[list[int]]] = [
        [[0] * cols for _ in range(rows)] for _ in range(5)
    ]  # per channel 1-4 + grid
    # The grid: 10 divisions across, 8 down, a dot every 4 points (muted); the centre is not bold.
    dot_rows = rows * 4
    for r in range(0, dot_rows, max(1, dot_rows // 8)):
        for c in range(0, cols * 2, 4):
            grid[4][r // 4][c // 2] |= _BRAILLE[r % 4][c % 2]
    clipped: list[int] = []
    traces = {t.get("channel"): t for t in (screen or {}).get("traces", [])}
    for ch in range(1, 5):
        trace = traces.get(ch)
        if trace is None or not (state.get(f"ch{ch}.display") or {}).get("value"):
            continue
        own, was_clipped = trace_frame(trace, cols, rows)
        if was_clipped:
            clipped.append(ch)
        for r in range(rows):
            for c in range(cols):
                if own[r][c]:
                    grid[ch - 1][r][c] |= own[r][c]
    text = Text()
    muted = style_of(app, "muted")
    for r in range(rows):
        for c in range(cols):
            ch_bits = [grid[k][r][c] for k in range(4)]
            drawn = False
            for k in (3, 2, 1, 0):  # later channels cover earlier ones (6.1)
                if ch_bits[k]:
                    if mono:
                        text.append(chr(0x2800 + ch_bits[k]))
                    else:
                        text.append(chr(0x2800 + ch_bits[k]), style=style_of(app, f"ch{k + 1}"))
                    drawn = True
                    break
            if not drawn:
                if grid[4][r][c]:
                    text.append(chr(0x2800 + grid[4][r][c]), style=muted)
                else:
                    text.append("⠀")
        text.append("\n")
    return text, clipped


def silence_lines(app: Any, screen: dict[str, Any] | None, state: dict[str, dict], lang: str) -> Text:
    """``CH2 无信号：<原因>`` in the channel's colour, one line per open silent channel (6.3)."""
    from ..reasons import silence_text

    text = Text()
    for trace in (screen or {}).get("traces", []):
        ch = trace.get("channel")
        silence = trace.get("silence") or []
        if not silence or not (state.get(f"ch{ch}.display") or {}).get("value"):
            continue
        line = i18n.t("scope.silenceLine", lang, ch=f"CH{ch}", reason=silence_text(silence[0], lang))
        text.append(line + "\n", style=style_of(app, f"ch{(ch or 1) % 4 or 4}"))
    return text


class ScopeScreen(ControlEditingScreen):
    """示波器 (tui-screens.md 6): wave area, settings column, per-instrument log, console."""

    screen_key = "scope"

    # Like the instrument screen: nothing focused on entry keeps ``:`` for the console (6.4),
    # and ``?`` explains the screen's trouble rather than whichever setting auto-focus landed on.
    AUTO_FOCUS = "#simubench-entry-no-focus"

    BINDINGS: ClassVar = [
        Binding("r", "run_stop", show=False),
        Binding("s", "single", show=False),
        Binding("c", "next_channel", show=False),
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_previous", show=False),
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("left", "cursor('left')", show=False),
        Binding("right", "cursor('right')", show=False),
        Binding("enter", "edit", show=False),
        Binding("e", "edit", show=False),
        Binding("space", "toggle", show=False),
        Binding("plus", "step(1)", show=False),
        Binding("minus", "step(-1)", show=False),
        Binding("colon", "console", show=False),
        Binding("left_square_bracket", "switch_instrument(-1)", show=False),
        Binding("right_square_bracket", "switch_instrument(1)", show=False),
        Binding("w", "reason", show=False),  # "why"; ? is the key help everywhere
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.instrument_name = name
        self._last_version: tuple[int, int] = (-1, -1)
        self._built = False
        self._last_cols = 0
        self._last_rows = 0
        self._cursor_channel = 1

    DEFAULT_CSS = """
    #scope-top { height: 1fr; }
    #wave-box { width: 1fr; }
    #settings-box { width: 26; margin-left: 1; }
    #settings-scroll { height: 1fr; }
    .button-row { height: auto; }
    .flash-line { color: $bad; }
    """

    # ---- data ----------------------------------------------------------------------------------------

    @property
    def frame(self) -> dict[str, Any] | None:
        """The instrument's latest /screen payload (never Textual's ``screen``)."""
        app: Any = self.app
        return app.screen_data.get(self.instrument_name)

    def screen_title(self) -> str:
        return self.instrument_name

    # ---- layout --------------------------------------------------------------------------------------

    def body(self) -> ComposeResult:
        with Horizontal(id="scope-top"):
            self._wave_silence = Static(id="wave-silence")
            self._wave_silence.can_focus = False
            self._wave_static = Static(id="wave-text")
            self._wave_static.can_focus = False
            self._wave_box = _Box(self._wave_silence, self._wave_static, id="wave-box")
            self._wave_box.can_focus = False
            yield self._wave_box
            self._settings_static = Static(id="settings-text")
            self._settings_static.can_focus = False
            self._settings_buttons = Vertical(id="scope-buttons")
            self._settings_flash = Static(classes="flash-line")
            self._settings_flash.can_focus = False
            self._settings_flash.display = False
            self._settings_scroll = VerticalScroll(
                self._settings_static, self._settings_buttons, self._settings_flash, id="settings-scroll"
            )
            self._settings_scroll.can_focus = False
            self._settings_box = _Box(self._settings_scroll, id="settings-box")
            self._settings_box.can_focus = False
            yield self._settings_box
        self._log_static = Static(id="inst-log-text")
        self._log_static.can_focus = False
        self._log_box = _Box(self._log_static, id="inst-log")
        self._log_box.can_focus = False
        yield self._log_box
        self._scpi = ScpiConsole(self.instrument_name, self._console_submit)
        yield self._scpi

    def on_mount(self) -> None:
        super().on_mount()
        # While the scope runs its frame follows at 200 ms (tui-screens.md 1.6).
        self.set_interval(0.25, self._poll_frame)

    async def _poll_frame(self) -> None:
        app: Any = self.app
        if app.sid is None or app.conn != "connected":
            return
        data = self.frame
        if data is None:
            return
        cols, _rows = self._wave_size()
        points = max(2, cols * 2)
        # A running scope follows at 200 ms; a stopped one is fetched once at this screen's dot
        # width — the frame the app holds from its own fetch is raw and the trace would not draw.
        if not data.get("running") and app.scope_points.get(self.instrument_name) == points:
            return
        await app.refresh_scope_frame(self.instrument_name, points)

    # ---- rendering ------------------------------------------------------------------------------------

    def _wave_size(self) -> tuple[int, int]:
        """The trace area in characters: the wave box's inner width, height fitting (6.1)."""
        app: Any = self.app
        width = app.size.width or 100
        height = app.size.height or 32
        # The box's inner width — a row wider than that wraps and doubles every trace row. Before
        # the first layout: the screen less the settings column (26), its margin (1) and the wave
        # box's border and padding (4).
        cols = max(20, self._wave_static.size.width or width - 31)
        rows = max(6, height - 16)
        return cols, rows

    def refresh_content(self) -> None:
        super().refresh_content()
        app: Any = self.app
        if not self.info:
            return
        if not self._built:
            self._build_buttons()
            self._built = True
            self.call_after_refresh(self._refresh_buttons)
        version = (app.data_version, app.scope_version)
        if version == self._last_version:
            return
        self._last_version = version
        cols, rows = self._wave_size()
        state = {sv["path"]: sv for sv in self.info.get("state", [])}
        text, clipped = render_wave(app, self.frame, cols, rows, state, app.lang, self._open_channels())
        self._wave_static.update(text)
        silences = silence_lines(app, self.frame, state, app.lang)
        self._wave_silence.update(silences)
        self._wave_silence.display = silences.plain != ""
        self._wave_box.border_title = self._wave_title(clipped)
        self._refresh_settings()
        self._refresh_log()
        self._refresh_buttons()

    def _open_channels(self) -> list[int]:
        state = {sv["path"]: sv for sv in self.info.get("state", [])}
        return [ch for ch in range(1, 5) if (state.get(f"ch{ch}.display") or {}).get("value")]

    def _wave_title(self, clipped: list[int]) -> str:
        app: Any = self.app
        lang = app.lang
        state = {sv["path"]: sv for sv in self.info.get("state", [])}
        left = " · ".join(
            f"CH{ch} " + fmt((state.get(f"ch{ch}.scale") or {}).get("value"), "V", digits=3) + "/div"
            for ch in self._open_channels()
        )
        tb = fmt((state.get("timebase.scale") or {}).get("value"), "S", digits=4) + "/div"
        if left:
            left += f" · {tb}"
        else:
            left = tb
        data = self.frame or {}
        if data.get("waiting"):
            run = (i18n.t("cards.waiting", lang), "focus")
        elif data.get("running"):
            run = (i18n.t("cards.running", lang), "ok")
        else:
            run = (i18n.t("cards.stopped", lang), "muted")
        mode = C.summary_text((state.get("trigger.mode") or {}).get("value"), state.get("trigger.mode"), lang)
        right = f"{run[0]} · {mode} · " + i18n.t(
            "scope.acquisitions", lang, count=data.get("acquisitions", 0)
        )
        for ch in clipped:
            left += "  " + i18n.t("scope.outOfScreen", lang, ch=f"CH{ch}")
        # One title row: left, dashes, right (this Textual does not render border_subtitle).
        from ..fmt import cell_len

        width = max(30, self._wave_box.size.width + 2)  # the border row spans size + 2
        gap = max(1, width - cell_len(left) - cell_len(right) - 4)
        return f" {left} " + "─" * gap + f" {right} "

    # -- the settings column (6.2) -------------------------------------------------------------------------

    def _setting_controls(self) -> list[C.Control]:
        """The settings column's controls: timebase, trigger, acquisition, then the channels."""
        info = self.info
        lang = self.app.lang
        state = {sv["path"]: sv for sv in info.get("state", [])}
        paths = [
            "timebase.scale",
            "timebase.delay",
            "trigger.source",
            "trigger.slope",
            "trigger.level",
            "trigger.mode",
            "trigger.coupling",
            "acquire.type",
            "acquire.resolution",
            "acquire.mdepth",
        ]
        out: list[C.Control] = []
        for path in paths:
            sv = state.get(path)
            if sv is not None and not sv.get("readonly"):
                out.append(C._control_for(path, sv, lang))
        for ch in range(1, 5):
            for suffix in ("display", "scale", "coupling", "impedance", "probe"):
                path = f"ch{ch}.{suffix}"
                sv = state.get(path)
                if sv is not None and not sv.get("readonly"):
                    out.append(C._control_for(path, sv, lang))
        return out

    def _build_buttons(self) -> None:
        controls = self._setting_controls()
        self._settings_buttons.mount(
            *[ControlButton(c, "settings") for c in controls if c.writable or c.kind == "buttons"]
        )

    def _refresh_settings(self) -> None:
        app: Any = self.app
        lang = app.lang
        info = self.info
        state = {sv["path"]: sv for sv in info.get("state", [])}
        session = app.session_info or {}
        rows: list[list[tuple[str, str]]] = []

        def row(label: str, text: str, mark: str = "") -> list[tuple[str, str]]:
            return [
                (pad(label, 6), "muted"),
                (text, ""),
                (" " + mark if mark else "", (mark and "bad") or ""),
            ]

        rows.append(
            row(
                i18n.t("scope.timebase", lang),
                fmt((state.get("timebase.scale") or {}).get("value"), "S", digits=4) + "/div",
            )
        )
        rows.append(
            row(i18n.t("scope.delay", lang), fmt((state.get("timebase.delay") or {}).get("value"), "S"))
        )
        src = C.summary_text(
            (state.get("trigger.source") or {}).get("value"), state.get("trigger.source"), lang
        )
        slope = (
            "↑" if str((state.get("trigger.slope") or {}).get("value", "")).lower().startswith("ri") else "↓"
        )
        level = fmt((state.get("trigger.level") or {}).get("value"), "V", digits=4)
        rows.append(row(i18n.t("scope.trigger", lang), f"{src} {slope} {level}"))
        coupling = state.get("trigger.coupling")
        rows.append(
            row(
                "",
                (C.summary_text(coupling.get("value"), coupling, lang) + " · " if coupling else "")
                + C.summary_text(
                    (state.get("trigger.mode") or {}).get("value"), state.get("trigger.mode"), lang
                ),
            )
        )
        rows.append(
            row(
                i18n.t("scope.acquisition", lang),
                C.summary_text(
                    (state.get("acquire.type") or {}).get("value"), state.get("acquire.type"), lang
                )
                + " · "
                + str((state.get("acquire.resolution") or {}).get("value", "")),
            )
        )
        rows.append([("", "")])
        for ch in range(1, 5):
            on = bool((state.get(f"ch{ch}.display") or {}).get("value"))
            port = next((p for p in info.get("ports", []) if p.get("channel") == ch), None)
            wired = port is not None and port.get("net")
            if not on and not wired:
                rows.append([(i18n.t("scope.channelOff", lang, ch=f"CH{ch}"), "muted")])
                continue
            head = f"CH{ch} " + i18n.t("common.on" if on else "common.off", lang)
            style = f"ch{ch}" if app.theme != "simubench-mono" else ""
            rows.append(
                [
                    (head + " ", style),
                    (fmt((state.get(f"ch{ch}.scale") or {}).get("value"), "V", digits=3) + "/div", "bold"),
                ]
            )
            coupling = C.summary_text(
                (state.get(f"ch{ch}.coupling") or {}).get("value"), state.get(f"ch{ch}.coupling"), lang
            )
            impedance = C.summary_text(
                (state.get(f"ch{ch}.impedance") or {}).get("value"), state.get(f"ch{ch}.impedance"), lang
            )
            probe = (state.get(f"ch{ch}.probe") or {}).get("value")
            probe_text = f"{probe:g}" if isinstance(probe, (int, float)) else "?"
            rows.append(
                [
                    ("  ", "muted"),
                    (
                        f"{coupling} · {impedance} · {i18n.t('instrument.probe', lang)} {probe_text}",
                        "",
                    ),
                ]
            )
            if wired:
                ends = opposite_ends(f"{self.instrument_name}.{port['name']}", session, lang)
                arrow = "←"
                rows.append(
                    [
                        (" " + arrow + " ", "muted"),
                        (" · ".join(ends) if ends else (port.get("net") or ""), ""),
                    ]
                )
            else:
                rows.append([("  " + i18n.t("scope.unwired", lang), "muted")])
        if app.theme == "simubench-mono":
            rows.append([("", "")])
            rows.append([(i18n.t("scope.legend", lang, items="CH1 CH2 CH3 CH4"), "muted")])
        width = max(16, self._settings_box.size.width)
        self._settings_static.update(box_text(app, rows, width))
        self._settings_box.border_title = i18n.t("scope.settings", lang)

    # ---- keys ---------------------------------------------------------------------------------------------

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        return [
            ("Esc", i18n.t("keys.back", lang)),
            ("r", i18n.t("keys.runStop", lang)),
            ("s", i18n.t("keys.single", lang)),
            ("c", i18n.t("keys.channels", lang)),
            ("⏎", i18n.t("keys.edit", lang)),
            ("[ ]", i18n.t("keys.nextInstrument", lang)),
            ("w", i18n.t("keys.reason", lang)),
            ("?", i18n.t("keys.help", lang)),
            ("q", i18n.t("common.quit", lang)),
        ]

    def action_next_channel(self) -> None:
        """``c``: the focus channel cycles; the settings view jumps to it (tui-screens.md 6.4)."""
        self._cursor_channel = self._cursor_channel % 4 + 1
        buttons = [b for b in self.query(ControlButton) if b.display]
        target = next((b for b in buttons if b.control.path == f"ch{self._cursor_channel}.display"), None)
        if target is not None:
            target.focus()

    def action_cursor(self, direction: str) -> None:
        buttons = [b for b in self.query(ControlButton) if b.display]
        if not buttons:
            return
        focused = self._focused_button()
        if focused is None:
            buttons[0].focus()
            return
        index = next((i for i, b in enumerate(buttons) if b is focused), 0)
        if direction in ("left", "up"):
            buttons[max(0, index - 1)].focus()
        else:
            buttons[min(len(buttons) - 1, index + 1)].focus()

    # -- writes ----------------------------------------------------------------------------------------------

    def _flash_target(self, button: ControlButton | None) -> Static | None:
        return self._settings_flash

    async def _panel(self, action: str) -> None:
        app: Any = self.app
        try:
            await app.client.panel(app.sid, self.instrument_name, action)
        except Exception as exc:
            self._flash(None, self._error_text(None, str(exc)))

    def action_run_stop(self) -> None:
        app: Any = self.app
        if self._readonly_guard():
            return
        data = self.frame or {}
        app.run_worker(self._panel("stop" if data.get("running") else "run"), exclusive=True)

    def action_single(self) -> None:
        app: Any = self.app
        if self._readonly_guard():
            return
        app.run_worker(self._panel("single"), exclusive=True)

    def _reason_report(self) -> ReasonReport | None:
        """``?`` on the scope screen: the focused channel's silence, else the focused setting."""
        app: Any = self.app
        lang = app.lang
        info = self.info
        if not info:
            return None
        data = self.frame or {}
        button = self._focused_button()
        channel = self._cursor_channel
        if button is not None and button.control.path and button.control.path.startswith("ch"):
            channel = int(button.control.path[2])
        for trace in data.get("traces", []):
            trace_ch = trace.get("channel")
            if trace_ch == channel:
                report = silence_report(self.instrument_name, trace_ch, trace.get("silence") or [], lang)
                if report is not None:
                    return report
        if button is not None and button.control.path:
            return self._setting_report(button.control.path)
        for trace in data.get("traces", []):
            report = silence_report(
                self.instrument_name, trace.get("channel"), trace.get("silence") or [], lang
            )
            if report is not None and (self._state().get(f"ch{trace.get('channel')}.display") or {}).get(
                "value"
            ):
                return report
        return None

    def _state(self) -> dict[str, dict]:
        return {sv["path"]: sv for sv in self.info.get("state", [])}

    def _setting_report(self, path: str) -> Any:
        from ..reasons import setting_report

        return setting_report(self.info, path, self.app.store.events, self.app.lang)
