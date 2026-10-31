"""The screens' shared frame: top bar, content, bottom bar, and the minimum-size guard.

Every screen is its own module with its own bindings; T2/T3 add screens to the registry in
``screens/__init__.py`` without touching this base.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from .. import i18n
from ..widgets import BottomBar, TopBar, remote_status

MIN_WIDTH, MIN_HEIGHT = 80, 24


class ShellScreen(Screen):
    """Base class of all main screens. Subclasses implement ``body`` and ``refresh_content``."""

    DEFAULT_CSS = """
    ShellScreen {
        layout: vertical;
        #topbar { dock: top; }
        #bottombar { dock: bottom; }
        #minsize { display: none; width: 1fr; height: 1fr; content-align: center middle; }
        #minsize.-show { display: block; }
        .-too-small #body { display: none; }
        .-too-small #topbar { display: none; }
        .-too-small #bottombar { display: none; }
        #body { width: 1fr; height: 1fr; }
    }
    """

    BINDINGS: ClassVar = [Binding("escape", "back", "", show=False)]

    #: Registry key (tui-screens.md 2.1's screen names); T2/T3 screens add theirs.
    screen_key: str = ""

    def body(self) -> ComposeResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def screen_title(self) -> str:
        """The screen's name in the top bar."""
        return self.screen_key

    def keys(self) -> list[tuple[str, str]]:
        """The bottom bar's keys for the current focus and connection state."""
        return []

    def compose(self) -> ComposeResult:
        yield TopBar(id="topbar")
        from textual.containers import Vertical

        with Vertical(id="body"):
            yield from self.body()
        yield BottomBar(id="bottombar")
        yield Static("", id="minsize")

    def action_back(self) -> None:
        """Esc returns to the previous screen; the overview has nothing to return to (2.3)."""
        if self.screen_key != "overview" and self.app.screen is self:
            self.app.pop_screen()

    def _check_size(self) -> None:
        size = self.size
        small = size.width < MIN_WIDTH or size.height < MIN_HEIGHT
        self.set_class(small, "-too-small")
        guard = self.query_one("#minsize", Static)
        guard.set_class(small, "-show")
        if small:
            guard.update(i18n.t("minSize", self.app.lang, width=size.width, height=size.height))

    def _on_resize(self) -> None:
        self._check_size()
        self.refresh_content()

    def _poll(self) -> None:
        self.refresh_content()

    def on_mount(self) -> None:
        self._check_size()
        self.set_interval(0.15, self._poll)
        self.refresh_content()

    def refresh_content(self) -> None:
        """Re-render from the app's state; subclasses compare versions to skip work."""
        app: Any = self.app
        self.query_one(TopBar).update_bar(
            app.bench_name(),
            self.screen_title(),
            app.status_segments(),
            app.clock_mode(),
            app.now_ns(),
            app.lang,
            app.file_changed,
        )
        self.query_one(BottomBar).update_bar(self.keys(), remote_status(app.session_info, app.lang), app.lang)
        self.refresh()
