"""The runs screen (tui-screens.md 11.3): the run records list, a summary, and a replay that
reports the first difference. `R` opens it — unless the bench file changed, when `R` rebuilds
(7, D25); the bottom bar names whichever `R` will do.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Static

from .. import i18n
from ..fmt import fit, ralign
from ..widgets import box_title, style_of
from .shell import ShellScreen

#: The list's columns, in cells (11.3): the bench column takes the room that is left.
START_W, CLOCK_W, SEED_W, EVENTS_W, DURATION_W, SIZE_W = 19, 8, 5, 7, 10, 7
GAPS = 2 * 6


def fmt_run_duration(ns: int) -> str:
    """A run's virtual duration as the table shows it: ``4.210 s`` (11.3)."""
    return f"{int(ns) / 1e9:.3f} s"


def fmt_run_size(size: int) -> str:
    """Bytes on disk as the table shows them: ``3 KB``, ``212 KB``, ``1.4 MB``."""
    value = float(size)
    unit = "B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            break
        value /= 1024
    text = f"{value:.0f}" if unit == "B" else f"{value:.1f}".removesuffix(".0")
    return f"{text} {unit}"


def _columns(width: int) -> int:
    """The bench column: all the cells the other columns leave."""
    return max(8, width - (START_W + CLOCK_W + SEED_W + EVENTS_W + DURATION_W + SIZE_W + GAPS))


def _clock(run: dict[str, Any], lang: str) -> str:
    return i18n.t("runs.clock." + str(run.get("clock", "command")), lang)


def run_header(lang: str, width: int = 100) -> str:
    """The column titles, aligned with ``run_rows`` (11.3)."""
    bench = _columns(width)
    return "  ".join(
        [
            fit(i18n.t("runs.colStarted", lang), START_W),
            fit(i18n.t("runs.colBench", lang), bench),
            fit(i18n.t("runs.colClock", lang), CLOCK_W),
            ralign(i18n.t("runs.colSeed", lang), SEED_W),
            ralign(i18n.t("runs.colEvents", lang), EVENTS_W),
            ralign(i18n.t("runs.colDuration", lang), DURATION_W),
            ralign(i18n.t("runs.colSize", lang), SIZE_W),
        ]
    )


def run_rows(runs: list[dict[str, Any]], current_run: str | None, lang: str, width: int = 100) -> list[str]:
    """The list's lines, newest first (11.3); ``selection_text`` marks the live session.

    A pure function so the unit tier can verify the interface without an app.
    """
    bench_w = _columns(width)
    out = []
    for r in runs:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["started"]))
        out.append(
            "  ".join(
                [
                    fit(started, START_W),
                    fit(r.get("bench", ""), bench_w),
                    fit(_clock(r, lang), CLOCK_W),
                    ralign(str(r.get("seed", 0)), SEED_W),
                    ralign(str(r.get("events", 0)), EVENTS_W),
                    ralign(fmt_run_duration(int(r.get("duration_ns") or 0)), DURATION_W),
                    ralign(fmt_run_size(int(r.get("size") or 0)), SIZE_W),
                ]
            )
        )
    return out


def selection_text(run: dict[str, Any] | None, lang: str) -> str:
    """``▸ 2026-10-31 10:42:07 · 运行中（本会话）`` under the list (11.3)."""
    if run is None:
        return ""
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run["started"]))
    line = f"▸ {started}"
    if run.get("ended") is None:
        line += " · " + i18n.t("runs.liveSession", lang)
    return line


def first_mismatch_text(result: dict[str, Any], lang: str) -> str:
    """The replay's first difference, as the screen reports it (11.3)."""
    mismatches = result.get("mismatches") or []
    if not mismatches:
        return i18n.t(
            "runs.replayOk", lang, actions=result.get("actions", 0), replies=result.get("replies", 0)
        )
    m = mismatches[0]
    return i18n.t(
        "runs.firstMismatch",
        lang,
        seq=m.get("seq", 0),
        instrument=m.get("instrument", ""),
        command=m.get("command") or "",
        expected=(m.get("expected") or "")[:40],
        actual=(m.get("actual") or "")[:40],
    )


class RunsScreen(ShellScreen):
    """`R` (tui-screens.md 11.3)."""

    screen_key = "runs"

    AUTO_FOCUS = "#simubench-entry-no-focus"

    DEFAULT_CSS = """
    RunsScreen {
        #runs-box { height: 1fr; border: round $muted 60%; border-title-align: left; }
        #runs-list { padding: 0 1; }
        #runs-detail-box { display: none; height: auto; border: round $panel; padding: 0 1; }
    }
    """

    BINDINGS: ClassVar = [
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("enter", "summary", show=False),
        Binding("p", "replay", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.runs: list[dict[str, Any]] = []
        self.index = 0
        self.detail: dict[str, Any] | None = None
        self.replay: dict[str, Any] | None = None
        self._fetched_at = 0.0

    def screen_title(self) -> str:
        return i18n.t("runs.title", self.app.lang)

    def body(self) -> ComposeResult:
        with Vertical(id="runs-box"):
            yield Static("", id="runs-list")
        with Vertical(id="runs-detail-box"):
            yield Static("", id="runs-detail")

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        return [
            ("↑↓", i18n.t("keys.select", lang)),
            ("⏎", i18n.t("keys.summary", lang)),
            ("p", i18n.t("keys.replay", lang)),
            ("Esc", i18n.t("keys.back", lang)),
            ("q", i18n.t("common.quit", lang)),
        ]

    def refresh_content(self) -> None:
        super().refresh_content()
        # The screen polls four times a second; a run arrives at most every second, so the list
        # is refetched at that pace instead of four requests a second.
        if time.monotonic() - self._fetched_at >= 1.0:
            self._fetched_at = time.monotonic()
            self.run_worker(self._fetch(), exclusive=True)

    async def _fetch(self) -> None:
        app: Any = self.app
        try:
            self.runs = await app.client.runs(app.sid)
        except Exception:
            return
        self.index = min(self.index, max(0, len(self.runs) - 1))
        self._repaint()

    def _inner(self) -> int:
        return max(60, self.size.width - 4)  # the border and the list's padding

    def _repaint(self) -> None:
        from rich.text import Text

        if not self.is_mounted:  # a fetch can land after the screen was popped
            return

        app: Any = self.app
        lang = app.lang
        width = self._inner()
        lines = [run_header(lang, width), *run_rows(self.runs, None, lang, width)]
        text = Text()
        for n, line in enumerate(lines):
            if n:
                text.append("\n")
            style = (
                style_of(app, "muted") if n == 0 else style_of(app, "reverse" if n - 1 == self.index else "")
            )

            text.append(line, style=style)
        selected = self.runs[self.index] if self.runs else None
        if selected is not None:
            text.append("\n\n" + selection_text(selected, lang), style=style_of(app, "bold"))
        box = self.query_one("#runs-box", Vertical)
        box.border_title = box_title(i18n.t("runs.title", lang), str(len(self.runs)), width)
        self.query_one("#runs-list", Static).update(text)

        detail_box = self.query_one("#runs-detail-box", Vertical)
        parts: list[str] = []
        if self.detail is not None:
            summary = self.detail.get("summary", self.detail)
            parts.append(
                i18n.t(
                    "runs.summaryLine",
                    lang,
                    bench=summary.get("bench", ""),
                    events=summary.get("events", 0),
                    size=fmt_run_size(int(summary.get("size") or 0)),
                )
            )
        if self.replay is not None:
            parts.append(first_mismatch_text(self.replay, lang))
        detail_box.display = bool(parts)
        if parts:
            run = selected or {}
            started = (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run["started"])) if "started" in run else ""
            )
            key = "runs.replayTitle" if self.replay is not None else "runs.summaryTitle"
            detail_box.border_title = i18n.t(key, lang, started=started, bench=run.get("bench", ""))
        self.query_one("#runs-detail", Static).update("\n".join(parts))

    def action_cursor(self, direction: str) -> None:
        if not self.runs:
            return
        self.index = max(0, min(len(self.runs) - 1, self.index + (1 if direction == "down" else -1)))
        self._repaint()

    def action_summary(self) -> None:
        if not self.runs:
            return
        self.run_worker(self._fetch_detail(), exclusive=True)

    async def _fetch_detail(self) -> None:
        app: Any = self.app
        run = self.runs[self.index]
        try:
            self.detail = await app.client.run_detail(run["run_id"])
        except Exception:
            return
        self.replay = None
        self._repaint()

    def action_replay(self) -> None:
        if not self.runs:
            return
        self.run_worker(self._do_replay(), exclusive=True)

    async def _do_replay(self) -> None:
        app: Any = self.app
        run = self.runs[self.index]
        try:
            self.replay = await app.client.replay(run["run_id"])
        except Exception:
            return
        self._repaint()
