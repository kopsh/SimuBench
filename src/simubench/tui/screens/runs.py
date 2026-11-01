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
from ..fmt import fmt_ns
from .shell import ShellScreen


def run_rows(runs: list[dict[str, Any]], current_run: str | None, lang: str) -> list[str]:
    """The list's lines: newest first, the current session's run marked 运行中 (11.3).

    A pure function so the unit tier can verify the interface without an app.
    """
    out = []
    for r in runs:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["started"]))
        live = r.get("ended") is None
        marker = i18n.t("runs.live", lang) if live or r["run_id"] == current_run else ""
        out.append(
            i18n.t(
                "runs.row",
                lang,
                started=started,
                bench=r.get("bench", ""),
                clock=i18n.t("clockMode." + r.get("clock", "command"), lang),
                seed=r.get("seed", 0),
                events=r.get("events", 0),
                duration=fmt_ns(int(r.get("duration_ns") or 0))[:-4] or "0.000 s",
                marker=marker,
            )
        )
    return out


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
        #runs-list { width: 1fr; height: 1fr; }
        #runs-detail { display: none; height: auto; border-top: round $panel; padding-top: 1; }
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

    def screen_title(self) -> str:
        return i18n.t("runs.title", self.app.lang)

    def body(self) -> ComposeResult:
        with Vertical():
            yield Static("", id="runs-list")
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
        self.run_worker(self._fetch(), exclusive=True)

    async def _fetch(self) -> None:
        app: Any = self.app
        try:
            runs = await app.client.runs(app.sid)
        except Exception:
            return
        self.runs = runs
        self._render()

    def _render(self) -> None:
        app: Any = self.app
        lang = app.lang
        rows = run_rows(self.runs, None, lang)
        header = i18n.t("runs.header", lang)
        lines = [header] if not rows else [header, *rows]
        self.query_one("#runs-list", Static).update("\n".join(lines))
        detail = self.query_one("#runs-detail", Static)
        parts: list[str] = []
        if self.detail is not None:
            s = self.detail
            parts.append(
                i18n.t(
                    "runs.summaryLine",
                    lang,
                    bench=s.get("bench", ""),
                    events=s.get("summary", s).get("events", 0) if "summary" in s else s.get("events", 0),
                    size=s.get("summary", s).get("size", 0) if "summary" in s else s.get("size", 0),
                )
            )
        if self.replay is not None:
            parts.append(first_mismatch_text(self.replay, lang))
        detail.update("\n".join(parts))
        detail.display = bool(parts)

    def action_cursor(self, direction: str) -> None:
        if not self.runs:
            return
        self.index = max(0, min(len(self.runs) - 1, self.index + (1 if direction == "down" else -1)))
        self._render()

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
        self._render()

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
        self._render()
