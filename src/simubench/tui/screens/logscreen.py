"""The fullscreen communication log (``l``, tui-screens.md 7.2): filter, errors-only, follow."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from .. import i18n
from ..log import filter_lines
from ..widgets import log_text
from .shell import ShellScreen


class LogScreen(ShellScreen):
    """通信: every event kind, one line per command, newest at the bottom, following."""

    screen_key = "log"

    BINDINGS: ClassVar = [
        Binding("slash", "open_filter", show=False),
        Binding("e", "errors_only", show=False),
        Binding("t", "toggle_time", show=False),
        Binding("end", "follow", show=False),
        Binding("up", "scroll('up')", show=False),
        Binding("down", "scroll('down')", show=False),
        Binding("pageup", "scroll('pageup')", show=False),
        Binding("pagedown", "scroll('pagedown')", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_version = -1
        self._following = True
        self._new_count = 0
        self._seen_count = 0
        self._filter_text = ""
        self._filter_open = False
        self._errors_only = False
        self._virtual = True

    def screen_title(self) -> str:
        return i18n.t("log.title", self.app.lang)

    def body(self) -> ComposeResult:
        self._box = Vertical(Static(id="log-rows"), id="log-box")
        self._box.can_focus = False
        self._input = Input(id="log-filter")
        self._input.display = False
        self._input.can_focus = False  # focusable only while the filter is open
        yield Vertical(self._box, self._input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self._input:
            return
        self._filter_text = event.value.strip()
        self._close_filter()
        self._last_version = -1
        self.refresh_content()

    def _close_filter(self) -> None:
        self._input.display = False
        self._input.can_focus = False
        self._filter_open = False
        self.set_focus(None)

    def on_key(self, event) -> None:
        """Esc inside the filter input clears the filter (tui-screens.md 7.2)."""
        if event.key == "escape" and self._input.has_focus:
            self._close_filter()

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        return [
            ("/", i18n.t("keys.filter", lang)),
            ("e", i18n.t("keys.errorsOnly", lang)),
            ("End", i18n.t("keys.follow", lang)),
            ("Esc", i18n.t("keys.back", lang)),
            ("q", i18n.t("common.quit", lang)),
        ]

    def refresh_content(self) -> None:
        super().refresh_content()
        app: Any = self.app
        if app.data_version == self._last_version:
            return
        self._last_version = app.data_version
        self._render_log()

    def _render_log(self) -> None:
        app: Any = self.app
        lang = app.lang
        inner = max(20, app.size.width - 4)  # border 2 + the rows' padding 2
        lines = filter_lines(app.lines, self._filter_text.split(), self._errors_only)
        if self._following:
            self._new_count = 0
            self._seen_count = len(app.lines)
        else:
            # New records since the user scrolled away (a rough count: all lines newer than before).
            self._new_count = max(0, len(app.lines) - self._seen_count)
        title = i18n.t("log.title", lang)
        right = i18n.t("log.countAll", lang, count=len(lines))
        if self._new_count:
            right = i18n.t("log.newRecords", lang, count=self._new_count)
        self._box.border_title = _full_title(title, right, inner, lang)
        rows = self._box.query_one("#log-rows", Static)
        rows.update(
            log_text(
                app,
                lines,
                lang,
                width=inner,
                virtual=self._virtual,
                header=True,
                older_available=bool(app.store.dropped),
            )
        )
        if self._following:
            self._box.scroll_end(animate=False, force=True)
        self._input.display = self._filter_open
        if self._filter_open:
            self._input.placeholder = i18n.t("log.filterHint", lang)

    # ---- keys ---------------------------------------------------------------------------------------

    def action_open_filter(self) -> None:
        self._filter_open = True
        self._input.display = True
        self._input.can_focus = True
        self._input.focus()

    def action_errors_only(self) -> None:
        self._errors_only = not self._errors_only
        self._last_version = -1
        self.refresh_content()

    def action_toggle_time(self) -> None:
        self._virtual = not self._virtual
        self._last_version = -1
        self.refresh_content()

    def action_follow(self) -> None:
        self._following = True
        self._new_count = 0
        self._box.scroll_end(animate=False, force=True)
        self._last_version = -1
        self.refresh_content()

    def action_scroll(self, direction: str) -> None:
        self._following = False
        if direction == "up":
            self._box.scroll_up(animate=False)
        elif direction == "down":
            self._box.scroll_down(animate=False)
        elif direction == "pageup":
            self._box.scroll_page_up(animate=False)
        else:
            self._box.scroll_page_down(animate=False)

    def action_back(self) -> None:
        if self._filter_open or self._filter_text:
            self._filter_text = ""
            self._close_filter()
            self._last_version = -1
            self.refresh_content()
            return
        super().action_back()


def _full_title(title: str, right: str, width: int, lang: str) -> str:
    """The log box's border title: ``通信 ───── 10 条 · 全部仪表`` (tui-screens.md 7.2)."""
    from ..fmt import cell_len

    suffix = "" if not right else f" {right} "
    total = max(0, width - cell_len(title) - cell_len(suffix) - 10)
    return f" {title} " + "─" * total + suffix
