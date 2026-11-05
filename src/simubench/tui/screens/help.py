"""The key-help overlay (``?``): every key the focused screen and the app bind, with what it does.

The rows are read from the real ``BINDINGS`` of the screen (and its bases) and the app, so the
help cannot drift from the keys. A binding whose action has no wording in ``help.actions`` is a
test failure (``tests/test_tui_help.py``), not a silent omission.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import i18n

#: Textual key names → what the keyboard shows.
KEY_TEXT = {
    "question_mark": "?",
    "plus": "+",
    "minus": "-",
    "colon": ":",
    "slash": "/",
    "enter": "⏎",
    "space": "␣",
    "escape": "Esc",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
    "tab": "Tab",
    "shift+tab": "Shift+Tab",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "end": "End",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
}


def action_name(action: str) -> str:
    """``cursor('up')`` → ``cursor``; ``open('log')`` → ``open_log``; ``step(-1)`` → ``step``."""
    match = re.match(r"(?:app\.)?(\w+)(?:\((.*)\))?$", action)
    if not match:
        return action
    name, arg = match.groups()
    return f"{name}_{arg.strip(chr(39))}" if name == "open" and arg else name


def bindings_of(owner: Any) -> list[Binding]:
    """The bindings declared by a screen or app class and its bases, base-most last."""
    out: list[Binding] = []
    for cls in (owner if isinstance(owner, type) else type(owner)).__mro__:
        if not cls.__module__.startswith("simubench"):  # Textual's own bases bind their own keys
            continue
        for b in cls.__dict__.get("BINDINGS", []):
            if isinstance(b, Binding):
                out.append(b)
    return out


def help_rows(screen: Any, app: Any) -> list[tuple[str, str]]:
    """``(keys, what it does)`` rows: the screen's own keys first, then the app's."""
    lang = app.lang
    grouped: dict[str, list[str]] = {}
    for b in [*bindings_of(screen), *bindings_of(app)]:
        name = action_name(b.action)
        if name == "focus_previous":  # shown with focus_next as one "Tab / Shift+Tab" row
            continue
        keys = grouped.setdefault(name, [])
        for key in (k.strip() for k in b.key.split(",")):
            text = KEY_TEXT.get(key, key)
            if text not in keys:
                keys.append(text)
    rows: list[tuple[str, str]] = []
    for name, keys in grouped.items():
        if name == "card":
            keys = ["1–9"]
        elif name == "cursor":
            keys = ["↑", "↓", "←", "→"]
        elif name == "focus_next":
            keys = ["Tab", "Shift+Tab"]
        text = i18n.t(f"help.actions.{name}", lang)
        rows.append((" ".join(keys) if name == "cursor" else " / ".join(keys), text))
    return rows


class HelpScreen(ModalScreen[None]):
    """``?`` — the keys of the screen underneath; ``Esc`` or ``?`` closes it."""

    BINDINGS: ClassVar = [
        Binding("escape", "close", show=False),
        Binding("question_mark", "close", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $background 70%; }
    #help-box { width: 64; max-width: 96%; height: auto; max-height: 90%;
                border: round $focus; background: $background; padding: 0 2; }
    #help-title { color: $focus; text-style: bold; margin-bottom: 1; }
    #help-foot { color: $muted; margin-top: 1; }
    """

    def __init__(self, title: str, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self._rows = rows

    def compose(self) -> ComposeResult:
        from rich.text import Text

        width = max((len(k) for k, _ in self._rows), default=0)
        body = Text()
        for n, (keys, what) in enumerate(self._rows):
            if n:
                body.append("\n")
            body.append(keys.ljust(width + 2), style="bold")
            body.append(what)
        with Vertical(id="help-box"):
            yield Static(self._title, id="help-title")
            yield Static(body, id="help-body")
            yield Static(i18n.t("help.foot", self.app.lang), id="help-foot")  # type: ignore[attr-defined]

    def action_close(self) -> None:
        self.dismiss(None)
