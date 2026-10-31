"""The overview screen (tui-screens.md 4): card grid, embedded communication log.

T1 binds the digit keys and focus movement; `⏎` / `␣` / `?` / `f` / `n` arrive with T2/T3.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from .. import i18n
from ..cards import CardData, build_cards
from ..log import visible
from ..widgets import log_text, render_segments
from .shell import ShellScreen


def _truncate(text: str, room: int) -> str:
    """Cut ``text`` to ``room`` cells, ending with … when something was cut."""
    from ..fmt import cell_len

    if cell_len(text) <= room:
        return text
    out, cells = "", 0
    for ch in text:
        w = cell_len(ch)
        if cells + w > room - 1:
            return out + "…" + " " * max(0, room - cells - 1)
        out += ch
        cells += w
    return out


class Card(Static):
    """One overview card: 32 columns, focusable, amber border while focused (4.1)."""

    DEFAULT_CSS = """
    Card {
        width: 32;
        height: auto;
        border: round $muted 60%;
        padding: 0 0;
        border-title-align: left;
    }
    Card:focus {
        border: round $focus;
    }
    """
    can_focus = True

    def __init__(self, card: CardData) -> None:
        super().__init__()
        self.card = card

    def render_card(self) -> None:
        """Set the border title and body from the card data; long lines truncate with … (4.2)."""
        from rich.text import Text

        from ..fmt import cell_len
        from ..widgets import style_of

        self.border_title = render_segments(self.app, self.card.title)
        width = max(2, self.size.width - 2) if self.size.width else 30
        text = Text()
        for line in self.card.lines:
            used = 0
            for value, tokens in line:
                room = width - used
                if room <= 0:
                    break
                if cell_len(value) > room:  # the line's last visible segment carries the …
                    text.append(_truncate(value, room), style=style_of(self.app, tokens))
                    used = width
                    break
                text.append(value, style=style_of(self.app, tokens))
                used += cell_len(value)
            if width - used > 0:
                text.append(" " * (width - used))
            text.append("\n")
        self.update(text)

    def on_mount(self) -> None:
        self.render_card()

    def _on_resize(self) -> None:
        self.render_card()


class OverviewScreen(ShellScreen):
    """总览: a card grid on top, the embedded communication log below (tui-screens.md 4)."""

    screen_key = "overview"

    BINDINGS: ClassVar = [
        *[Binding(str(n), f"card({n})", show=False) for n in range(1, 10)],
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("left", "cursor('left')", show=False),
        Binding("right", "cursor('right')", show=False),
        Binding("tab", "focus_next", show=False),
        Binding("shift+tab", "focus_previous", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_version: int = -1
        self._focus_name: str | None = None
        self._log_rows = 8

    def screen_title(self) -> str:
        return i18n.t("overview.screen", self.app.lang)

    def body(self) -> ComposeResult:
        self._grid = VerticalScroll(id="cards")
        self._grid.can_focus = False
        yield self._grid
        self._log = Static(id="embedded-log")
        self._log_box = _LogBox(self._log)
        yield self._log_box

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        keys = [("1-9", i18n.t("keys.select", lang)), ("Tab", i18n.t("keys.move", lang))]
        keys.append(("l", i18n.t("keys.log", lang)))
        keys.append(("q", i18n.t("common.quit", lang)))
        return keys

    def refresh_content(self) -> None:
        super().refresh_content()
        app: Any = self.app
        version = app.data_version
        if version == self._last_version:
            return
        self._last_version = version
        self._rebuild_cards()
        self._refresh_log()

    def _rebuild_cards(self) -> None:
        app: Any = self.app
        session = app.session_info
        grid = self._grid
        focused = None
        if grid.children:
            focused = next((c.card.name for c in grid.query(Card) if c.has_focus), self._focus_name)
        grid.remove_children()
        if not session:
            grid.mount(Static(i18n.t("connect.screen", app.lang), id="connecting-note"))
            return
        cards = build_cards(session, app.readings, app.levels, app.screen_data, app.catalog, app.lang)
        columns = (
            max(1, (grid.size.width + 2) // 34) if grid.size.width else max(1, (app.size.width + 2) // 34)
        )
        for row_start in range(0, len(cards), columns):
            row = cards[row_start : row_start + columns]
            height = max(len(c.lines) for c in row)
            for c in row:
                c.lines += [[]] * (height - len(c.lines))
            grid.mount(Horizontal(*[Card(c) for c in row]))
        grid.call_after_refresh(self._restore_focus, focused)

    def _restore_focus(self, name: str | None) -> None:
        if name:
            for card in self._grid.query(Card):
                if card.card.name == name:
                    card.focus()
                    return

    def _refresh_log(self) -> None:
        app: Any = self.app
        lang = app.lang
        self._log_rows = max(6, min(16, app.size.height - 24))
        lines = visible(app.lines, embedded=True)
        shown = lines[-50:]
        title = i18n.t("log.title", lang)
        count = i18n.t("log.count", lang, count=len(lines))
        inner = max(20, app.size.width - 2)
        self._log_box.border_title = _box_title(title, count, inner)
        self._log_box.styles.height = self._log_rows + 2
        self._log.update(log_text(app, shown, lang, width=inner))
        self._log_box.scroll_end(animate=False, force=True)

    # ---- keys ---------------------------------------------------------------------------------------

    def action_card(self, n: int) -> None:
        cards = list(self._grid.query(Card))
        if 1 <= n <= len(cards):
            cards[n - 1].focus()

    def action_cursor(self, direction: str) -> None:
        cards = list(self._grid.query(Card))
        if not cards:
            return
        focused = self.focused
        if focused is None or not isinstance(focused, Card):
            cards[0].focus()
            return
        columns = self._columns()
        index = next(i for i, c in enumerate(cards) if c is focused)
        row, col = divmod(index, columns)
        if direction == "left":
            col = max(0, col - 1)
        elif direction == "right":
            col = min(columns - 1, col + 1)
        elif direction == "up":
            row = max(0, row - 1)
        elif direction == "down":
            row = min((len(cards) - 1) // columns, row + 1)
        target = min(row * columns + col, len(cards) - 1)
        cards[target].focus()

    def _columns(self) -> int:
        return max(1, (self._grid.size.width + 2) // 34) if self._grid.size.width else 3

    def action_focus_next(self) -> None:
        cards = list(self._grid.query(Card))
        if not cards:
            return
        focused = self.focused
        index = next((i for i, c in enumerate(cards) if c is focused), -1)
        cards[(index + 1) % len(cards)].focus()

    def action_focus_previous(self) -> None:
        cards = list(self._grid.query(Card))
        if not cards:
            return
        focused = self.focused
        index = next((i for i, c in enumerate(cards) if c is focused), 0)
        cards[(index - 1) % len(cards)].focus()


def _box_title(title: str, right: str, width: int) -> str:
    """``┌─ 通信 ───── 6 条 ─┐``'s title row: title left, count right (rendered by the border)."""
    from ..fmt import cell_len

    # A margin of 6 keeps the title shorter than the border on any terminal width.
    total = max(0, width - cell_len(title) - cell_len(right) - 10)
    return f" {title} " + "─" * total + f" {right} "


class _LogBox(VerticalScroll):
    """The embedded log's box: the Static inside carries the rows; the border carries the title."""

    DEFAULT_CSS = """
    _LogBox {
        border: round $muted 60%;
        height: auto;
        border-title-align: left;
    }
    """

    def __init__(self, log: Widget) -> None:
        super().__init__(log)
        self.can_focus = False
