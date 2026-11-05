"""The control editing layer every instrument screen shares (issue 635): focus and cursor over
the control buttons, the write path (configure with flash-on-error), the key actions (edit,
toggle with the dangerous/electrical confirmation, step, console, local, reason), and the embedded
log — one implementation instead of one per screen (they had drifted: the confirmation, the log
budget, the reason's jump). A screen provides layout and its row renderers; what it edits, this
layer knows. Hooks (`_flash_target`, `_related_settings`, `_wiring_summary`, `_reason_report`)
carry the per-screen parts.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.containers import Container
from textual.widgets import Static

from .. import controls as C
from .. import i18n
from ..fmt import cell_len
from ..log import source_label
from ..reasons import ReasonReport
from ..widgets import box_title, log_text, style_of
from .dialogs import NumberEditScreen, SelectScreen
from .shell import ShellScreen
from .states import ConfirmScreen


class ControlButton(Static):
    """One ``[label]`` button of a box's button grid; focusable, reversed while focused (5.2)."""

    DEFAULT_CSS = """
    ControlButton { width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    ControlButton:focus { background: $focus; color: $background; text-style: none; }
    ControlButton.-readonly { color: $muted; }
    ControlButton.-lit { text-style: reverse; }
    ControlButton.-highlighted { background: $focus; color: $background; text-style: bold; }
    /* NO_COLOR: the tokens carry no colour, so the focus and the frame are bold and underlined */
    .-mono ControlButton:focus, .-mono ControlButton.-highlighted {
        background: transparent; color: ansi_default; text-style: bold underline;
    }
    """

    can_focus = True
    SCOPED_CSS = False  # the NO_COLOR rule above starts at the app's `.-mono`, outside the widget

    def __init__(self, control: C.Control, box_id: str) -> None:
        # A Text, not a bare string: the brackets would parse as console markup and an ASCII
        # label (`[Output]`, `[OFF]`) renders as nothing (5.2; found via #454).
        super().__init__(Text(f"[{control.label}]"))
        self.control = control
        self.box_id = box_id  # where this button's write errors flash (5.3)

    def render_button(self, info: dict[str, Any], readonly: bool) -> None:
        self.set_class(readonly or not self.control.writable, "-readonly")
        self.set_class(C.is_lit(info, self.control), "-lit")


class ButtonGrid(Container):
    """A box's buttons in equal columns, as many per row as fit; never cut mid-word (5.2).

    Rows wrap instead of overflowing, so every control stays visible and reachable. ``fit``
    takes the inner width, sets the column count and returns how many rows that makes — the
    screen's row budget needs the height before the layout has run. A short screen caps the rows
    so the readings above keep their room (the buttons below the cap scroll into view).
    """

    DEFAULT_CSS = """
    ButtonGrid {
        layout: grid; grid-size: 1; grid-gutter: 0 1; grid-rows: 1; height: auto; width: 1fr;
        overflow-y: auto; scrollbar-size-vertical: 0;
    }
    """

    def __init__(self, *buttons: ControlButton, **kwargs: Any) -> None:
        super().__init__(*buttons, **kwargs)
        self._cells = [cell_len(b.control.label) + 2 for b in buttons]  # ``[label]``

    @property
    def count(self) -> int:
        return len(self._cells)

    def needed(self, width: int) -> int:
        """Rows all the buttons take at ``width`` (no side effects)."""
        if not self._cells:
            return 0
        cell = min(max(self._cells), 24)
        cols = max(1, min(len(self._cells), (width + 1) // (cell + 1)))
        return -(-len(self._cells) // cols)

    def fit(self, width: int, max_rows: int | None = None) -> int:
        """Set the columns for ``width``; show at most ``max_rows`` rows (the rest scrolls into
        view with the focus) and return the rows shown."""
        if not self._cells:
            return 0
        cell = min(max(self._cells), 24)
        cols = max(1, min(len(self._cells), (width + 1) // (cell + 1)))
        self.styles.grid_size_columns = cols
        rows_ = -(-len(self._cells) // cols)
        if max_rows is not None and rows_ > max_rows:
            self.styles.max_height = max_rows
            return max_rows
        self.styles.max_height = None
        return rows_

    def add_buttons(self, *buttons: ControlButton) -> None:
        self._cells.extend(cell_len(b.control.label) + 2 for b in buttons)
        self.mount(*buttons)


class ControlEditingScreen(ShellScreen):
    """Everything an instrument screen does to its controls, except drawing them."""

    # ---- session data ----------------------------------------------------------------------------------

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

    # ---- the embedded log ------------------------------------------------------------------------------

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
        if shown:
            self._log_static.update(log_text(app, shown, lang, width=inner))
        else:  # an empty state names the next step (ui-design.md 6)
            address = self.info.get("address_virtual", "")
            hint = i18n.t("log.emptyFor", lang, address=address)
            self._log_static.update(Text(hint, style=style_of(app, "muted")))
        self._log_box.scroll_end(animate=False, force=True)

    def _refresh_buttons(self) -> None:
        for button in self.query(ControlButton):
            button.render_button(self.info, self.remote_controlled)

    # ---- focus and cursor ------------------------------------------------------------------------------

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
        """Where a flash message shows: a widget under the edited control's box, per screen."""
        return None

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

    def _press_button(self, control: C.Control, button: ControlButton) -> None:
        """A panel ``buttons`` key: write its value and its ``sets`` (tui-screens.md 5.3)."""
        if control.dangerous and not bool(C.control_state(self.info, control)):
            self._confirm_switch(control, button, value=control.value)
            return
        self.run_worker(self._run_writes(C.apply_sets(control), button), exclusive=True)

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

    def action_toggle(self) -> None:
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        control = button.control
        if control.kind != "switch" or control.path is None:
            return
        value = bool(C.control_state(self.info, control))
        if not value and (control.dangerous or control.electrical):
            # Switching an output or an electrical setting on (tui-screens.md 10.1) — every
            # screen confirms, not only the ones that happened to grow the dialog first.
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
        """The affected settings a confirmation names; screens with such context override this."""
        return ""

    def _wiring_summary(self) -> str:
        """The ports a confirmation names; screens that show wiring override this."""
        return ""

    def action_step(self, direction: int) -> None:
        app: Any = self.app
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        control = button.control
        if control.kind != "number":
            self._flash(button, i18n.t("instrument.stepHint", app.lang))
            return
        value = C.nudge(control, self.info, direction)
        if value is None:
            self._flash(button, i18n.t("instrument.stepHint", app.lang))
            return
        self.run_worker(self._run_writes([(control.path, value)], button), exclusive=True)

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
            app.show_reason(report)

    def _reason_report(self) -> ReasonReport | None:
        """What ``?`` explains; each screen knows its own order of explanations."""
        return None
