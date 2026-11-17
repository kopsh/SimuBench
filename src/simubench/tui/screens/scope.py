"""The oscilloscope screen (``Enter`` on a scope card, tui-screens.md 6): braille traces at the
terminal's dot width, a settings column, silence reasons, run/stop/single via POST panel.

The frame is requested with ``points=<2×width>`` so the server returns per-bin min/max instead
of every sample (the /screen contract of this PR); while the scope runs the screen re-reads it
every 200 ms (tui-screens.md 1.6), otherwise only after acquisition/state events.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from .. import controls as C
from .. import i18n
from ..cards import opposite_ends
from ..fmt import fmt
from ..reasons import ReasonReport, silence_report
from ..widgets import box_text, style_of
from .dialogs import ScpiConsole
from .editing import ControlButton, ControlEditingScreen
from .instrument import _Box

SETTINGS_WIDTH = 26  # the right settings column's total width (tui-screens.md 6.1)


def trace_frame(
    trace: dict[str, Any],
    cols: int,
    rows: int,
    connect: bool = False,
    window: tuple[float, float] | None = None,
) -> tuple[list[list[int]], bool]:
    """One trace's dot grid as per-character bit ORs, plus whether it was clipped (6.1).

    Each braille character covers 2 dot columns × 4 dot rows; the server returned per-bin min
    and max (2 values per bin, bins == 2×cols). ``y`` maps volts to divisions by the channel's
    scale and offset; parts beyond the screen draw pinned to the top/bottom row. ``connect``
    joins each column to the previous one: a record with fewer points than dot columns (an FFT
    over 0–10 MHz has some 80 bins) would otherwise draw as scattered dots.

    ``window`` is the time the screen spans, ``(left, width)`` in seconds from the trigger
    (``screen_left_s``, ten divisions): a record is drawn where its own times fall and the rest of
    the screen stays empty (#1333). The bins cover the whole record, so a stretch zoomed in far
    past the bins shows them as blocks. Without a window the record fills the width.
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
    prev: float | None = None
    t0 = float(trace.get("t0_s") or 0.0)
    record_s = float(trace.get("dt_s") or 0.0) * int(trace.get("points") or 0)
    for col in range(cols * 2):
        if window is not None and record_s > 0:
            t = window[0] + (col + 0.5) / (cols * 2) * window[1]
            frac = (t - t0) / record_s
            if not 0.0 <= frac < 1.0:
                prev = None
                continue
            bin_index = min(bins - 1, int(frac * bins))
        else:
            bin_index = min(bins - 1, col * bins // (cols * 2))
        lo = minmax[bin_index * 2]
        hi = minmax[bin_index * 2 + 1]
        if connect:
            mid = (lo + hi) / 2
            if prev is not None:
                lo, hi = min(lo, hi, prev), max(lo, hi, prev)
            prev = mid
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
        [[0] * cols for _ in range(rows)] for _ in range(6)
    ]  # per channel 1-4, the grid, the time-domain math functions
    # The grid: 10 divisions across, 8 down, a dot every 4 points (muted); the centre is not bold.
    dot_rows = rows * 4
    for r in range(0, dot_rows, max(1, dot_rows // 8)):
        for c in range(0, cols * 2, 4):
            grid[4][r // 4][c // 2] |= _BRAILLE[r % 4][c % 2]
    clipped: list[int] = []
    window = None
    if screen and screen.get("screen_left_s") is not None and screen.get("timebase_scale"):
        window = (float(screen["screen_left_s"]), 10 * float(screen["timebase_scale"]))
    traces = {t.get("channel"): t for t in (screen or {}).get("traces", [])}
    for ch in range(1, 5):
        trace = traces.get(ch)
        if trace is None or not (state.get(f"ch{ch}.display") or {}).get("value"):
            continue
        own, was_clipped = trace_frame(trace, cols, rows, window=window)
        if was_clipped:
            clipped.append(ch)
        for r in range(rows):
            for c in range(cols):
                if own[r][c]:
                    grid[ch - 1][r][c] |= own[r][c]
    # Time-domain functions (ADD, DIFF…) in the math colour, over the channels (6.1).
    for func in (screen or {}).get("functions", []):
        if func.get("spectrum"):
            continue
        own, _ = trace_frame(
            {**func, "t0_s": func.get("x0"), "dt_s": func.get("dx")}, cols, rows, connect=True, window=window
        )
        for r in range(rows):
            for c in range(cols):
                grid[5][r][c] |= own[r][c]
    text = Text()
    muted = style_of(app, "muted")
    for r in range(rows):
        for c in range(cols):
            if grid[5][r][c]:
                text.append(chr(0x2800 + grid[5][r][c]), style=None if mono else style_of(app, "math"))
                continue
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


#: A function's unit as the screen writes it (the Web's UNIT_TEXT).
UNIT_TEXT = {"DECibels": "dB", "DB": "dB", "DBM": "dBm", "VOLT": "V", "WATT": "W"}


def spectra_of(screen: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The transforms turned on (FFT magnitude, phase, PSD): drawn in their own pane (6.1)."""
    return [f for f in (screen or {}).get("functions", []) if f.get("spectrum")]


def spectrum_label(spectra: list[dict[str, Any]], width: int, lang: str) -> Text:
    """``F1 FFTMagnitude 10 dB/div`` per transform, then the frequency span and the bin width on
    the right, as the Web's spectrum pane writes them."""
    left = Text()
    for i, f in enumerate(spectra):
        if i:
            left.append("  ")
        unit = UNIT_TEXT.get(f.get("unit", ""), f.get("unit", ""))
        left.append(f"F{f.get('function')}", style="bold")
        left.append(f" {f.get('operator')} ")
        left.append(f"{float(f.get('scale') or 0):.3g} {unit}/div", style="bold")
    first = spectra[0]
    points = len(first.get("values") or []) or len(first.get("minmax") or []) // 2
    x0, dx = float(first.get("x0") or 0.0), float(first.get("dx") or 0.0)
    stop = x0 + dx * max(points - 1, 0)
    span = f"{i18n.t('scope.spectrum.span', lang)} {fmt(x0, 'Hz', digits=4)} – {fmt(stop, 'Hz', digits=4)}"
    from ..fmt import cell_len

    # A row wider than the box wraps (6.1): the bin width goes first, then the span.
    for right in (f"{span} · {i18n.t('scope.spectrum.bin', lang)} {fmt(dx, 'Hz', digits=4)}", span, ""):
        gap = width - cell_len(left.plain) - cell_len(right)
        if gap >= 2 or not right:
            break
    if right:
        left.append(" " * gap)
        left.append(right)
    if cell_len(left.plain) > width:
        left.truncate(width)
    return left


def render_spectrum(app: Any, spectra: list[dict[str, Any]], cols: int, rows: int) -> Text:
    """The spectrum pane: the same 10 × 8 grid, each transform at its own scale and offset."""
    mono = app.theme == "simubench-mono"
    grid = [[0] * cols for _ in range(rows)]
    dots = [[0] * cols for _ in range(rows)]
    dot_rows = rows * 4
    for r in range(0, dot_rows, max(1, dot_rows // 8)):
        for c in range(0, cols * 2, 4):
            dots[r // 4][c // 2] |= _BRAILLE[r % 4][c % 2]
    for func in spectra:
        own, _ = trace_frame(func, cols, rows, connect=True)
        for r in range(rows):
            for c in range(cols):
                grid[r][c] |= own[r][c]
    text = Text()
    muted = style_of(app, "muted")
    math = style_of(app, "math")
    for r in range(rows):
        for c in range(cols):
            if grid[r][c]:
                text.append(chr(0x2800 + grid[r][c]), style=None if mono else math)
            elif dots[r][c]:
                text.append(chr(0x2800 + dots[r][c]), style=muted)
            else:
                text.append("⠀")
        text.append("\n")
    return text


def title_scales(scales: list[tuple[int, str]], group: bool) -> str:
    """``CH1 100 mV/div · CH4 50.0 mV/div`` for the wave box's title; ``group`` joins neighbouring
    channels with the same scale: ``CH1–3 100 mV/div``."""
    runs: list[tuple[int, int, str]] = []
    for ch, scale in scales:
        if group and runs and runs[-1][2] == scale and runs[-1][1] == ch - 1:
            runs[-1] = (runs[-1][0], ch, scale)
        else:
            runs.append((ch, ch, scale))
    return " · ".join((f"CH{a}" if a == b else f"CH{a}–{b}") + f" {scale}/div" for a, b, scale in runs)


def split_rows(rows: int, spectra: bool) -> tuple[int, int]:
    """The trace rows and the spectrum rows (0 without a transform): the spectrum takes about two
    fifths, under one label row (6.1)."""
    if not spectra:
        return rows, 0
    spec = max(3, (rows - 1) * 2 // 5)
    return max(3, rows - 1 - spec), spec


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
        Binding("slash", "search", show=False),
        Binding("left_square_bracket", "switch_instrument(-1)", show=False),
        Binding("right_square_bracket", "switch_instrument(1)", show=False),
        Binding("w", "reason", show=False),  # "why"; ? is the key help everywhere
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.instrument_name = name
        self._last_version: tuple[int, ...] = (-1, -1, -1, -1)
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
            self._wave_hint = Static(id="wave-hint")
            self._wave_hint.can_focus = False
            self._wave_hint.display = False
            self._wave_static = Static(id="wave-text")
            self._wave_static.can_focus = False
            self._spectrum_label = Static(id="spectrum-label")
            self._spectrum_label.can_focus = False
            self._spectrum_static = Static(id="spectrum-text")
            self._spectrum_static.can_focus = False
            self._spectrum_label.display = self._spectrum_static.display = False
            self._wave_box = _Box(
                self._wave_hint,
                self._wave_silence,
                self._wave_static,
                self._spectrum_label,
                self._spectrum_static,
                id="wave-box",
            )
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
        if self._wave_hint.display:  # the clock note above the trace takes its lines (#1334)
            rows = max(6, rows - -(-len(i18n.t("scope.hintClock", app.lang)) // cols))
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
        # The columns are drawn at the width their boxes have: a layout that settles after the last
        # data change (the first layout, a resize) redraws too (#938).
        version = (
            app.data_version,
            app.scope_version,
            self._wave_box.size.width,
            self._settings_box.size.width,
        )
        if version == self._last_version:
            return
        self._last_version = version
        cols, rows = self._wave_size()
        state = {sv["path"]: sv for sv in self.info.get("state", [])}
        spectra = spectra_of(self.frame)
        rows, spec_rows = split_rows(rows, bool(spectra))
        text, clipped = render_wave(app, self.frame, cols, rows, state, app.lang, self._open_channels())
        self._wave_static.update(text)
        # The clock rule: a live frame takes no virtual time (#1334), as the Web screen says.
        hint = bool((self.frame or {}).get("running")) and app.clock_mode() in ("command", "stepped")
        self._wave_hint.display = hint
        if hint:
            self._wave_hint.update(Text(i18n.t("scope.hintClock", app.lang), style=style_of(app, "muted")))
        self._spectrum_label.display = self._spectrum_static.display = bool(spectra)
        if spectra:
            self._spectrum_label.update(spectrum_label(spectra, cols, app.lang))
            self._spectrum_static.update(render_spectrum(app, spectra, cols, spec_rows))
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
        scales = [
            (ch, fmt((state.get(f"ch{ch}.scale") or {}).get("value"), "V", digits=3))
            for ch in self._open_channels()
        ]
        tb = fmt((state.get("timebase.scale") or {}).get("value"), "S", digits=4) + "/div"
        data = self.frame or {}
        if data.get("waiting"):
            run = (i18n.t("cards.waiting", lang), "focus")
        elif data.get("running"):
            run = (i18n.t("cards.running", lang), "ok")
        else:
            run = (i18n.t("cards.stopped", lang), "muted")
        mode = C.summary_text((state.get("trigger.mode") or {}).get("value"), state.get("trigger.mode"), lang)
        full_right = f"{run[0]} · {mode} · " + i18n.t(
            "scope.acquisitions", lang, count=data.get("acquisitions", 0)
        )
        clips = "".join("  " + i18n.t("scope.outOfScreen", lang, ch=f"CH{ch}") for ch in clipped)
        # One title row: left, dashes, right (this Textual does not render border_subtitle).
        from ..fmt import cell_len

        # The border row is the box's outer width; its corners and one ─ and one space on each
        # side are not the title's, and a longer title is cut at its right end (the run state
        # went first).
        width = max(30, self._wave_box.outer_size.width - 6)
        # When it does not fit (four open channels, an 80-column terminal): channels with the same
        # scale share one entry (CH1–3), then the right keeps only the run state, and last the
        # scales go (the settings column has them).
        grouped = title_scales(scales, group=True)
        for channels, right in (
            (title_scales(scales, group=False), full_right),
            (grouped, full_right),
            (grouped, run[0]),
            ("", full_right),
            ("", run[0]),
        ):
            left = (f"{channels} · {tb}" if channels else tb) + clips
            gap = width - cell_len(left) - cell_len(right) - 4
            if gap >= 1:
                break
        return f" {left} " + "─" * max(1, gap) + f" {right} "

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
            first = True
            for suffix in ("display", "scale", "coupling", "impedance", "probe"):
                path = f"ch{ch}.{suffix}"
                sv = state.get(path)
                if sv is not None and not sv.get("readonly"):
                    control = C._control_for(path, sv, lang)
                    if first:  # the channel's first field names it; the rest follow beneath
                        control = replace(control, label=f"CH{ch} {control.label}")
                        first = False
                    out.append(control)
        return out

    def _build_buttons(self) -> None:
        controls = self._setting_controls()
        self._settings_buttons.mount(
            *[ControlButton(c, "settings") for c in controls if c.writable or c.kind == "buttons"]
        )

    def _field_width(self) -> int | None:
        # The settings column is 26 columns; the buttons row inside it loses the border and
        # padding. Before the first layout the box has no size yet: 12 keeps a value readable.
        return max(12, self._settings_box.size.width - 4)

    def _refresh_settings(self) -> None:
        """What the settings column shows beside its field buttons: only what is not a field —
        the channels' wiring and, on the mono theme, the colour legend (issue 680). Every
        writable setting is a ControlButton (timebase, trigger, acquisition, channels); the
        running/stopped state and the trigger mode ride the wave box's title."""
        app: Any = self.app
        lang = app.lang
        info = self.info
        state = {sv["path"]: sv for sv in info.get("state", [])}
        session = app.session_info or {}
        rows: list[list[tuple[str, str]]] = []
        for ch in range(1, 5):
            on = bool((state.get(f"ch{ch}.display") or {}).get("value"))
            port = next((p for p in info.get("ports", []) if p.get("channel") == ch), None)
            wired = port is not None and port.get("net")
            if not on and not wired:
                rows.append([(i18n.t("scope.channelOff", lang, ch=f"CH{ch}"), "muted")])
                continue
            style = f"ch{ch}" if app.theme != "simubench-mono" else ""
            rows.append([(f"CH{ch} ", style), (i18n.t("common.on" if on else "common.off", lang), "")])
            if wired:
                ends = opposite_ends(f"{self.instrument_name}.{port['name']}", session, lang)
                rows.append([(" ← ", "muted"), (" · ".join(ends) if ends else (port.get("net") or ""), "")])
            else:
                rows.append([("  " + i18n.t("scope.unwired", lang), "muted")])
        if app.theme == "simubench-mono":
            rows.append([("", "")])
            rows.append([(i18n.t("scope.legend", lang, items="CH1 CH2 CH3 CH4"), "muted")])
        width = max(16, self._settings_box.size.width)
        self._settings_static.update(box_text(app, rows, width) if rows else Text(""))
        self._settings_box.border_title = i18n.t("scope.settings", lang)

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
