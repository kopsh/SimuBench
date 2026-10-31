"""The T2 dialogs (tui-screens.md 10.2, 9.1): the number editor, the option list, the reason dialog.

All are modal, centered, thick border, dimmed background; ``Esc`` always cancels. They receive a
submit callback that performs the write (the app's client) and show the write's error in place.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .. import i18n
from ..controls import parse_number, range_text
from ..fmt import cell_len, fmt
from ..reasons import ReasonReport, plain_text


class HistoryInput(Input):
    """An ``Input`` whose ↑↓ walk a history instead of moving the cursor (10.2, 10.3).

    Textual's ``Input`` binds no arrow keys, so without these the keys would bubble to the
    screen and move the focus instead.
    """

    BINDINGS: ClassVar = [
        Binding("up", "history_up", show=False),
        Binding("down", "history_down", show=False),
    ]

    def __init__(self, history: Sequence[str] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._history: Sequence[str] = history or []
        self._index = len(self._history)

    def set_history(self, history: Sequence[str]) -> None:
        self._history = history
        self._index = len(history)

    def action_history_up(self) -> None:
        if self._history:
            self._index = max(0, self._index - 1)
            self.value = self._history[self._index]
            self.cursor_position = len(self.value)

    def action_history_down(self) -> None:
        if self._history:
            self._index = min(len(self._history), self._index + 1)
            self.value = self._history[self._index] if self._index < len(self._history) else ""
            self.cursor_position = len(self.value)


class NumberEditScreen(ModalScreen[bool]):
    """Edit a numeric setting (tui-screens.md 10.2): unit conversion, range check, write errors."""

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    NumberEditScreen {
        align: center middle;
        background: $background 60%;
        #edit-box { width: 62; height: auto; border: thick $focus; padding: 1 2; }
        #edit-box Horizontal { height: auto; }
        #edit-box Vertical { height: auto; }
        #edit-error { color: $bad; }
        #edit-range { color: $muted; }
        #edit-accepted { color: $muted; }
        #edit-keys { color: $muted; margin-top: 1; }
        Input { width: 1fr; }
    }
    """

    def __init__(
        self,
        label: str,
        path: str,
        value: Any,
        spec: dict[str, Any] | None,
        history: list[str] | None = None,
        submit: Callable[[float], Awaitable[tuple[bool, str | None]]] | None = None,
    ) -> None:
        super().__init__()
        self._label = label
        self._path = path
        self._value = value
        self._spec = spec or {}
        self._history = history or []
        self._history_index = len(self._history)
        self._submit = submit
        self._busy = False

    def compose(self) -> ComposeResult:
        lang = self.app.lang
        unit = self._spec.get("display_unit") or self._spec.get("unit") or ""
        shown = fmt(self._value, unit) if isinstance(self._value, (int, float)) else "—"
        with Vertical(id="edit-box"):
            yield Static(i18n.t("edit.current", lang) + "  " + shown, id="edit-current")
            with Horizontal():
                yield Static(i18n.t("edit.newValue", lang) + "  ")
                yield HistoryInput(self._history, id="edit-input")
                yield Static(" " + unit if unit else "")
            yield Static(i18n.t("edit.range", lang) + "  " + (range_text(self._spec) or "—"), id="edit-range")
            yield Static(i18n.t("edit.accepted", lang), id="edit-accepted")
            yield Static("", id="edit-error")
            yield Static(
                i18n.t("edit.write", lang)
                + "   "
                + i18n.t("edit.cancelKey", lang)
                + "   "
                + i18n.t("edit.historyKey", lang),
                id="edit-keys",
            )

    def on_mount(self) -> None:
        box = self.query_one("#edit-box", Vertical)
        box.border_title = i18n.t("edit.numberTitle", self.app.lang, label=self._label, path=self._path)
        inp = self.query_one("#edit-input", Input)
        if self._history:
            inp.value = self._history[-1]
        inp.focus()

    async def _do_submit(self, text: str) -> None:
        lang = self.app.lang
        error = self.query_one("#edit-error", Static)
        spec = self._spec
        try:
            value = parse_number(text, spec.get("display_unit") or spec.get("unit"))
        except ValueError:
            error.update(i18n.t("edit.accepted", lang))
            return
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and value < float(lo)) or (hi is not None and value > float(hi)):
            error.update(i18n.t("edit.outOfRange", lang))
            return
        if self._submit is None:
            self.dismiss(True)
            return
        self._busy = True
        try:
            ok, message = await self._submit(value)
        finally:
            self._busy = False
        if ok:
            self.dismiss(True)
        else:
            error.update(message or i18n.t("edit.failed", lang, code="", message=""))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "edit-input" or self._busy:
            return
        text = event.value.strip()
        if text:
            if text not in self._history:
                self._history.append(text)
            self._history_index = len(self._history)
        self.run_worker(self._do_submit(text), exclusive=True)

    def on_key(self, event) -> None:
        """Esc cancels here; the event must not also reach the screen below (it would pop it)."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()

    def action_cancel(self) -> None:
        if not self._busy:
            self.dismiss(False)


class SelectScreen(ModalScreen[str | None]):
    """Choose one of an enum's options (tui-screens.md 5.3): ↑↓ select, ⏎ write, Esc cancel."""

    BINDINGS: ClassVar = [
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("enter", "pick", show=False),
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    SelectScreen {
        align: center middle;
        background: $background 60%;
        #select-box { width: 44; height: auto; max-height: 16; border: thick $focus; padding: 1 2; }
    }
    """

    def __init__(self, label: str, options: Sequence[tuple[str, str]], current: str | None) -> None:
        super().__init__()
        self._label = label
        self._options = list(options)
        self._current = current
        self._cursor = next((i for i, (v, _) in enumerate(self._options) if v == current), 0)
        self._rows_static: Static | None = None

    def compose(self) -> ComposeResult:
        self._rows_static = Static(self._rows_text(), id="select-rows")
        yield Vertical(self._rows_static, id="select-box")

    def on_mount(self) -> None:
        self.query_one("#select-box", Vertical).border_title = i18n.t(
            "edit.selectTitle", self.app.lang, label=self._label
        )

    def _rows_text(self) -> str:
        from ..fmt import fit

        rows = []
        for i, (value, label) in enumerate(self._options):
            mark = "▸ " if i == self._cursor else "  "
            mark += "● " if value == self._current else "  "
            rows.append(mark + fit(label, 34))
        return "\n".join(rows)

    def _redraw(self) -> None:
        if self._rows_static is not None:
            self._rows_static.update(self._rows_text())

    def action_cursor(self, direction: str) -> None:
        if not self._options:
            return
        self._cursor = (self._cursor + (1 if direction == "down" else -1)) % len(self._options)
        self._redraw()

    def action_pick(self) -> None:
        if self._options:
            self.dismiss(self._options[self._cursor][0])

    def action_cancel(self) -> None:
        self.dismiss(None)


class ReasonScreen(ModalScreen[None]):
    """The reason dialog (tui-screens.md 9.1): conclusion, evidence, ⏎ jump, c copy."""

    BINDINGS: ClassVar = [
        Binding("enter", "jump", show=False),
        Binding("c", "copy", show=False),
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ReasonScreen {
        align: center middle;
        background: $background 60%;
        #reason-box { width: 72; max-width: 92%; height: auto; max-height: 20; border: thick $focus; padding: 1 2; }
        #reason-conclusion { margin-bottom: 1; }
        #reason-keys { color: $muted; margin-top: 1; }
    }
    """

    def __init__(self, report: ReasonReport, jump: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._report = report
        self._jump = jump

    def compose(self) -> ComposeResult:
        lang = self.app.lang
        from ..widgets import render_segments

        with Vertical(id="reason-box"):
            yield Static(
                render_segments(self.app, [(self._report.conclusion, "bold")]), id="reason-conclusion"
            )
            from rich.text import Text

            body = Text()
            for ev in self._report.evidences:
                body.append(ev.label + "  ", style=self._style("muted"))
                body.append(ev.value)
                if ev.pointer:
                    body.append("  " + ev.pointer, style=self._style("muted"))
                body.append("\n")
                if ev.source:
                    body.append(Text(ev.source, style=self._style("muted")))
                    body.append("\n")
            yield Static(body, id="reason-evidences")
            yield Static(self._report.hint or "", id="reason-hint")
            yield Static(i18n.t("reason.jumpKeys", lang), id="reason-keys")

    def _style(self, token: str) -> str:
        from ..widgets import style_of

        return style_of(self.app, token)

    def on_mount(self) -> None:
        title = self._report.title
        box = self.query_one("#reason-box", Vertical)
        box.border_title = f"━ {title} " + "━" * max(2, 40 - cell_len(title))
        hint = self.query_one("#reason-hint", Static)
        if not self._report.hint:
            hint.display = False

    def action_jump(self) -> None:
        if self._jump is not None:
            self.dismiss(None)
            self._jump()

    def action_copy(self) -> None:
        self.app.copy_to_clipboard(plain_text(self._report))

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---- the SCPI console (tui-screens.md 10.3) ----------------------------------------------------------

_HISTORY_PATH = Path.home() / ".simubench" / "tui-history"
_HISTORY_LIMIT = 100


def load_history() -> dict[str, list[str]]:
    """Per-instrument command history, kept across runs in ``~/.simubench/tui-history``."""
    try:
        data = json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        {k: [str(c) for c in v] for k, v in data.items() if isinstance(v, list)}
        if isinstance(data, dict)
        else {}
    )


def save_history(history: Mapping[str, Sequence[str]]) -> None:
    """Write the history back; failures are silent (a console must not crash the TUI)."""
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        trimmed = {k: list(v)[-_HISTORY_LIMIT:] for k, v in history.items()}
        _HISTORY_PATH.write_text(json.dumps(trimmed, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


class ScpiConsole(Vertical):
    """``:`` opens a one-line console above the bottom bar (tui-screens.md 10.3).

    The prompt names the instrument; ``⏎`` POSTs the command (the reply lands in the
    communication log with the source 终端, and never takes a script's reply, D32); ``↑↓`` walks
    the instrument's own history; ``Esc`` closes.
    """

    DEFAULT_CSS = """
    ScpiConsole {
        dock: bottom;
        height: auto;
        display: none;
        padding: 0 1;
        background: $background;
    }
    ScpiConsole.-open { display: block; }
    #console-row { height: 1; }
    #console-prompt { color: $focus; }
    #console-input { width: 1fr; border: none; height: 1; padding: 0; }
    #console-hints { color: $muted; width: auto; }
    #console-reply { height: 1; color: $muted; padding-left: 2; }
    """

    BINDINGS: ClassVar = []

    def __init__(
        self, instrument: str, submit: Callable[[str], Awaitable[tuple[str | None, str | None]]]
    ) -> None:
        super().__init__(id="console")
        self.instrument = instrument
        self._submit = submit
        self._history: list[str] = []
        self._busy = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="console-row"):
            yield Static(
                i18n.t("console.prompt", self.app.lang, instrument=self.instrument), id="console-prompt"
            )
            yield HistoryInput(id="console-input")
            yield Static(
                i18n.t("console.send", self.app.lang) + "  " + i18n.t("console.history", self.app.lang)
                + "  " + i18n.t("console.close", self.app.lang),
                id="console-hints",
            )
        yield Static("", id="console-reply")

    def open(self) -> None:
        app: Any = self.app
        self._history = list(app.scpi_history.get(self.instrument, []))
        inp = self.query_one("#console-input", HistoryInput)
        inp.set_history(self._history)
        self.add_class("-open")
        inp.focus()

    def close(self) -> None:
        self.remove_class("-open")
        self.query_one("#console-reply", Static).update("")
        screen = self.screen
        screen.set_focus(None)
        screen.refresh_content()

    def is_open(self) -> bool:
        return self.has_class("-open")

    async def _run(self, command: str) -> None:
        reply, error = await self._submit(command)
        lang = self.app.lang
        from rich.text import Text

        row = self.query_one("#console-reply", Static)
        if error:
            row.update(Text(error, style=self._style("bad")))
        elif reply is None:
            row.update(Text(i18n.t("console.noReply", lang), style=self._style("muted")))
        else:
            row.update(Text("→ " + reply))
        self._busy = False

    def _style(self, token: str) -> str:
        from ..widgets import style_of

        return style_of(self.app, token)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._busy:
            return
        command = event.value.strip()
        if command:
            if command not in self._history:
                self._history.append(command)
            app: Any = self.app
            app.scpi_history[self.instrument] = self._history
            save_history(app.scpi_history)
            self.query_one("#console-input", HistoryInput).set_history(self._history)
        self._busy = True
        self.run_worker(self._run(command), exclusive=True)

    def on_key(self, event) -> None:
        """Esc closes the console and stays on the screen (the event stops here)."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.close()
