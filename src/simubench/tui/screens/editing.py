"""The control editing layer every instrument screen shares (issue 635): focus and cursor over
the control buttons, the write path (configure with flash-on-error), the key actions (edit,
toggle with the dangerous/electrical confirmation, step, console, local, reason), and the embedded
log — one implementation instead of one per screen (they had drifted: the confirmation, the log
budget, the reason's jump). A screen provides layout and its row renderers; what it edits, this
layer knows. Hooks (`_flash_target`, `_related_settings`, `_wiring_summary`, `_reason_report`)
carry the per-screen parts.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from rich.text import Text
from textual.containers import Container
from textual.widgets import Static

from .. import controls as C
from .. import i18n
from ..log import source_label
from ..reasons import ReasonReport
from ..widgets import box_title, log_text, style_of
from .shell import ShellScreen
from .states import ConfirmScreen


class ControlButton(Static):
    """One control of a box: a field ``label value`` for a setting (switch, select, number), a
    ``[label]`` key for an action; focusable, highlighted while focused (5.2, 5.3). A field is
    edited where it stands: ``edit`` holds the state while it is."""

    DEFAULT_CSS = """
    ControlButton { width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    ControlButton:focus { background: $focus; color: $background; text-style: none; }
    ControlButton.-readonly { color: $muted; }
    ControlButton.-lit { text-style: reverse; }
    ControlButton.-editing { text-style: bold underline; }
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
        self._text = Text(f"[{control.label}]")
        super().__init__(self._text)
        self.control = control
        self.box_id = box_id  # where this button's write errors flash (5.3)
        self.edit: C.FieldEdit | None = None

    @property
    def is_field(self) -> bool:
        return self.control.kind in ("switch", "select", "number") and self.control.path is not None

    @property
    def natural_width(self) -> int:
        """Cells the button's text takes now: the grid sizes its columns from these."""
        return self._text.cell_len

    def field_text(self, info: dict[str, Any], lang: str) -> Text:
        control = self.control
        if self.edit is not None:
            if self.edit.kind == "number":
                shown = self.edit.buffer or "…"
                return Text.assemble((control.label, "dim"), " ", (shown, "bold"), ("▌", "bold"))
            return Text.assemble((control.label, "dim"), " ◂ ", (self.edit.choice or "—", "bold"), " ▸")
        if not self.is_field:
            return Text(f"[{control.label}]")
        state = {sv["path"]: sv for sv in info.get("state", [])}
        if control.path and C.inactive_under_auto(state, control.path):
            value = "AUTO"  # the stored manual value is not the one in effect (ui-design.md 6)
        else:
            value = C.summary_text(C.control_state(info, control), control.spec, lang)
        return Text.assemble((control.label, "dim"), " ", (value, "bold"))

    def render_button(self, info: dict[str, Any], readonly: bool, lang: str = "en") -> None:
        self.set_class(readonly or not self.control.writable, "-readonly")
        self.set_class(C.is_lit(info, self.control), "-lit")
        self.set_class(self.edit is not None, "-editing")
        self._text = self.field_text(info, lang)
        if self.is_attached:
            self.update(self._text)

    def on_mount(self) -> None:
        self.update(self._text)

    def check_consume_key(self, key: str, character: str | None) -> bool:
        """While a field is edited every key is the editor's: Enter commits, Esc cancels, the rest
        types or cycles — none of the screen's own bindings (``e``, ``w``, ``L`` …) may fire."""
        return self.edit is not None

    def on_key(self, event: Any) -> None:
        if self.edit is None:
            return
        event.stop()
        event.prevent_default()
        self.screen.edit_key(self, event.key, event.character)  # type: ignore[attr-defined]


class ButtonGrid(Container):
    """A box's controls in equal columns, as many per row as fit; never cut mid-word (5.2).

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
        self._buttons = list(buttons)

    @property
    def count(self) -> int:
        return len(self._buttons)

    def _columns(self, width: int) -> int:
        cell = min(max(b.natural_width for b in self._buttons), 24)
        return max(1, min(len(self._buttons), (width + 1) // (cell + 1)))

    def needed(self, width: int) -> int:
        """Rows all the buttons take at ``width`` (no side effects)."""
        if not self._buttons:
            return 0
        return -(-len(self._buttons) // self._columns(width))

    def fit(self, width: int, max_rows: int | None = None) -> int:
        """Set the columns for ``width``; show at most ``max_rows`` rows (the rest scrolls into
        view with the focus) and return the rows shown."""
        if not self._buttons:
            return 0
        cols = self._columns(width)
        self.styles.grid_size_columns = cols
        rows_ = -(-len(self._buttons) // cols)
        if max_rows is not None and rows_ > max_rows:
            self.styles.max_height = max_rows
            return max_rows
        self.styles.max_height = None
        return rows_

    def add_buttons(self, *buttons: ControlButton) -> None:
        self._buttons.extend(buttons)
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
            button.render_button(self.info, self.remote_controlled, self.app.lang)

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

    # -- finding a field (the palette and ``/``, tui-design.md 4) ----------------------------------------------

    def field_index(self) -> list[Any]:
        """A palette command per editable field of this screen: label, path and value, then the
        jump. Read from the buttons that exist, so only what the screen shows can be found."""
        from ..palette import Command

        lang = self.app.lang  # type: ignore[attr-defined]
        out: list[Command] = []
        for b in self.query(ControlButton):
            c = b.control
            if not (b.is_field and c.writable):
                continue
            value = C.summary_text(C.control_state(self.info, c), c.spec, lang)
            out.append(
                Command(
                    c.label,
                    i18n.t("palette.now", lang, path=c.path, value=value),
                    partial(self.go_to_field, c.path),
                    f"{c.label} {c.path}",
                )
            )
        return out

    def go_to_field(self, path: str, text: str | None = None) -> bool:
        """Focus the field for ``path``. With no ``text``, start editing it (a switch is only
        focused: the jump must not flip an output). With ``text``, type it in and commit, so the
        value goes through the same range check, unit parsing and confirmation as a typed one."""
        button = next((b for b in self.query(ControlButton) if b.is_field and b.control.path == path), None)
        if button is None:
            app: Any = self.app
            known = any(sv.get("path") == path for sv in self.info.get("state", []))
            if known and self.screen_key != "states":
                # This screen does not show that setting: the state tree lists every one (5.6).
                app.pending_field = (self.instrument_name, path, text)
                app.show("states", self.instrument_name)
                return True
            self._flash(None, i18n.t("palette.noField", app.lang, path=path))
            return False
        button.focus()
        button.scroll_visible(animate=False)
        if self._readonly_guard():
            return False
        control = button.control
        if text is None:
            if control.kind != "switch":
                self._edit_button(button)
            return True
        if control.kind == "switch":
            on = text.strip().lower() in ("1", "on", "true", "yes")
            off = text.strip().lower() in ("0", "off", "false", "no")
            if (on or off) and on != bool(C.control_state(self.info, control)):
                self._toggle_button(button)
            return on or off
        if control.kind == "select":
            options = C.options_of(control.spec or {})
            wanted = text.strip().lower()
            pick = next((v for v, label in options if wanted in (str(v).lower(), str(label).lower())), None)
            if pick is None:
                self._flash(button, i18n.t("edit.accepted", self.app.lang))  # type: ignore[attr-defined]
                return False
            self._begin_select(button)
            if button.edit is not None and pick in button.edit.options:
                button.edit.index = button.edit.options.index(pick)
            self._commit_edit(button)
            return True
        self._begin_number(button)
        if button.edit is not None:
            button.edit.buffer = text
        self._commit_edit(button)
        return True

    # -- key actions -------------------------------------------------------------------------------------------

    def action_edit(self) -> None:
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        self._edit_button(button)

    def _edit_button(self, button: ControlButton) -> None:
        control = button.control
        if control.kind == "switch":
            self._toggle_button(button)
            return
        if control.kind == "select":
            self._begin_select(button)
            return
        if control.kind == "buttons":
            self._press_button(control, button)
            return
        if control.kind == "softkey":
            self._run_softkey(control, button)
            return
        self._begin_number(button)

    # -- editing a field where it stands (tui-screens.md 5.3) --------------------------------------------------

    def _begin_select(self, button: ControlButton) -> None:
        control = button.control
        options = [value for value, _label in C.options_of(control.spec or {})]
        if not options:
            return
        button.edit = C.FieldEdit.for_select(options, C.control_state(self.info, control))
        self._repaint_field(button)

    def _begin_number(self, button: ControlButton) -> None:
        app: Any = self.app
        lang = app.lang
        spec = button.control.spec or {}
        button.edit = C.FieldEdit("number")
        self._repaint_field(button)
        hint = (
            i18n.t("edit.range", lang)
            + "  "
            + (C.range_text(spec) or "—")
            + "  ·  "
            + i18n.t("edit.accepted", lang)
        )
        self._flash(button, hint)

    def _repaint_field(self, button: ControlButton) -> None:
        button.render_button(self.info, self.remote_controlled, self.app.lang)

    def _end_edit(self, button: ControlButton) -> None:
        button.edit = None
        self._repaint_field(button)

    def edit_key(self, button: ControlButton, key: str, character: str | None) -> None:
        """One key for the field being edited: Enter commits, Esc cancels; a number types, a
        choice cycles with ←/→ (or + / −)."""
        edit = button.edit
        if edit is None:
            return
        if key == "escape":
            self._end_edit(button)
            return
        if key == "enter":
            self._commit_edit(button)
            return
        if edit.kind == "number":
            if key == "backspace":
                edit.backspace()
            elif character:
                edit.type(character)
        elif key in ("left", "up", "minus"):
            edit.cycle(-1)
        elif key in ("right", "down", "plus", "space"):
            edit.cycle(1)
        self._repaint_field(button)

    def _commit_edit(self, button: ControlButton) -> None:
        edit = button.edit
        control = button.control
        if edit is None:
            return
        if edit.kind == "select":
            changed = edit.index != edit.start
            value = edit.choice
            self._end_edit(button)
            if changed and value is not None:
                self.run_worker(self._run_writes(C.apply_sets(control, value), button), exclusive=True)
            return
        if not edit.buffer.strip():  # nothing typed: leave the setting as it is
            self._end_edit(button)
            return
        lang = self.app.lang
        try:
            value = C.parse_input(edit.buffer, control.spec or {})
        except ValueError as exc:  # stay in the field: the user corrects the number
            self._flash(button, i18n.t("edit.outOfRange" if str(exc) == "range" else "edit.accepted", lang))
            return
        self._end_edit(button)
        self.run_worker(self._run_writes([(control.path, value)], button), exclusive=True)

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

    def action_toggle(self) -> None:
        button = self._focused_button()
        if button is None or self._readonly_guard():
            return
        self._toggle_button(button)

    def _toggle_button(self, button: ControlButton) -> None:
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

    def action_switch_instrument(self, direction: int) -> None:
        """``[`` / ``]``: the previous / next instrument of the bench, without going back to the
        overview first (tui-screens.md 2.3). The screen is replaced, not stacked."""
        app: Any = self.app
        names = [i["name"] for i in (app.session_info or {}).get("instruments", [])]
        if len(names) < 2 or self.instrument_name not in names:
            return
        target = names[(names.index(self.instrument_name) + direction) % len(names)]
        app.open_instrument(target, replace=True)

    def action_states(self) -> None:
        """``t``: this instrument's state tree (tui-screens.md 5.6)."""
        self.app.show("states", self.instrument_name)  # type: ignore[attr-defined]

    def action_search(self) -> None:
        """``/``: search this screen's settings; Enter jumps to the field and edits it."""
        from ..palette import open_settings_search

        if self.remote_controlled:
            self._readonly_guard()
            return
        open_settings_search(self.app)

    def take_pending_field(self, tries: int = 40) -> None:
        """Do the field jump the palette asked for before this screen existed. The controls are
        mounted a beat after the screen is, so wait for the field instead of missing it."""
        app: Any = self.app
        pending = app.pending_field
        if pending is None or pending[0] != self.instrument_name:
            return
        if tries and not any(b.control.path == pending[1] for b in self.query(ControlButton)):
            self.set_timer(0.05, lambda: self.take_pending_field(tries - 1))
            return
        app.pending_field = None
        self.go_to_field(pending[1], pending[2])

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
