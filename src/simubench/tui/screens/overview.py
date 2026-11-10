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
from ..fmt import cell_len
from ..log import visible
from ..widgets import BottomBar, decorate_title, is_mono, log_text, render_segments, style_of
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
        margin-right: 2;
        border: round $line;
        padding: 0 0;
        border-title-align: left;
        border-title-color: $hud;
        border-title-style: bold;
    }
    Card:focus {
        border: heavy $focus;
        border-title-color: $focus;
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

        self.border_title = decorate_title(self.app, render_segments(self.app, self.card.title), tight=True)
        # size is the content area (the border is outside it): 30 columns for a 32-wide card.
        width = self.size.width or 30
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


#: Rows of the shut log drawer (tui-screens.md 4.5).
DRAWER_ROWS = 3


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
        Binding("enter", "open_card", show=False),
        Binding("w", "reason", show=False),
        Binding("p", "next_problem", show=False),
        Binding("L", "toggle_log", show=False),  # the drawer; lowercase l is the full-screen log
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_version: int = -1
        self._pending_focus: str | None = None  # a rebuild's focus not yet put back (#827)
        self._layout: tuple[int, tuple[str, ...]] | None = None  # columns + card names on screen
        self._log_rows = DRAWER_ROWS
        self._log_open = False  # the log is a drawer: shut, it shows the newest few rows
        self._seen_started = False
        self._errors_seen = 0  # error rows the user has had in view (drawer open, or at the start)

    def screen_title(self) -> str:
        return i18n.t("overview.screen", self.app.lang)

    def body(self) -> ComposeResult:
        self._strip = Static(id="attention")
        yield self._strip
        self._grid = VerticalScroll(id="cards")
        self._grid.can_focus = False
        yield self._grid
        self._log = Static(id="embedded-log")
        self._log_box = _LogBox(self._log)
        yield self._log_box

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        keys = [("1-9", i18n.t("keys.select", lang)), ("Tab", i18n.t("keys.move", lang))]
        focused = self.focused
        if isinstance(focused, Card):
            if self._is_instrument(focused.card.name):
                keys.append(("⏎", i18n.t("keys.open", lang)))
            else:
                keys.append(("⏎", i18n.t("keys.partNets", lang)))
        if self._cards_with_alerts():
            keys.append(("p", i18n.t("keys.problem", lang)))
        keys.append(("w", i18n.t("keys.reason", lang)))
        keys.append(("l", i18n.t("keys.log", lang)))
        keys.append(("L", i18n.t("keys.drawer", lang)))
        keys.append(("?", i18n.t("keys.help", lang)))
        keys.append(("q", i18n.t("common.quit", lang)))
        return keys

    def _is_instrument(self, name: str) -> bool:
        app: Any = self.app
        return any(i.get("name") == name for i in (app.session_info or {}).get("instruments", []))

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
        if session:
            cards = build_cards(
                session, app.readings, app.levels, app.screen_data, app.catalog, app.lang, app.faults
            )
            layout = (self._columns(), tuple(c.name for c in cards))
            if layout == self._layout and self._update_cards_in_place(cards):
                return
        focused = None
        if grid.children:
            # Only a focus that exists, or that a rebuild a moment ago has yet to put back: a focus
            # the user cleared stays cleared (#827), else ``f`` after a late refresh opened the dialog.
            card = self.focused
            focused = card.card.name if isinstance(card, Card) else self._pending_focus
        grid.remove_children()
        self._layout = None
        if not session:
            note = i18n.t("connect.screen", app.lang)
            if app.conn == "disconnected" or app.conn_attempts > 1:
                note += "\n" + i18n.t(
                    "connect.attempt", app.lang, n=app.conn_attempts, seconds=int(app._backoff)
                )
            grid.mount(Static(note, id="connecting-note"))
            return
        self._pad_rows(cards)
        self._alerting = [c for c in cards if c.alerts]
        self._refresh_strip(len(session.get("instruments", [])))
        columns = layout[0]
        for row_start in range(0, len(cards), columns):
            grid.mount(Horizontal(*[Card(c) for c in cards[row_start : row_start + columns]]))
        self._layout = layout
        self._pending_focus = focused
        grid.call_after_refresh(self._restore_focus, focused)

    def _pad_rows(self, cards: list[CardData]) -> None:
        columns = self._columns()
        for row_start in range(0, len(cards), columns):
            row = cards[row_start : row_start + columns]
            height = max(len(c.lines) for c in row)
            for c in row:
                c.lines += [[]] * (height - len(c.lines))

    def _update_cards_in_place(self, cards: list[CardData]) -> bool:
        """New data for the same cards in the same places: change the cards, do not rebuild them.
        The focus never leaves a card that stays, so there is nothing to put back — the rebuild's
        window, in which a late callback revived a focus the user had cleared (#827, #849) or
        dropped the one they had (#852), does not exist (#864)."""
        on_screen = list(self._grid.query(Card))
        if len(on_screen) != len(cards):
            return False
        self._pad_rows(cards)
        self._alerting = [c for c in cards if c.alerts]
        app: Any = self.app
        self._refresh_strip(len((app.session_info or {}).get("instruments", [])))
        for widget, data in zip(on_screen, cards, strict=True):
            widget.card = data
            widget.render_card()
        return True

    def _cards_with_alerts(self) -> list[Card]:
        return [c for c in self._grid.query(Card) if c.card.alerts]

    def _refresh_strip(self, instruments: int) -> None:
        """The attention strip (one row above the cards): every alert of every card, worst
        first, so a trip does not wait to be found card by card. Quiet when all is well."""
        from rich.text import Text

        app: Any = self.app
        width = max(20, app.size.width)
        alerting = getattr(self, "_alerting", [])
        if not alerting:
            self._strip.update(
                render_segments(
                    app, [(" " + i18n.t("attention.clear", app.lang, count=instruments), "muted")]
                )
            )
            return
        text = Text()
        badge = "bold reverse" if is_mono(app) else f"bold {style_of(app, 'bar')} on {style_of(app, 'bad')}"
        text.append(i18n.t("attention.badge", app.lang), style=badge)
        items = sorted(
            ((c, a) for c in alerting for a in c.alerts),
            key=lambda ca: {"fault": 0, "limit": 1, "error": 2}[ca[1].kind],
        )
        for card, alert in items:
            piece = f"  {card.name} "
            if text.cell_len + cell_len(piece) + cell_len(alert.text) > width - 2:
                text.append(" …", style=style_of(app, "muted"))
                break
            text.append(piece, style=style_of(app, "muted"))
            text.append(alert.text, style=style_of(app, alert.tone))
        self._strip.update(text)

    def action_next_problem(self) -> None:
        """``p``: focus the next card with an alert, wrapping round (tui-screens.md 4.4)."""
        cards = list(self._grid.query(Card))
        alerting = [i for i, c in enumerate(cards) if c.card.alerts]
        if not alerting:
            return
        focused = next((i for i, c in enumerate(cards) if c is self.focused), -1)
        target = next((i for i in alerting if i > focused), alerting[0])
        cards[target].focus()

    def action_reason(self) -> None:
        """``w``: why the focused instrument is in the state it is in (tui-screens.md 9.1)."""
        from ..reasons import explain_instrument

        app: Any = self.app
        focused = self.focused
        info = None
        if isinstance(focused, Card):
            info = next(
                (
                    i
                    for i in (app.session_info or {}).get("instruments", [])
                    if i.get("name") == focused.card.name
                ),
                None,
            )
        report = (
            explain_instrument(
                info, app.readings.get(info["name"]), app.store.events, app.faults, app.lines, app.lang
            )
            if info
            else None
        )
        if report is None:
            self.query_one("#bottombar", BottomBar).flash(i18n.t("attention.noReason", app.lang))
            return
        app.show_reason(report)

    def _restore_focus(self, name: str | None) -> None:
        if name == self._pending_focus:
            self._pending_focus = None
        if name and self.focused is None:  # never take a focus someone has set since (#852)
            for card in self._grid.query(Card):
                if card.card.name == name:
                    card.focus()
                    return

    def _drawer_rows(self) -> int:
        """Rows of the log's box: a few when shut (the cards get the height), the full block
        (4.1) when open."""
        if not self._log_open:
            return DRAWER_ROWS
        return max(6, min(16, self.app.size.height - 24))

    def _refresh_log(self) -> None:
        app: Any = self.app
        lang = app.lang
        self._log_rows = self._drawer_rows()
        lines = visible(app.lines, embedded=True)
        shown = lines[-50:]
        errors = sum(1 for line in lines if line.has_error)
        if self._log_open or not self._seen_started:
            self._errors_seen = errors  # what the user can see, or the state at the start
            self._seen_started = True
        title = i18n.t("log.title", lang)
        count = i18n.t("log.count", lang, count=len(lines))
        fresh = errors - self._errors_seen
        if fresh > 0:  # a shut drawer says so; it never opens by itself
            count = i18n.t("log.newErrors", lang, count=fresh) + " · " + count
        inner = max(20, app.size.width - 4)  # border 2 + the rows' padding 2
        self._log_box.border_title = _box_title(title, count, inner)
        self._log_box.styles.height = self._log_rows + 2
        self._log.update(log_text(app, shown, lang, width=inner))
        self._log_box.scroll_end(animate=False, force=True)

    def action_toggle_log(self) -> None:
        """``L``: open or shut the log drawer."""
        self._log_open = not self._log_open
        self._refresh_log()
        self.refresh_content()

    # ---- keys ---------------------------------------------------------------------------------------

    def action_card(self, n: int) -> None:
        cards = list(self._grid.query(Card))
        if 1 <= n <= len(cards):
            cards[n - 1].focus()

    def action_open_card(self) -> None:
        """``⏎`` on a focused card (tui-screens.md 4.4): an instrument opens its screen; a part
        opens the nets screen on its first net (2.3)."""
        focused = self.focused
        if not isinstance(focused, Card):
            return
        name = focused.card.name
        app: Any = self.app
        if not self._is_instrument(name):
            for part in (app.session_info or {}).get("parts", []):
                if part.get("name") == name:
                    for port in part.get("ports", []):
                        if port.get("net"):
                            app.show("nets", port["net"])
                            return
            app.show("nets")
            return
        app.open_instrument(name)

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
        """``floor((W + 2) / 34)`` for the screen width W (tui-screens.md 4.1)."""
        return max(1, (self.app.size.width + 2) // 34)

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
        border: round $line;
        border-title-color: $hud;
        height: auto;
        border-title-align: left;
        scrollbar-size-vertical: 0;
    }
    """

    def __init__(self, log: Widget) -> None:
        super().__init__(log)
        self.can_focus = False
