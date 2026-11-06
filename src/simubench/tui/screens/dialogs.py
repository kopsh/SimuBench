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
from ..fmt import cell_len
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
        # The children are constructor children, so they exist as soon as the console does —
        # ``open()`` may run before a compose-cycle would have mounted compose() children.
        lang = getattr(self, "lang", None) or "zh"
        self.instrument = instrument
        self._submit = submit
        self._history: list[str] = []
        self._busy = False
        self._input = HistoryInput(id="console-input")
        # The closed console must not be reachable by the screen's auto-focus: a focused Input
        # would swallow ``:`` and every other character instead of letting the keys bubble.
        self._input.can_focus = False
        self._reply = Static("", id="console-reply")
        super().__init__(
            Horizontal(
                Static(i18n.t("console.prompt", lang, instrument=instrument), id="console-prompt"),
                self._input,
                Static(
                    i18n.t("console.send", lang)
                    + "  "
                    + i18n.t("console.history", lang)
                    + "  "
                    + i18n.t("console.close", lang),
                    id="console-hints",
                ),
                id="console-row",
            ),
            self._reply,
            id="console",
        )

    def on_mount(self) -> None:
        """The prompt and hints are built before attachment; re-render with the app's language."""
        lang = self.app.lang
        self.query_one("#console-prompt", Static).update(
            i18n.t("console.prompt", lang, instrument=self.instrument)
        )
        self.query_one("#console-hints", Static).update(
            i18n.t("console.send", lang)
            + "  "
            + i18n.t("console.history", lang)
            + "  "
            + i18n.t("console.close", lang)
        )

    def open(self) -> None:
        app: Any = self.app
        self._history = list(app.scpi_history.get(self.instrument, []))
        self._input.set_history(self._history)
        self.add_class("-open")
        self._input.can_focus = True
        self._input.focus()

    def close(self) -> None:
        self.remove_class("-open")
        self._input.can_focus = False
        self._reply.update("")
        screen = self.screen
        screen.set_focus(None)
        screen.refresh_content()

    def is_open(self) -> bool:
        return self.has_class("-open")

    async def _run(self, command: str) -> None:
        reply, error = await self._submit(command)
        lang = self.app.lang
        from rich.text import Text

        if error:
            self._reply.update(Text(error, style=self._style("bad")))
        elif reply is None:
            self._reply.update(Text(i18n.t("console.noReply", lang), style=self._style("muted")))
        else:
            self._reply.update(Text("→ " + reply))
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
            self._input.set_history(self._history)
        self._busy = True
        self.run_worker(self._run(command), exclusive=True)

    def on_key(self, event) -> None:
        """Esc closes the console and stays on the screen (the event stops here)."""
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.close()
