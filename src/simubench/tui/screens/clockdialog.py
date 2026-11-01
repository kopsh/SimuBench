"""Advancing the virtual clock (tui-screens.md 11.2): one input line, a duration, ⏎.

Command and stepped clocks step by the typed amount (the default is the previous value of this
run); the wall clock walks on its own and only says so. When scripts are connected the advance
disturbs them, so it confirms and carries `force` (D32).
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import i18n
from .dialogs import HistoryInput


def parse_duration(text: str) -> float | None:
    """`10 ms`, `1 s`, `500 us`, `1.5` (seconds) → seconds; None when unparsable or not positive.

    The unit tier's interface: the dialog accepts what an engineer types, SI prefixes included.
    """
    raw = text.strip().lower().replace("µs", "us")
    if not raw:
        return None
    number, unit = "", ""
    for ch in raw:
        if ch.isdigit() or ch in ".+-e":
            number += ch
        elif ch.isalpha():
            unit += ch
        elif ch != " ":
            return None
    try:
        value = float(number)
    except ValueError:
        return None
    factor = {"": 1.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "m": 60.0, "min": 60.0}.get(unit)
    if factor is None:
        return None
    out = value * factor
    return out if out > 0 else None


class AdvanceDialog(ModalScreen[float]):
    """`a` under the command or stepped clock: 推进虚拟时间 [ 1 s ]."""

    AUTO_FOCUS = "#fd-advance-input"

    DEFAULT_CSS = """
    AdvanceDialog {
        align: center middle;
        background: $background 60%;
        #fd-advance-box { width: 56; height: auto; border: thick $focus; padding: 1 2; }
        #fd-advance-title { color: $focus; margin-bottom: 1; }
        #fd-advance-row { height: auto; }
        #fd-advance-row Label { width: 20; color: $muted; }
        #fd-advance-input { width: 1fr; }
    }
    """

    def __init__(self, default: str = "1 s") -> None:
        super().__init__()
        self._default = default

    def compose(self) -> ComposeResult:
        lang = self.app.lang
        with Vertical(id="fd-advance-box"):
            yield Static(i18n.t("clock.advanceTitle", lang), id="fd-advance-title")
            with Horizontal(id="fd-advance-row"):
                yield Static(i18n.t("clock.advanceLabel", lang), id="fd-advance-label")
                yield HistoryInput(id="fd-advance-input")
            yield Static(i18n.t("clock.advanceHint", lang), id="fd-advance-hint")

    def on_mount(self) -> None:
        self.query_one("#fd-advance-input", HistoryInput).value = self._default

    async def _submit(self, raw: str) -> None:
        seconds = parse_duration(raw)
        if seconds is None:
            self.query_one("#fd-advance-hint", Static).update(i18n.t("clock.advanceInvalid", self.app.lang))
            return
        self.dismiss(seconds)

    def on_input_submitted(self, event: Any) -> None:
        if event.input.id == "fd-advance-input":
            self.run_worker(self._submit(event.value), exclusive=True)

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.dismiss(None)


def format_duration(seconds: float) -> str:
    """Seconds back as the engineer typed them (``1 s``, ``10 ms``) — the dialog's next default."""
    if seconds >= 1:
        return f"{seconds:g} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:g} ms"
    if seconds >= 1e-6:
        return f"{seconds * 1e6:g} us"
    return f"{seconds * 1e9:g} ns"
