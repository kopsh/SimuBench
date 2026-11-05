"""The instrument screen (``Enter`` on a card, tui-screens.md 5): identity, per-channel boxes,
controls, wiring and load, the error history, the per-instrument log and the SCPI console.

The boxes and their buttons are built once per screen (their structure is stable);
``refresh_content`` rewrites the texts in place, so focus never jumps. All data comes from the
app's session, readings, faults, lines and event store; the screen only performs the writes the
focused control asks for (POST configure / panel / local, always with the tui header).
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
from ..cards import ARROWS, opposite_ends
from ..fmt import braille_rows, cell_len, fmt, fmt_ns, pad, ralign
from ..log import source_label
from ..reasons import ReasonReport, error_report, fault_report, limited_report, setting_report
from ..widgets import box_text, box_title, log_text, render_segments
from .dialogs import NumberEditScreen, ReasonScreen, ScpiConsole, SelectScreen
from .shell import ShellScreen
from .states import ConfirmScreen


def _english(text: Any) -> str | None:
    """``text`` when it is written in Latin script, else None: the TUI shows no CJK (D23)."""
    if not text:
        return None
    t = str(text)
    return t if all(ord(c) < 0x2E80 for c in t) else None


class ControlButton(Static):
    """One ``[label]`` button of a box's button row; focusable, reversed while focused (5.2)."""

    DEFAULT_CSS = """
    ControlButton { width: auto; margin-right: 1; }
    ControlButton:focus { background: $focus; color: $background; text-style: none; }
    ControlButton.-readonly { color: $muted; }
    ControlButton.-lit { text-style: reverse; }
    ControlButton.-highlighted { border: thick $focus; }
    """

    can_focus = True

    def __init__(self, control: C.Control, box_id: str) -> None:
        # A Text, not a bare string: the brackets would parse as console markup and an ASCII
        # label (`[Output]`, `[OFF]`) renders as nothing (5.2; found via #454).
        super().__init__(Text(f"[{control.label}]"))
        self.control = control
        self.box_id = box_id  # where this button's write errors flash (5.3)

    def render_button(self, info: dict[str, Any], readonly: bool) -> None:
        self.set_class(readonly or not self.control.writable, "-readonly")
        self.set_class(C.is_lit(info, self.control), "-lit")


class _Box(Vertical):
    """A round-bordered box; the border carries the title, Statics inside carry the rows."""

    DEFAULT_CSS = """
    _Box { border: round $muted 60%; height: auto; border-title-align: left; padding: 0 1; }
    """


class InstrumentScreen(ShellScreen):
    """仪表 (tui-screens.md 5): identity box, channel boxes, wiring and load, errors, log."""

    screen_key = "instrument"

    # Nothing is focused on entry: the console's input must not swallow ``:`` and the first
    # Tab takes the user to the controls. ``None`` would inherit the app's ``AUTO_FOCUS`` ("*"),
    # which focuses the first control — and ``?`` would then explain that setting instead of
    # the instrument's trouble (5.5); a selector that matches nothing focuses nothing.
    AUTO_FOCUS = "#simubench-entry-no-focus"

    BINDINGS: ClassVar = [
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
        Binding("L", "local", show=False),  # capital L (5.5): lowercase l is the log everywhere
        Binding("question_mark", "reason", show=False),
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.instrument_name = name
        self._last_version = -1
        self._built = False
        self._boxes: list[dict[str, Any]] = []
        self._other_entry: dict[str, Any] | None = None

    DEFAULT_CSS = """
    #remote-hint { color: $muted; }
    #main-row { height: 1fr; }
    #channel-area { width: 1fr; }
    #wiring-box { width: 16; }
    #load-box { width: 19; margin-left: 1; }
    #side-row { height: auto; }
    #other-box { width: 2fr; }
    #error-box { width: 1fr; margin-left: 1; }
    .button-row { height: auto; }
    .flash-line { color: $bad; }
    .reason-line { color: $fg; text-wrap: nowrap; text-overflow: ellipsis; }
    """

    # ---- data ----------------------------------------------------------------------------------------

    @property
    def info(self) -> dict[str, Any]:
        app: Any = self.app
        for i in (app.session_info or {}).get("instruments", []):
            if i.get("name") == self.instrument_name:
                return i
        return {}

    @property
    def readings(self) -> dict[str, Any]:
        app: Any = self.app
        return app.readings.get(self.instrument_name) or {}

    @property
    def remote_controlled(self) -> bool:
        info = self.info
        return bool(info.get("remote")) and not info.get("local")

    def screen_title(self) -> str:
        return self.instrument_name

    # ---- layout --------------------------------------------------------------------------------------

    def body(self) -> ComposeResult:
        self._remote_hint = Static(id="remote-hint")
        self._remote_hint.display = False
        yield self._remote_hint
        self._identity_static = Static(id="identity-text")
        self._identity_static.can_focus = False
        self._identity = _Box(self._identity_static, id="identity")
        self._identity.can_focus = False
        yield self._identity
        with Horizontal(id="main-row"):
            self._channels = VerticalScroll(id="channel-area")
            self._channels.can_focus = False
            yield self._channels
            self._wiring_static = Static(id="wiring-text")
            self._wiring_static.can_focus = False
            self._wiring = _Box(self._wiring_static, id="wiring-box")
            self._wiring.can_focus = False
            yield self._wiring
            self._load_static = Static(id="load-text")
            self._load_static.can_focus = False
            self._load = _Box(self._load_static, id="load-box")
            self._load.can_focus = False
            yield self._load
        with Horizontal(id="side-row"):
            self._other_static = Static(id="other-text")
            self._other_static.can_focus = False
            self._other_buttons = Horizontal(classes="button-row")
            self._other_flash = Static(classes="flash-line")
            self._other_flash.can_focus = False
            self._other_flash.display = False
            self._other = _Box(self._other_static, self._other_buttons, self._other_flash, id="other-box")
            self._other.can_focus = False
            yield self._other
            self._error_static = Static(id="error-text")
            self._error_static.can_focus = False
            self._errors = _Box(self._error_static, id="error-box")
            self._errors.can_focus = False
            yield self._errors
        self._log_static = Static(id="inst-log-text")
        self._log_static.can_focus = False
        self._log_box = _Box(self._log_static, id="inst-log")
        self._log_box.can_focus = False
        yield self._log_box
        self._scpi = ScpiConsole(self.instrument_name, self._console_submit)
        yield self._scpi

    # ---- rendering ------------------------------------------------------------------------------------

    def refresh_content(self) -> None:
        super().refresh_content()
        app: Any = self.app
        if not self.info:
            self._identity_static.update(i18n.t("connect.screen", app.lang))
            return
        if not self._built:
            self._build_boxes()
            self._built = True
        self._refresh_remote_hint()
        if app.data_version == self._last_version:
            return
        self._last_version = app.data_version
        self._refresh_identity()
        self._refresh_channels()
        self._refresh_other()
        self._refresh_errors()
        self._refresh_wiring()
        self._refresh_log()
        self._refresh_buttons()

    # -- identity (5.1 #1) --------------------------------------------------------------------------------

    def _refresh_remote_hint(self) -> None:
        app: Any = self.app
        if self.remote_controlled:
            client = (self.info.get("remote") or [""])[0]
            source = source_label({"client": client, "kind": "scpi"}, app.lang)
            self._remote_hint.update(
                " "
                + i18n.t("instrument.readonlyHint", app.lang, instrument=self.instrument_name, source=source)
            )
            self._remote_hint.display = True
        else:
            self._remote_hint.display = False

    def _refresh_identity(self) -> None:
        app: Any = self.app
        lang = app.lang
        info = self.info
        width = max(40, app.size.width - 4)
        # Label columns as wide as their longest label, one space after it.
        lw = 1 + max(cell_len(i18n.t(k, lang)) for k in ("instrument.id", "instrument.address"))
        rw = 1 + max(cell_len(i18n.t(k, lang)) for k in ("instrument.definition", "instrument.real"))
        rows: list[list[tuple[str, str]]] = [
            [
                (pad(i18n.t("instrument.id", lang), lw), "muted"),
                (info.get("idn", ""), ""),
            ]
        ]
        addr_row: list[tuple[str, str]] = [
            (pad(i18n.t("instrument.address", lang), lw), "muted"),
            (info.get("address_virtual", ""), ""),
        ]
        if info.get("address_hislip"):
            addr_row.append(("   " + i18n.t("instrument.hiSLIP", lang) + " ", "muted"))
            addr_row.append((info["address_hislip"], ""))
        if info.get("address_vxi11"):
            addr_row.append(("   " + i18n.t("instrument.vxi11", lang) + " ", "muted"))
            addr_row.append((info["address_vxi11"], ""))
        rows.append(addr_row)
        right: list[list[tuple[str, str]]] = []
        right.append(
            [
                (pad(i18n.t("instrument.definition", lang), rw), "muted"),
                (info.get("definition_id", ""), ""),
            ]
        )
        errors = info.get("errors", [])
        if errors:
            last = errors[-1]
            text = i18n.t(
                "instrument.lastError", lang, count=len(errors), code=last["code"], message=last["message"]
            )
            head, _, tail = text.partition(str(last["code"]))
            right.append([(head, ""), (str(last["code"]), "bad"), (tail, "")])
        else:
            right.append([(i18n.t("instrument.errors", lang) + "  ", "muted"), ("0", "")])
        if info.get("address_real"):
            right.append([(pad(i18n.t("instrument.real", lang), rw), "muted"), (info["address_real"], "")])
        for fault in self._active_faults():
            right.append([(i18n.t("log.faultActive", lang, id=fault.get("id", "")), "bad")])
        half = width // 2
        merged: list[list[tuple[str, str]]] = []
        for n in range(max(len(rows), len(right))):
            row = list(rows[n]) if n < len(rows) else []
            used = sum(cell_len(v) for v, _ in row)
            gap = max(1, half - used)
            row.append((" " * gap, ""))
            if n < len(right):
                row.extend(right[n])
            merged.append(row)
        self._identity.border_title = self._identity_title()
        self._identity_static.update(box_text(app, merged, width))
        self._identity_rows = len(merged)  # the channel row budget takes the boxes' heights (#374)

    def _identity_title(self) -> str:
        app: Any = self.app
        info = self.info
        title = f"{info.get('name', '')} · {info.get('model', '')}"
        label = _english(info.get("label"))
        if label:
            title += f" · {label}"
        if self.remote_controlled:
            title += " " + i18n.t("instrument.remoteBadge", app.lang)
        return title

    # -- channels (5.1 #2, 5.2) ---------------------------------------------------------------------------

    def _template(self) -> str:
        return (self.info.get("display") or {}).get("template") or ""

    def _channels_of(self) -> list[int]:
        return sorted({sv.get("channel") for sv in self.info.get("state", []) if sv.get("channel")})

    def _build_boxes(self) -> None:
        """One box per channel (a single one without channels), its rows, reason line, buttons."""
        template = self._template()
        channels = self._channels_of()
        if template == "function_generator" and channels:
            boxes = [(f"ch{ch}", ch) for ch in channels]
        else:
            boxes = [("main", None)]
        for key, channel in boxes:
            rows = Static(classes="box-rows")
            rows.can_focus = False
            reason = Static(classes="reason-line")
            reason.can_focus = False
            reason.display = False
            flash = Static(classes="flash-line")
            flash.can_focus = False
            flash.display = False
            controls = self._controls_for(channel)
            buttons = Horizontal(
                *[ControlButton(c, f"ch:{key}") for c in controls if c.writable or c.kind == "buttons"],
                classes="button-row",
            )
            box = _Box(rows, reason, buttons, flash, classes="channel-box")
            box.can_focus = False
            self._channels.mount(box)
            entry: dict[str, Any] = {
                "box": box,
                "rows": rows,
                "reason": reason,
                "buttons": buttons,
                "flash": flash,
                "channel": channel,
                "key": key,
                "controls": controls,
            }
            self._boxes.append(entry)
        # The buttons are still pending children on this first refresh; style them once mounted.
        self.call_after_refresh(self._refresh_buttons)

    def _controls_for(self, channel: int | None) -> list[C.Control]:
        info = self.info
        template = self._template()
        lang = self.app.lang
        if template == "smu":
            return C.smu_controls(info, lang)
        if template == "function_generator":
            return C.generator_controls(info, lang, channel)
        panel = (info.get("display") or {}).get("panel")
        if panel:
            controls: list[C.Control] = []
            for group in panel.get("groups", []):
                controls.extend(C.panel_controls(group, info, lang))
            controls.extend(C.softkey_controls(panel, lang))
            return controls
        return C.generic_controls(info, lang)

    def _channel_width(self) -> int:
        """The channel boxes' inner width: what the main row leaves for them (6.1's share)."""
        area = self._channels.container_size.width or (self.app.size.width - 38)
        return max(30, area)

    def _channel_row_budget(self) -> int:
        """Text rows a channel box may show before the boxes around it clip it (#374): the
        screen's height minus the header and status bars, the identity, errors and log boxes,
        and the channel box's own borders, button row and reason and flash lines. From the data
        alone, so the render does not depend on when the layout happens."""
        h = self.app.size.height - 2  # the header and the status bar
        if self._remote_hint.display:
            h -= 1
        identity = getattr(self, "_identity_rows", 2) + 2
        side = getattr(self, "_errors_rows", 1) + 2  # the errors box shares its row with the
        # other-settings box; the taller of the two sets the row's height
        if self._other.display:
            side = max(side, getattr(self, "_other_rows", 0) + 2)
        chrome = 2  # the channel box's borders
        for entry in self._boxes:
            chrome = max(
                chrome,
                2
                + (1 if entry["buttons"].children else 0)
                + (1 if entry["reason"].display else 0)
                + (1 if entry["flash"].display else 0),
            )
        return max(4, h - identity - side - (self._log_rows() + 2) - chrome)

    def _refresh_channels(self) -> None:
        app: Any = self.app
        template = self._template()
        width = self._channel_width()
        budget = self._channel_row_budget()
        for entry in self._boxes:
            box = entry["box"]
            lines = self._channel_lines(template, entry["channel"], width, budget)
            entry["rows"].update(box_text(app, lines, width))
            box.border_title = self._box_title(template, entry["channel"])
            reason = self._reason_line(template)
            if reason:
                entry["reason"].update(render_segments(app, reason))
                entry["reason"].display = True
            else:
                entry["reason"].display = False
        self._other.display = self._other_paths() is not None

    def _box_title(self, template: str, channel: int | None) -> str:
        lang = self.app.lang
        if template == "smu":
            return i18n.t("instrument.smuBox", lang)
        if channel is not None:
            return f"CH{channel}"
        info = self.info
        # The TUI is English only (D23): a bench label in another script gives way to the model.
        return _english(info.get("label")) or info.get("model", "")

    def _channel_lines(
        self, template: str, channel: int | None, width: int, budget: int | None = None
    ) -> list[list[tuple[str, str]]]:
        info = self.info
        state = {sv["path"]: sv for sv in info.get("state", [])}
        if template == "smu":
            return self._smu_lines(state)
        if template == "function_generator":
            return self._generator_lines(state, channel or 1)
        if template == "spectrum_analyzer":
            return self._spectrum_lines(state, width)
        if template == "lockin":
            return self._lockin_lines(state, width)
        if template == "rf_generator":
            return self._rf_lines(state, width, budget)
        if template == "electronic_load":
            return self._load_lines(state, width)
        if (info.get("display") or {}).get("panel"):
            return self._panel_lines(width, budget)
        return self._readings_lines(width) + self._generic_lines(state, width)

    def _row(
        self, label: str, value: str, *, bold: bool = True, mark: str = "", width: int = 6
    ) -> list[tuple[str, str]]:
        return [
            (pad(label, max(width, cell_len(label) + 1)), "muted"),
            (value, "bold" if bold else ""),
            (" " + mark if mark else "", "bad"),
        ]

    def _smu_lines(self, state: dict[str, dict]) -> list[list[tuple[str, str]]]:
        """状态 / 电压 / 电流 columns, 设定 next to 限值; ▲ on the limited column (5.2)."""
        lang = self.app.lang
        readings = self.readings.get("values", {})
        limited = bool(readings.get("core.limited"))
        regulation = str(readings.get("core.regulation") or "")
        voltage_limited = limited and regulation == "CV"
        current_limited = limited and regulation == "CC"
        label_w = 1 + max(
            cell_len(i18n.t(k, lang)) for k in ("instrument.set", "instrument.actual", "instrument.limit")
        )
        value_w = 10
        status_w = 1 + max(
            cell_len(i18n.t(k, lang))
            for k in ("cards.output", "instrument.function", "instrument.regulation")
        )
        status_col = status_w + 8  # On / Off, VOLTage / CURRent, CV / CC

        def sv_text(path: str) -> str:
            sv = state.get(path) or {}
            return C.summary_text(sv.get("value"), sv, lang)

        def cell(label: str, value: str, mark: str = "") -> list[tuple[str, str]]:
            bold = value not in (i18n.t("common.on", lang), i18n.t("common.off", lang))
            return [
                (pad(label, label_w), "muted"),
                (ralign(value, value_w), "bold" if bold else ""),
                (" " + mark if mark else "", "bad" if mark else ""),
            ]

        col = label_w + value_w + 2
        header = [
            (pad(i18n.t("instrument.colStatus", lang), status_col + 2), "muted"),
            (pad(i18n.t("instrument.colVoltage", lang), col), "muted"),
            (pad(i18n.t("instrument.colCurrent", lang), col), "muted"),
        ]

        def status(label: str, value: str, tokens: str = "") -> list[tuple[str, str]]:
            return [(pad(label, status_w), "muted"), (pad(value, max(1, status_col - status_w)), tokens)]

        status_rows = [
            status(i18n.t("cards.output", lang), sv_text("output.enabled")),
            status(i18n.t("instrument.function", lang), sv_text("source.function")),
        ]
        regulation_row = status(
            i18n.t("instrument.regulation", lang), regulation or "—", "bad" if limited else ""
        )
        gap = [(" " * 2, "")]
        v1 = cell(i18n.t("instrument.set", lang), sv_text("source.voltage.level"))
        c1 = cell(i18n.t("instrument.set", lang), sv_text("source.current.level"))
        v2 = cell(i18n.t("instrument.actual", lang), fmt(readings.get("core.voltage"), "V"))
        c2 = cell(i18n.t("instrument.actual", lang), fmt(readings.get("core.current"), "A"))
        v3 = cell(
            i18n.t("instrument.limit", lang),
            sv_text("compliance.voltage.limit"),
            "▲" if voltage_limited else "",
        )
        c3 = cell(
            i18n.t("instrument.limit", lang),
            sv_text("compliance.current.limit"),
            "▲" if current_limited else "",
        )
        return [
            header,
            status_rows[0] + gap + v1 + gap + c1,
            status_rows[1] + gap + v2 + gap + c2,
            regulation_row + gap + v3 + gap + c3,
        ]

    def _generator_lines(self, state: dict[str, dict], channel: int) -> list[list[tuple[str, str]]]:
        lang = self.app.lang
        rows: list[tuple[str, str, bool]] = []
        for path in (
            f"ch{channel}.output",
            f"ch{channel}.function",
            f"ch{channel}.frequency",
            f"ch{channel}.amplitude",
            f"ch{channel}.offset",
            f"ch{channel}.phase",
            f"ch{channel}.load",
            f"ch{channel}.inverted",
        ):
            sv = state.get(path)
            if sv is None:
                continue
            label = C.label_of(sv, lang, fallback=path.rsplit(".", 1)[-1])
            value = C.summary_text(sv.get("value"), sv, lang)
            rows.append((label, value, isinstance(sv.get("value"), (int, float))))
        width = 1 + max((cell_len(label) for label, _, _ in rows), default=5)
        return [self._row(label, value, bold=bold, width=width) for label, value, bold in rows]

    def _panel_lines(
        self, width: int | None = None, budget: int | None = None
    ) -> list[list[tuple[str, str]]]:
        """Readouts on top (numbers bold), then one column per panel group, as many as fit
        (tui-screens.md 5.2). A group block the ``budget`` has no room for is left out whole
        (#374, #454): a group title above the fold misleads."""
        lang = self.app.lang
        info = self.info
        panel = info["display"]["panel"]
        state = {sv["path"]: sv for sv in info.get("state", [])}
        values = self.readings.get("values", {})
        rows: list[list[tuple[str, str]]] = []
        shown = []
        for ro in panel.get("readouts", []):
            when = ro.get("when") or {}
            if when and not all((state.get(p) or {}).get("value") == v for p, v in when.items()):
                continue
            shown.append(ro)
        label_w = max((cell_len(ro.get("label_en") or ro.get("label") or "") for ro in shown), default=0) + 1
        for ro in shown:
            label = ro.get("label_en") or ro.get("label") or ro.get("reading", "")
            value = values.get(ro.get("reading", ""))
            if isinstance(value, int) and not isinstance(value, bool) and not ro.get("unit"):
                rows.append([(pad(label, label_w), "muted"), (str(value), "bold")])  # a count (#355)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                text = fmt(value, ro.get("unit"), ro.get("digits") or 5)
                rows.append([(pad(label, label_w), "muted"), (text, "bold")])
            else:
                rows.append([(pad(label, label_w), "muted"), (C.summary_text(value, None, lang), "")])
        if rows:
            rows.append([("", "")])
        groups = {
            (group.get("label_en") or group.get("label") or ""): [
                pc["state"] for pc in group.get("controls", []) if pc.get("state")
            ]
            for group in panel.get("groups", [])
        }
        group_budget = budget - len(rows) if budget is not None else None
        return rows + self._grouped_columns(groups, state, width, budget=group_budget)

    def _readings_lines(self, width: int | None = None) -> list[list[tuple[str, str]]]:
        """The numeric readings of an instrument without a template view (an electronic load, an RF
        generator): ``component.name value`` (#282)."""
        values = self.readings.get("values", {})
        items = [(k, v) for k, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not items:
            return []
        label_w = max(cell_len(k) for k, _ in items) + 1
        rows = [[(pad(k, label_w), "muted"), (fmt(float(v), None, 5), "bold")] for k, v in items[:8]]
        return [*rows, [("", "")]]

    def _spectrum_lines(self, state: dict[str, dict], width: int) -> list[list[tuple[str, str]]]:
        """The analyzer's trace as braille (4 rows of dots per text row), the marker, RBW/VBW and
        sweep time, then its settings (#282) — the same data the web screen draws."""
        readings = self.readings
        values = readings.get("values", {})
        traces = readings.get("traces") or []
        cols = max(10, width - 4)
        rows: list[list[tuple[str, str]]] = []
        trace = next((t for t in traces if t.get("name") == "trace1"), traces[0] if traces else None)
        if trace and trace.get("values"):
            wave = [float(v) for v in trace["values"]]
            for line in braille_rows(wave, cols, 4):
                rows.append([(line, "ch1")])
            lo, hi = min(wave), max(wave)
            rows.append([(f"{fmt(hi, 'dBm', 4)} top · {fmt(lo, 'dBm', 4)} floor", "muted")])
        key = next((k for k in values if k.endswith(".marker_x")), None)
        if key:
            base = key.rsplit(".", 1)[0]
            mx, my = values.get(f"{base}.marker_x"), values.get(f"{base}.marker_y")
            rows.append(
                [
                    ("Marker ", "muted"),
                    (fmt(float(mx), "Hz", 6), "bold"),
                    ("  ", ""),
                    (fmt(float(my), "dBm", 4), "bold"),
                ]
            )
            line: list[tuple[str, str]] = []
            for name, label, unit in (
                ("rbw", "RBW", "Hz"),
                ("vbw", "VBW", "Hz"),
                ("sweep_time", "Sweep", "s"),
            ):
                v = values.get(f"{base}.{name}")
                if isinstance(v, (int, float)):
                    line += [(f"{label} ", "muted"), (fmt(float(v), unit, 3), ""), ("   ", "")]
            if values.get(f"{base}.uncal"):
                line.append(("UNCAL", "bad"))
            if line:
                rows.append(line)
        rows.append([("", "")])
        return rows + self._generic_lines(state, width)

    def _value(self, name: str) -> Any:
        """A reading by its name, whatever component gives it (``synth.frequency`` -> frequency)."""
        values = self.readings.get("values", {})
        if name in values:
            return values[name]
        return next((v for k, v in values.items() if k.rsplit(".", 1)[-1] == name), None)

    @staticmethod
    def _flag(label: str, on: bool, tokens: str = "ok") -> list[tuple[str, str]]:
        return [(label, tokens if on else "muted"), ("  ", "")]

    def _rf_lines(
        self, state: dict[str, dict], width: int, budget: int | None = None
    ) -> list[list[tuple[str, str]]]:
        """The RF source as the web screen shows it (#355): frequency and level, RF and modulation
        flags, the reference, the sweep point and the output's spectral lines — then its settings
        in three groups. Not the synthesizer's internal quantities. A group block the ``budget``
        has no room for is left out whole (#374): a title alone above the fold misleads."""
        on = lambda path: (state.get(path) or {}).get("value") is True  # noqa: E731
        freq = self._value("frequency")
        level = (state.get("power.level") or {}).get("value")
        rows: list[list[tuple[str, str]]] = []
        if isinstance(freq, (int, float)):
            rows.append([(pad("Frequency", 10), "muted"), (fmt(float(freq), "Hz", 10), "bold")])
        if isinstance(level, (int, float)):
            rows.append([(pad("Level", 10), "muted"), (fmt(float(level), "dBm", 4), "bold")])
        external = (state.get("rosc.source") or {}).get("value") == "EXTernal"
        locked = self._value("locked") is not False
        flags = self._flag("RF ON" if on("output.enabled") else "RF OFF", on("output.enabled"))
        for mod in ("AM", "FM", "PM"):
            flags += self._flag(mod, on(f"{mod.lower()}.state"))
        reference = ("EXT locked" if locked else "EXT missing") if external else "INT ref"
        flags += [(reference, ("ok" if locked else "bad") if external else "muted")]
        rows.append(flags)
        detail: list[tuple[str, str]] = []
        mode = str((state.get("freq.mode") or {}).get("value") or "CW")
        if mode.upper() not in ("CW", "FIX", "FIXED"):
            point, points = self._value("point"), self._value("points")
            detail += [("Point ", "muted"), (f"{int(point or 0)}/{int(points or 0)}", ""), ("   ", "")]
        lines = self._value("lines")
        if isinstance(lines, (int, float)):
            detail += [("Lines ", "muted"), (str(int(lines)), ""), ("   ", "")]
        beta = self._value("beta")
        if isinstance(beta, (int, float)) and beta > 0:
            omitted = float(self._value("omitted_power") or 0.0) * 100
            detail += [
                ("β ", "muted"),
                (f"{beta:.4g}", ""),
                ("   ", ""),
                ("omitted ", "muted"),
                (f"{omitted:.3f} %", ""),
            ]
        if detail:
            rows.append(detail)
        rows.append([("", "")])
        groups = {
            "Carrier": ["freq.cw", "power.level", "output.enabled"],
            "Modulation": ["am.state", "am.depth", "fm.state", "fm.dev", "pm.state", "pm.dev"],
            "Sweep": ["freq.mode", "sweep.start", "sweep.stop", "sweep.points", "sweep.dwell"],
        }
        group_budget = budget - len(rows) if budget is not None else None
        return rows + self._grouped_columns(groups, state, width, budget=group_budget)

    def _load_lines(self, state: dict[str, dict], width: int) -> list[list[tuple[str, str]]]:
        """The electronic load as the web screen shows it (#355): measured V, I and P, the input,
        the mode and whether it holds its setting — then its settings."""
        rows: list[list[tuple[str, str]]] = []
        for name, label, unit, digits in (
            ("voltage", "V", "V", 6),
            ("current", "I", "A", 6),
            ("power", "P", "W", 4),
        ):
            v = self._value(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rows.append([(pad(label, 3), "muted"), (fmt(float(v), unit, digits), "bold")])
        enabled = (state.get("input.enabled") or {}).get("value") is True
        mode = (state.get("function.mode") or {}).get("value")
        regulating = self._value("regulating") is not False
        flags = self._flag("INPUT ON" if enabled else "INPUT OFF", enabled)
        if mode is not None:
            # The mode as the front panel names it: CC, CV, CR, CP.
            short = {"CURR": "CC", "VOLT": "CV", "RES": "CR", "POW": "CP"}
            word = str(mode).upper()
            flags += [
                (next((v for k, v in short.items() if word.startswith(k)), str(mode)), "bold"),
                ("  ", ""),
            ]
        if enabled and not regulating:
            flags += [("UNREG", "bad")]
        rows += [flags, [("", "")]]
        settings = [
            p
            for p in (
                "function.mode",
                "current.level",
                "voltage.level",
                "resistance.level",
                "power.level",
                "source.current.level",
                "source.voltage.level",
                "source.resistance.level",
                "source.power.level",
                "input.enabled",
            )
            if p in state
        ]
        return rows + self._grouped_columns({"Settings": settings}, state, width)

    def _lockin_lines(self, state: dict[str, dict], width: int) -> list[list[tuple[str, str]]]:
        """X, Y, R and θ of the lock-in's output (#282), then its settings."""
        values = self.readings.get("values", {})
        rows: list[list[tuple[str, str]]] = []
        for name, unit in (("X", "V"), ("Y", "V"), ("R", "V"), ("THETA", "DEG")):
            key = next((k for k in values if k.rsplit(".", 1)[-1] == name), None)
            if key is None:
                continue
            label = "θ" if name == "THETA" else name
            rows.append([(pad(label, 3), "muted"), (fmt(float(values[key]), unit, 5), "bold")])
        if rows:
            rows.append([("", "")])
        return rows + self._generic_lines(state, width)

    def _generic_lines(self, state: dict[str, dict], width: int | None = None) -> list[list[tuple[str, str]]]:
        """State variables grouped by first path segment, as many columns as fit (5.2)."""
        groups: dict[str, list[str]] = {}
        for path in state:
            groups.setdefault(path.split(".", 1)[0], []).append(path)
        return self._grouped_columns(groups, state, width)

    def _grouped_columns(
        self,
        groups: dict[str, list[str]],
        state: dict[str, dict],
        width: int | None = None,
        budget: int | None = None,
    ) -> list[list[tuple[str, str]]]:
        """``label value`` rows under their group name, one column per group. Each column is as
        wide as its widest row (labels and values aligned, #283); as many columns go side by side
        as ``width`` holds, and the next ones form a block below — nothing wraps or is cut (#282).
        ``budget`` is the rows this call may emit: a block it has no room for is left out whole
        with the blocks after it (#374) — a group title above the fold misleads."""
        lang = self.app.lang
        columns: list[list[tuple[str, str]]] = []  # per column: (label, value) rows; row 0 = title
        for name, paths in list(groups.items())[:3]:  # up to three groups, as before (5.2)
            col: list[tuple[str, str]] = [(name, "")]
            for path in paths:
                sv = state.get(path)
                if sv is None:
                    continue
                label = C.label_of(sv, lang, fallback=path.rsplit(".", 1)[-1])
                value = (
                    "AUTO"
                    if C.inactive_under_auto(state, path)
                    else C.summary_text(sv.get("value"), sv, lang)
                )
                col.append((label, value))
            if len(col) > 1:
                columns.append(col)
        label_w = [max((cell_len(lab) for lab, _ in col[1:]), default=0) for col in columns]
        col_w = [
            max(cell_len(col[0][0]), max((label_w[k] + 1 + cell_len(v) for _, v in col[1:]), default=0))
            for k, col in enumerate(columns)
        ]
        limit = width - 2 if width and width > 2 else 10_000  # the box's padding
        blocks: list[list[int]] = []
        for k in range(len(columns)):
            if blocks and sum(col_w[j] + 3 for j in blocks[-1]) + col_w[k] <= limit:
                blocks[-1].append(k)
            else:
                blocks.append([k])
        rows: list[list[tuple[str, str]]] = []
        emitted = 0
        for b, block in enumerate(blocks):
            height = max((len(columns[k]) for k in block), default=0)
            if budget is not None and emitted + (1 if b else 0) + height > budget:
                break
            if b:
                rows.append([("", "")])
                emitted += 1
            for n in range(height):
                row: list[tuple[str, str]] = []
                for k in block:
                    col = columns[k]
                    if n == 0:
                        row.append((pad(col[0][0], col_w[k]), "muted"))
                    elif n < len(col):
                        label, value = col[n]
                        row.append((pad(label, label_w[k] + 1), "muted"))
                        row.append((pad(value, col_w[k] - label_w[k] - 1), "bold"))
                    else:
                        row.append((" " * col_w[k], ""))
                    row.append((" " * 3, ""))
                if any(cell_len(v.strip()) for v, _ in row):
                    rows.append(row)
                    emitted += 1
        return rows

    def _reason_line(self, template: str) -> list[tuple[str, str]] | None:
        """The 原因 line of a troubled channel box, from existing data only (tui-screens.md 5.4)."""
        lang = self.app.lang
        readings = self.readings.get("values", {})
        info = self.info
        if template == "smu" and readings.get("core.limited"):
            regulation = str(readings.get("core.regulation") or "CC")
            set_path = "source.current.level" if regulation == "CV" else "source.voltage.level"
            state = {sv["path"]: sv for sv in info.get("state", [])}
            setting = C.summary_text((state.get(set_path) or {}).get("value"), state.get(set_path), lang)
            if regulation == "CV":
                text = i18n.t(
                    "reason.limitedCV",
                    lang,
                    limit=fmt(readings.get("core.limit"), "V"),
                    actual=fmt(readings.get("core.current"), "A"),
                    setting=setting,
                )
            else:
                text = i18n.t(
                    "reason.limitedCC",
                    lang,
                    limit=fmt(readings.get("core.limit"), "A"),
                    actual=fmt(readings.get("core.voltage"), "V"),
                    setting=setting,
                )
            return [(i18n.t("reason.title", lang), "focus"), ("  " + text, "")]
        for fault in self._active_faults():
            spec = fault.get("spec") or {}
            source = source_label({"kind": "fault", "source": fault.get("source", "")}, lang)
            if spec.get("kind") == "trip":
                text = i18n.t(
                    "reason.trip",
                    lang,
                    id=fault.get("id", ""),
                    source=source,
                    description=fault.get("description", ""),
                )
                return [(i18n.t("reason.title", lang), "focus"), ("  " + text, "bad")]
            if spec.get("kind") in ("timeout", "disconnect", "garble", "latency", "busy"):
                text = i18n.t(
                    "reason.commFault",
                    lang,
                    id=fault.get("id", ""),
                    source=source,
                    description=fault.get("description", ""),
                )
                return [(i18n.t("reason.title", lang), "focus"), ("  " + text, "")]
        return None

    # -- other settings (5.1 #3) ---------------------------------------------------------------------------

    def _other_paths(self) -> list[str] | None:
        """The state paths the template did not put into the channel area (5.1 #3)."""
        template = self._template()
        info = self.info
        if template == "smu":
            main = {
                "output.enabled",
                "source.function",
                "source.voltage.level",
                "source.current.level",
                "compliance.current.limit",
                "compliance.voltage.limit",
            }
            paths = [sv["path"] for sv in info.get("state", []) if sv["path"] not in main]
            return paths or None
        return None

    def _refresh_other(self) -> None:
        app: Any = self.app
        lang = app.lang
        paths = self._other_paths()
        if not paths:
            self._other.display = False
            return
        info = self.info
        state = {sv["path"]: sv for sv in info.get("state", [])}
        groups: dict[str, list[str]] = {}
        for path in paths:
            groups.setdefault(path.split(".", 1)[0], []).append(path)
        # Before the first layout the box has no size yet: take the row's share of the screen
        # (#other-box is 2fr of the side row) rather than cutting at a 20-cell guess (#283).
        width = self._other.content_size.width or max(20, (app.size.width * 2) // 3 - 4)
        rows = self._grouped_columns(groups, state, width)
        self._other.display = True
        self._other.border_title = i18n.t("instrument.otherSettings", lang)
        self._other_static.update(box_text(app, rows, width))
        if not self._other_buttons.children:
            controls = [
                C._control_for(path, state[path], lang)
                for path in paths
                if path in state
                and not state[path].get("readonly")
                and state[path].get("type") in ("bool", "enum", "float", "int")
            ]
            self._other_buttons.mount(*[ControlButton(c, "other") for c in controls])
        self._other_rows = len(rows) + (1 if self._other_buttons.children else 0)

    # -- wiring and load (5.1 #2) ---------------------------------------------------------------------------

    def _focused_channel(self) -> int | None:
        focused = self.focused
        if isinstance(focused, ControlButton):
            for entry in self._boxes:
                for child in entry["buttons"].children:
                    if child is focused:
                        return entry["channel"]
        return None

    def _active_ports(self) -> list[dict[str, Any]]:
        """The ports of the focused channel box; all ports when the instrument has no channels."""
        info = self.info
        channel = self._focused_channel()
        ports = [p for p in info.get("ports", []) if p.get("channel") == channel]
        if not ports:
            ports = [p for p in info.get("ports", []) if p.get("channel") is None] or list(
                info.get("ports", [])
            )
        return ports

    def _refresh_wiring(self) -> None:
        app: Any = self.app
        lang = app.lang
        info = self.info
        session = app.session_info or {}
        ports = self._active_ports()
        width = 14
        lines: list[list[tuple[str, str]]] = []
        for p in ports:
            arrow = ARROWS.get(p.get("direction", ""), "—")
            net = p.get("net")
            text = f"{p['name']} {arrow} {net}" if net else f"{p['name']} —"
            lines.append([(text, "" if net else "muted")])
        self._wiring.border_title = i18n.t("instrument.wiring", lang)
        self._wiring_static.update(box_text(app, lines, width))
        # The load box: the net's other members, then the net id and its level (5.1).
        load_lines: list[list[tuple[str, str]]] = []
        net = next((p.get("net") for p in ports if p.get("net")), None)
        members = next((n["members"] for n in session.get("nets", []) if n["id"] == net), []) if net else []
        own = {f"{info.get('name', '')}.{p['name']}" for p in info.get("ports", [])}
        for m in members:
            if m in own or m.startswith("wire"):
                continue
            inst, _, _port = m.rpartition(".")
            part = next((pt for pt in session.get("parts", []) if pt["name"] == inst), None)
            if part is not None:
                head = part["name"] + (" · DUT" if part.get("dut") else "")
                load_lines.append([(head, "")])
                for param, value in list((part.get("params") or {}).items())[:1]:
                    unit = self._param_unit(part, param)
                    load_lines.append([(fmt(value, unit), "")])
            else:
                load_lines.append([(m, "")])
        if net:
            load_lines.append([("", "")])
            load_lines.append([(net, "muted")])
            level = self._net_level(net)
            if level:
                load_lines.append([(level, "")])
        self._load.border_title = i18n.t("instrument.load", lang)
        self._load_static.update(box_text(app, load_lines, width))

    def _param_unit(self, part: dict[str, Any], param: str) -> str | None:
        app: Any = self.app
        doc = (app.catalog or {}).get(part.get("type", "")) or {}
        for p in doc.get("params", []):
            if p.get("name") == param:
                return p.get("unit")
        return None

    def _net_level(self, net: str) -> str:
        app: Any = self.app
        level = next((n for n in (app.levels or {}).get("nets", []) if n["net"] == net), None)
        if level is None:
            return ""
        dc, rms = level.get("dc", 0.0), level.get("rms", 0.0)
        value = dc if (rms == 0 and dc != 0) else rms
        return fmt(value, "V")

    # -- errors (5.1 #4) -------------------------------------------------------------------------------------

    def _refresh_errors(self) -> None:
        app: Any = self.app
        lang = app.lang
        info = self.info
        if not info:
            return
        queue = info.get("errors", [])
        width = max(20, self._errors.size.width)  # .size already excludes border and padding
        lines: list[list[tuple[str, str]]] = []
        for e in queue:
            lines.append([(str(e["code"]), "bad"), ("  " + e["message"], "bad")])
        read_away = self._read_away(queue)
        if read_away:
            lines.append([(i18n.t("instrument.readAway", lang), "muted")])
            for code, message, origin in read_away:
                lines.append([(str(code), "muted"), ("  " + message, "muted")])
                if origin:
                    lines.append([(origin, "muted")])
        else:
            lines.append([(i18n.t("instrument.readAwayNone", lang), "muted")])
        self._errors.border_title = box_title(
            i18n.t("instrument.errorBox", lang),
            i18n.t("instrument.queueN", lang, count=len(queue)),
            max(22, self._errors.size.width + 2),
        )
        self._error_static.update(box_text(app, lines, width))
        self._errors_rows = len(lines)

    def _read_away(self, queue: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
        """Errors seen in the event stream that the queue no longer holds, oldest first.

        A queue slot belongs to the newest matching event, so an entry turns grey the moment the
        ``:SYSTem:ERRor?`` that reads it away lands (tui-screens.md 5.1 #4, 14).
        """
        app: Any = self.app
        events = [
            e
            for e in app.store.events
            if e.get("kind") == "error" and e.get("instrument") == self.instrument_name
        ]
        remaining: dict[tuple[int, str], int] = {}
        for e in queue:
            key = (int(e["code"]), str(e["message"]))
            remaining[key] = remaining.get(key, 0) + 1
        out: list[tuple[int, str, str]] = []
        for ev in reversed(events):
            key = (int(ev.get("code") or 0), str(ev.get("message") or ""))
            if remaining.get(key, 0) > 0:
                remaining[key] -= 1  # still queued: the newest identical events fill the slots
                continue
            command, source = self._error_origin(ev)
            origin = ""
            if command:
                origin = "← " + " ".join(
                    part for part in (fmt_ns(int(ev.get("t_ns", 0))), source, command) if part
                )
            out.append((int(ev.get("code") or 0), str(ev.get("message") or ""), origin))
        out.reverse()
        return out

    def _error_origin(self, ev: dict[str, Any]) -> tuple[str, str]:
        """The command that caused the error and its source, from the log's pairing (7.1)."""
        app: Any = self.app
        lang = app.lang
        seq = ev.get("seq")
        for line in app.lines:
            if line.error_seq == seq:
                return line.command, line.source
        # An error no command owns (a tripped fault): name the fault and its source.
        for other in app.store.events:
            if (
                other.get("kind") == "fault"
                and other.get("reason") in ("active", "injected")
                and int(other.get("t_ns", -2)) == int(ev.get("t_ns", -1))
            ):
                source = source_label({"kind": "fault", "source": other.get("source", "")}, lang)
                return i18n.t("log.faultActive", lang, id=other.get("fault", "")), source
        return "", source_label(ev, lang)

    # -- the log (5.1 #5) ------------------------------------------------------------------------------------

    def _log_rows(self) -> int:
        # 3 at the minimum size (80 × 24): the embedded log scrolls to its end, while the channel
        # area above it must keep a visible row — at 4 the main row collapses to its borders (#454).
        return max(3, min(6, self.app.size.height - 26))

    def _refresh_log(self) -> None:
        app: Any = self.app
        lang = app.lang
        from ..log import visible

        lines = visible(app.lines, embedded=True, instrument=self.instrument_name)
        shown = lines[-200:]
        inner = max(20, app.size.width - 4)
        self._log_box.border_title = box_title(
            i18n.t("log.titleFor", lang, instrument=self.instrument_name),
            i18n.t("log.count", lang, count=len(lines)),
            inner,
        )
        self._log_box.styles.height = self._log_rows() + 2
        self._log_static.update(log_text(app, shown, lang, width=inner))
        self._log_box.scroll_end(animate=False, force=True)

    def _refresh_buttons(self) -> None:
        info = self.info
        readonly = self.remote_controlled
        for button in self.query(ControlButton):
            button.render_button(info, readonly)

    # ---- keys ---------------------------------------------------------------------------------------------

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        out = [
            ("Esc", i18n.t("keys.back", lang)),
            ("Tab", i18n.t("keys.move", lang)),
            ("⏎/e", i18n.t("keys.edit", lang)),
            ("+ -", i18n.t("keys.step", lang)),
            ("␣", i18n.t("keys.switch", lang)),
            (":", i18n.t("keys.scpi", lang)),
        ]
        if self.remote_controlled:
            out.append(("L", i18n.t("keys.local", lang)))
        out.append(("?", i18n.t("keys.reason", lang)))
        out.append(("q", i18n.t("common.quit", lang)))
        return out

    def _focused_button(self) -> ControlButton | None:
        focused = self.focused
        return focused if isinstance(focused, ControlButton) else None

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

    def _readonly_guard(self) -> bool:
        app: Any = self.app
        if self.remote_controlled:
            client = (self.info.get("remote") or [""])[0]
            source = source_label({"client": client, "kind": "scpi"}, app.lang)
            self._flash(
                None,
                i18n.t("instrument.readonlyHint", app.lang, instrument=self.instrument_name, source=source),
            )
            return True
        return False

    def _flash_target(self, button: ControlButton | None) -> Static | None:
        if button is not None:
            for entry in self._boxes:
                if f"ch:{entry['key']}" == button.box_id:
                    return entry["flash"]
            if button.box_id == "other":
                return self._other_flash
        for entry in self._boxes:
            if entry["box"].display:
                return entry["flash"]
        return self._other_flash

    def _flash(self, button: ControlButton | None, text: str) -> None:
        """``✗ <code> <message>`` under the focused button, gone after 2 s (tui-screens.md 5.3)."""
        target = self._flash_target(button)
        if target is None:
            return
        target.update(text)
        target.display = True
        target.scroll_visible(animate=False)  # the button row may sit below the fold (5.1 #2)

        def clear() -> None:
            target.display = False
            target.update("")

        self.set_timer(2.0, clear)

    def _error_text(self, err: dict[str, Any] | None, message: str | None = None) -> str:
        lang = self.app.lang
        if err:
            return i18n.t(
                "instrument.writeFailed", lang, code=err.get("code", ""), message=err.get("message", "")
            )
        return message or ""

    async def _configure(self, path: str, value: Any, button: ControlButton | None) -> bool:
        app: Any = self.app
        try:
            r = await app.client.configure(app.sid, self.instrument_name, path, value)
        except Exception as exc:
            self._flash(button, self._error_text(None, str(exc)))
            return False
        if not r.get("ok"):
            self._flash(button, self._error_text(r.get("error")))
            return False
        return True

    async def _run_writes(self, writes: list[tuple[str, Any]], button: ControlButton | None) -> None:
        for path, value in writes:
            if not await self._configure(path, value, button):
                return

    # -- key actions -------------------------------------------------------------------------------------------

    def action_edit(self) -> None:
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        control = button.control
        if control.kind == "switch":
            self.action_toggle()
            return
        if control.kind == "select":
            self._open_select(control)
            return
        if control.kind == "buttons":
            self._press_button(control, button)
            return
        if control.kind == "softkey":
            self._run_softkey(control, button)
            return
        self._open_number(control, button)

    def _open_select(self, control: C.Control) -> None:
        app: Any = self.app
        spec = control.spec or {}
        options = C.options_of(spec)
        if not options:
            return
        app.push_screen(
            SelectScreen(control.label, options, C.control_state(self.info, control)),
            lambda value: self._picked(control, value),
        )

    def _picked(self, control: C.Control, value: str | None) -> None:
        if value is None:
            return
        self.run_worker(
            self._run_writes(C.apply_sets(control, value), self._focused_button()), exclusive=True
        )

    def _open_number(self, control: C.Control, button: ControlButton) -> None:
        app: Any = self.app
        spec = dict(control.spec or {})
        path = control.path or ""
        history = app.scpi_history.setdefault(f"edit:{self.instrument_name}:{path}", [])
        current = C.control_state(self.info, control)

        async def submit(value: float) -> tuple[bool, str | None]:
            ok = await self._configure(path, value, button)
            return ok, None

        app.push_screen(NumberEditScreen(control.label, path, current, spec, history, submit))

    def _press_button(self, control: C.Control, button: ControlButton) -> None:
        """A panel ``buttons`` key: write its value and its ``sets`` (tui-screens.md 5.3)."""
        if control.dangerous and not bool(C.control_state(self.info, control)):
            self._confirm_switch(control, button, value=control.value)
            return
        self.run_worker(self._run_writes(C.apply_sets(control), button), exclusive=True)

    def action_toggle(self) -> None:
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        control = button.control
        if control.kind != "switch" or control.path is None:
            return
        value = bool(C.control_state(self.info, control))
        if not value and (control.dangerous or control.electrical):
            self._confirm_switch(control, button, value=True)
            return
        self.run_worker(self._run_writes([(control.path, not value)], button), exclusive=True)

    def _confirm_switch(self, control: C.Control, button: ControlButton, value: Any) -> None:
        """The dangerous switch's confirmation (tui-screens.md 10.1): what, where, what it affects."""
        app: Any = self.app
        lang = app.lang
        info = self.info
        shown = i18n.t("common.on", lang) if value is True else C.summary_text(value, control.spec, lang)
        lines = [f"{info.get('name', '')} · {info.get('model', '')}"]
        lines.append(i18n.t("confirm.willExecute", lang) + "  " + f"{control.path} → {shown}")
        related = self._related_settings()
        if related:
            lines.append(related)
        wiring = self._wiring_summary()
        if wiring:
            lines.append(wiring)
        lines.append("")
        lines.append(i18n.t("confirm.outputBody", lang))

        def on_done(ok: bool) -> None:
            if ok:
                self.run_worker(self._run_writes(C.apply_sets(control, value), button), exclusive=True)

        app.push_screen(
            ConfirmScreen(
                i18n.t("confirm.outputTitle", lang),
                lines,
                i18n.t("confirm.outputConfirm", lang),
                i18n.t("common.cancel", lang),
            ),
            on_done,
        )

    def _related_settings(self) -> str:
        lang = self.app.lang
        info = self.info
        state = {sv["path"]: sv for sv in info.get("state", [])}
        if self._template() == "smu":
            fn = C.summary_text(
                (state.get("source.function") or {}).get("value"), state.get("source.function"), lang
            )
            v = C.summary_text(
                (state.get("source.voltage.level") or {}).get("value"),
                state.get("source.voltage.level"),
                lang,
            )
            i = C.summary_text(
                (state.get("compliance.current.limit") or {}).get("value"),
                state.get("compliance.current.limit"),
                lang,
            )
            return f"{fn} {v}，{C.label_of(state.get('compliance.current.limit') or {}, lang, 'I limit')} {i}"
        parts = []
        for sv in info.get("state", []):
            if sv.get("electrical") and sv.get("path") not in (None,):
                parts.append(f"{C.label_of(sv, lang)} {C.summary_text(sv.get('value'), sv, lang)}")
            if len(parts) == 2:
                break
        return "，".join(parts)

    def _wiring_summary(self) -> str:
        app: Any = self.app
        lang = app.lang
        info = self.info
        session = app.session_info or {}
        parts = []
        for p in info.get("ports", []):
            ref = f"{info.get('name', '')}.{p['name']}"
            ends = opposite_ends(ref, session, lang)
            if ends:
                parts.append(f"{p['name']} to {' and '.join(ends)}")
        return "; ".join(parts)

    def action_step(self, direction: int) -> None:
        app: Any = self.app
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        control = button.control
        if control.kind not in ("number",):
            self._flash(button, i18n.t("instrument.stepHint", app.lang))
            return
        value = C.nudge(control, self.info, direction)
        if value is None:
            self._flash(button, i18n.t("instrument.stepHint", app.lang))
            return
        self.run_worker(self._run_writes([(control.path, value)], button), exclusive=True)

    def _run_softkey(self, control: C.Control, button: ControlButton | None) -> None:
        app: Any = self.app
        if control.action:

            async def run() -> None:
                try:
                    await app.client.panel(app.sid, self.instrument_name, control.action)
                except Exception as exc:
                    self._flash(button, self._error_text(None, str(exc)))

            self.run_worker(run(), exclusive=True)
            return
        if control.path is not None:
            self.run_worker(self._run_writes(C.apply_sets(control), button), exclusive=True)

    def action_console(self) -> None:
        self._scpi.open()

    async def _console_submit(self, command: str) -> tuple[str | None, str | None]:
        """POST /scpi; the reply as text, an error as ``✗ code message`` (tui-screens.md 10.3)."""
        app: Any = self.app
        lang = app.lang
        try:
            r = await app.client.scpi(app.sid, self.instrument_name, command)
        except Exception as exc:
            return None, str(exc)
        errors = r.get("errors") or []
        if errors:
            first = errors[0]
            return None, i18n.t(
                "instrument.writeFailed", lang, code=first.get("code", ""), message=first.get("message", "")
            )
        return r.get("response"), None

    def action_local(self) -> None:
        app: Any = self.app
        if not self.remote_controlled:
            return
        lang = app.lang
        info = self.info
        lines = [f"{info.get('name', '')} · {info.get('model', '')}"]
        lines.append(i18n.t("confirm.willExecute", lang) + "  " + i18n.t("remote.local", lang))
        lines.append("")
        lines.append(i18n.t("confirm.localBody", lang))

        def on_done(ok: bool) -> None:
            if ok:
                app.run_worker(self._take_local(), exclusive=True)

        app.push_screen(
            ConfirmScreen(
                i18n.t("confirm.localTitle", lang),
                lines,
                i18n.t("confirm.localConfirm", lang),
                i18n.t("common.cancel", lang),
            ),
            on_done,
        )

    async def _take_local(self) -> None:
        app: Any = self.app
        try:
            info = await app.client.local(app.sid, self.instrument_name)
        except Exception:
            return
        app._merge_instrument(info)
        app.bump()

    def action_reason(self) -> None:
        app: Any = self.app
        report = self._reason_report()
        if report is not None:
            app.push_screen(ReasonScreen(report, lambda: self._jump_to_command(report)))

    def _jump_to_command(self, report: ReasonReport) -> None:
        if report.jump_seq is None:
            return
        app: Any = self.app
        app.show("log")
        screen = app.screen
        focus = getattr(screen, "focus_seq", None)
        if focus is not None:
            focus(report.jump_seq)

    def _reason_report(self) -> ReasonReport | None:
        """What ``?`` explains (tui-screens.md 9.1): a fault, the limit, the focused setting, an error."""
        app: Any = self.app
        lang = app.lang
        info = self.info
        if not info:
            return None
        events = app.store.events
        faults = self._active_faults()
        if faults:
            return fault_report(faults[0], info, events, lang)
        limited = limited_report(info, self.readings, events, lang)
        if limited is not None:
            return limited
        button = self._focused_button()
        if button is not None and button.control.path:
            return setting_report(info, button.control.path, events, lang)
        for line in reversed(app.lines):
            if line.instrument == self.instrument_name and line.has_error:
                return error_report(line, lang)
        return None

    def _active_faults(self) -> list[dict[str, Any]]:
        """Faults that still explain this instrument's state (tui-screens.md 5.4, 8).

        A ``trip`` is a one-shot: the engine fires it and ends it at once, so it is never
        ``active`` — it still explains things for as long as the error it queued is readable.
        Communication faults explain while they are active or pending.
        """
        app: Any = self.app
        queue_codes = {int(e["code"]) for e in self.info.get("errors", [])}
        out: list[dict[str, Any]] = []
        for f in app.faults or []:
            spec = f.get("spec") or {}
            if spec.get("instrument") != self.instrument_name:
                continue
            status = f.get("status")
            if spec.get("kind") == "trip":
                if status == "active" or (status == "ended" and spec.get("code", -300) in queue_codes):
                    out.append(f)
            elif status in ("active", "pending"):
                out.append(f)
        return out
