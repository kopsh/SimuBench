"""The startup screens: the session picker and message screens (tui-screens.md 3), plus the
confirm dialog used for quitting with scripts connected and for rebuilding (tui-screens.md 10.1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Label, Static

from .. import i18n
from ..fmt import cell_len, fmt_ns, pad


class PickerScreen(Screen):
    """``simubench tui --attach`` with several sessions: columns per the mock in section 3."""

    BINDINGS: ClassVar = [
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("enter", "connect", show=False),
        Binding("escape", "exit_app", show=False),
    ]

    def __init__(self, url: str, reason: str | None = None) -> None:
        super().__init__()
        self._url = url
        self._reason = reason
        self._rows: list[dict[str, Any]] = []
        self._cursor = 0
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="picker")

    def on_mount(self) -> None:
        self.set_interval(0.2, self._poll)
        self.run_worker(self._load(), exclusive=False)

    async def _load(self) -> None:
        app: Any = self.app
        try:
            self._rows = await app.client.sessions()
        except Exception as exc:  # the picker stays and shows the reason
            self._error = str(exc)
        self._render_picker()

    def _poll(self) -> None:
        self._render_picker()

    def _clock_short(self, mode: str) -> str:
        short = (
            {"command": "命令", "wall": "墙钟", "stepped": "步进"}
            if self.app.lang == "zh"
            else {
                "command": "command",
                "wall": "wall",
                "stepped": "stepped",
            }
        )
        return short.get(mode, mode)

    def _render_picker(self) -> None:
        app: Any = self.app
        lang = app.lang
        width = max(40, app.size.width - 2)
        title = i18n.t("connect.pickerTitle", lang, url=self._url)
        text = f"┌─ {title} " + "─" * max(0, width - cell_len(title) - 5) + "┐\n"
        headers = [
            i18n.t("picker.colSession", lang),
            i18n.t("picker.colBench", lang),
            i18n.t("picker.colClock", lang),
            i18n.t("picker.colTime", lang),
            i18n.t("picker.colClients", lang),
        ]
        widths = (12, 22, 8, 12, 12)
        text += "│ " + pad(headers[0], widths[0]) + pad(headers[1], widths[1])
        text += pad(headers[2], widths[2]) + pad(headers[3], widths[3]) + headers[4] + " │\n"
        if self._error:
            text += f"│ {self._error}\n"
        for n, s in enumerate(self._rows):
            clients = s.get("clients", [])
            if clients:
                labels: dict[str, int] = {}
                for c in clients:
                    labels[c.get("client", "?")] = labels.get(c.get("client", "?"), 0) + 1
                client_text = "，".join(
                    f"{_client_short(k, lang)} ×{v}" if v > 1 else _client_short(k, lang)
                    for k, v in labels.items()
                )
            else:
                client_text = i18n.t("picker.noClients", lang)
            cursor = "▸ " if n == self._cursor else "  "
            cells = [
                cursor + s.get("id", ""),
                s.get("bench_name", ""),
                self._clock_short(s.get("clock_mode", "")),
                fmt_ns(s.get("now_ns", 0)),
                client_text,
            ]
            line = pad(cells[0], widths[0]) + pad(cells[1], widths[1])
            line += pad(cells[2], widths[2]) + pad(cells[3], widths[3]) + cells[4]
            text += "│ " + line + " │\n"
        text += "│\n"
        footer = f" ⏎ {i18n.t('picker.connect', lang)}   Esc {i18n.t('picker.exit', lang)}"
        text += "│" + footer + " " * max(0, width - cell_len(footer) - 1) + "│\n"
        text += "└" + "─" * width + "┘"
        if self._reason:
            text = f" {self._reason}\n" + text
        self.query_one("#picker", Static).update(text)

    def action_cursor(self, direction: str) -> None:
        if not self._rows:
            return
        self._cursor = max(0, min(len(self._rows) - 1, self._cursor + (1 if direction == "down" else -1)))
        self._render_picker()

    async def action_connect(self) -> None:
        if not self._rows:
            return
        sid = self._rows[self._cursor].get("id")
        await self.app.connect(sid)

    def action_exit_app(self) -> None:
        self.app.exit()


def _client_short(client: str, lang: str) -> str:
    from ..log import source_label

    return source_label({"client": client, "kind": "scpi"}, lang)


class MessageScreen(Screen):
    """A full-screen message (token invalid, no sessions); Esc/q exits."""

    BINDINGS: ClassVar = [Binding("escape, q", "exit_app", show=False)]

    def __init__(self, text: str, code: int = 0) -> None:
        super().__init__()
        self._text = text
        self._code = code

    def compose(self) -> ComposeResult:
        yield Static(self._text, id="message")

    def on_mount(self) -> None:
        self.query_one("#message", Static).styles.margin = (2, 4)

    def action_exit_app(self) -> None:
        self.app.exit(self._code)


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no confirmation (tui-screens.md 10.1); the default focus is on cancel."""

    BINDINGS: ClassVar = [
        Binding("y", "confirm", show=False),
        Binding("n", "cancel", show=False),
        Binding("escape", "cancel", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        background: $background 60%;
        #confirm-box { width: 60; height: auto; border: thick $focus; padding: 1 2; }
        #confirm-body { margin-bottom: 1; }
    }
    """

    def __init__(self, title: str, body_lines: list[str], confirm: str, cancel: str) -> None:
        super().__init__()
        self._title = title
        self._body = body_lines
        self._confirm = confirm
        self._cancel = cancel

    def compose(self) -> ComposeResult:
        with Horizontal(id="confirm-box"):
            yield Label(f"━━ {self._title} " + "━" * 8, id="confirm-title")
            yield Static("\n".join(self._body), id="confirm-body")
            with Horizontal():
                yield Button(self._confirm, variant="primary", id="confirm-yes")
                yield Button(self._cancel, id="confirm-no")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


def confirm_rebuild(app: Any, callback: Callable[[bool], None]) -> None:
    """The rebuild confirmation, listing the connected scripts (D25, tui-screens.md 10.1)."""
    lang = app.lang
    clients = (app.session_info or {}).get("clients", [])
    lines = [i18n.t("confirm.rebuildBody", lang)]
    lines += [
        f"  · {c.get('instrument') or ''} {_client_short(c.get('client', ''), lang)}".rstrip()
        for c in clients
    ]
    app.push_screen(
        ConfirmScreen(
            i18n.t("confirm.rebuildTitle", lang),
            lines,
            i18n.t("confirm.rebuildConfirm", lang),
            i18n.t("common.cancel", lang),
        ),
        callback,
    )


def confirm_quit(app: Any, callback: Callable[[bool], None]) -> None:
    """The quit confirmation when scripts are connected (tui-screens.md 10.1)."""
    lang = app.lang
    clients = (app.session_info or {}).get("clients", [])
    lines = [i18n.t("confirm.quitBody", lang)]
    lines += [
        f"  · {c.get('instrument') or ''} {_client_short(c.get('client', ''), lang)}".rstrip()
        for c in clients
    ]
    app.push_screen(
        ConfirmScreen(
            i18n.t("confirm.quitTitle", lang),
            lines,
            i18n.t("confirm.quitConfirm", lang),
            i18n.t("common.cancel", lang),
        ),
        callback,
    )
