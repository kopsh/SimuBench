"""The instrument screen (``Enter`` on a card, tui-screens.md 5): identity, per-channel boxes,
controls, wiring and load, the error history, the per-instrument log and the SCPI console.

The boxes and their buttons are built once per screen (their structure is stable);
``refresh_content`` rewrites the texts in place, so focus never jumps. All data comes from the
app's session, readings, faults, lines and event store; the screen only performs the writes the
focused control asks for (POST configure / panel / local, always with the tui header).
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from .. import controls as C
from .. import i18n, rows
from ..cards import ARROWS, opposite_ends
from ..fmt import cell_len, fmt, pad
from ..log import source_label
from ..reasons import ReasonReport, error_report, fault_report, limited_report, setting_report
from ..widgets import box_text, box_title, render_segments
from .dialogs import ScpiConsole
from .editing import ControlButton, ControlEditingScreen


def _english(text: Any) -> str | None:
    """``text`` when it is written in Latin script, else None: the TUI shows no CJK (D23)."""
    if not text:
        return None
    t = str(text)
    return t if all(ord(c) < 0x2E80 for c in t) else None


class _Box(Vertical):
    """A round-bordered box; the border carries the title, Statics inside carry the rows."""

    DEFAULT_CSS = """
    _Box { border: round $muted 60%; height: auto; border-title-align: left; padding: 0 1; }
    """


class InstrumentScreen(ControlEditingScreen):
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

    def _smu_lines(self, state: dict[str, dict]) -> list[list[tuple[str, str]]]:
        return rows.smu_rows(self.app.lang, state, self.readings)

    def _generator_lines(self, state: dict[str, dict], channel: int) -> list[list[tuple[str, str]]]:
        return rows.generator_rows(self.app.lang, state, channel)

    def _panel_lines(
        self, width: int | None = None, budget: int | None = None
    ) -> list[list[tuple[str, str]]]:
        return rows.panel_rows(self.app.lang, self.info, self.readings, width, budget)

    def _readings_lines(self, width: int | None = None) -> list[list[tuple[str, str]]]:
        return rows.readings_rows(self.readings)

    def _spectrum_lines(self, state: dict[str, dict], width: int) -> list[list[tuple[str, str]]]:
        return rows.spectrum_rows(self.app.lang, self.readings, state, width)

    def _rf_lines(
        self, state: dict[str, dict], width: int, budget: int | None = None
    ) -> list[list[tuple[str, str]]]:
        return rows.rf_rows(self.app.lang, state, self.readings, width, budget)

    def _load_lines(self, state: dict[str, dict], width: int) -> list[list[tuple[str, str]]]:
        return rows.load_rows(self.app.lang, state, self.readings, width)

    def _lockin_lines(self, state: dict[str, dict], width: int) -> list[list[tuple[str, str]]]:
        return rows.lockin_rows(self.app.lang, self.readings, state, width)

    def _generic_lines(self, state: dict[str, dict], width: int | None = None) -> list[list[tuple[str, str]]]:
        return rows.generic_rows(self.app.lang, state, width)

    def _reason_line(self, template: str) -> list[tuple[str, str]] | None:
        return rows.reason_line(self.app.lang, template, self.info, self.readings, self._active_faults())

    def _read_away(self, queue: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
        app: Any = self.app
        return rows.read_away(app.lang, app.store.events, queue, app.lines, self.instrument_name)

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
        out = rows.grouped_columns(self.app.lang, groups, state, width)
        self._other.display = True
        self._other.border_title = i18n.t("instrument.otherSettings", lang)
        self._other_static.update(box_text(app, out, width))
        if not self._other_buttons.children:
            controls = [
                C._control_for(path, state[path], lang)
                for path in paths
                if path in state
                and not state[path].get("readonly")
                and state[path].get("type") in ("bool", "enum", "float", "int")
            ]
            self._other_buttons.mount(*[ControlButton(c, "other") for c in controls])
        self._other_rows = len(out) + (1 if self._other_buttons.children else 0)

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

    # -- the log (5.1 #5) ------------------------------------------------------------------------------------

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

    # -- writes ----------------------------------------------------------------------------------------------

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

    # -- key actions -------------------------------------------------------------------------------------------

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
