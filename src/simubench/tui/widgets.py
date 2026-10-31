"""Shared widgets: the top bar, the bottom bar, cards, and the log rendering (tui-screens.md 2).

All of them read the semantic tokens from the app's theme variables (tui-screens.md 1.2), so
``NO_COLOR`` strips the colors in one place.
"""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual.widgets import Static

from . import i18n
from .fmt import cell_len, fmt_ns
from .log import LogLine

#: Column widths of the log rows (tui-screens.md 7.1): time, source, instrument, command.
LOG_WIDTHS = (13, 12, 7, 26)


def style_of(app: Any, tokens: str) -> str:
    """Resolve semantic tokens (``muted bad``) to concrete rich styles from the theme."""
    variables = app.get_css_variables()
    out = []
    for token in tokens.split():
        value = variables.get(token)
        out.append(value if value else (token if token in ("bold", "reverse", "dim") else ""))
    return " ".join(s for s in out if s)


def render_segments(app: Any, segments: list[tuple[str, str]], base: str = "") -> Text:
    """One line's segments as rich Text with resolved styles."""
    text = Text(style=base)
    for value, tokens in segments:
        text.append(value, style=style_of(app, tokens))
    return text


class TopBar(Static):
    """``SimuBench — <bench> | <screen>`` left; activity, clock mode and virtual time right."""

    DEFAULT_CSS = """
    TopBar { width: 1fr; height: 1; }
    """

    def update_bar(
        self,
        bench_name: str,
        screen_name: str,
        status_segments: list[tuple[str, str]],
        clock_mode: str | None,
        now_ns: int | None,
        lang: str,
        file_changed: bool = False,
    ) -> None:
        left = f"SimuBench — {bench_name} | {screen_name}"
        right_parts: list[tuple[str, str]] = []
        if file_changed:
            right_parts.append((i18n.t("topbar.fileChanged", lang) + "  ", "focus"))
        right_parts.extend(status_segments)
        if clock_mode and now_ns is not None:
            right_parts.append(
                (" · " + i18n.t(f"clockMode.{clock_mode}", lang) + f" · t = {fmt_ns(now_ns)}", "")
            )
        width = self.size.width or 80
        right_w = sum(cell_len(v) for v, _ in right_parts)
        left_w = cell_len(left)
        text = Text(style="reverse")
        text.append(left, style="reverse")
        gap = max(1, width - left_w - right_w)
        text.append(" " * gap, style="reverse")
        for value, tokens in right_parts:
            text.append(value, style=f"reverse {style_of(self.app, tokens)}".strip())
        if left_w + gap + right_w < width:
            text.append(" " * (width - left_w - gap - right_w), style="reverse")
        self.update(text)


class BottomBar(Static):
    """The focused screen's keys on the left; the remote status on the right (tui-screens.md 2.2)."""

    DEFAULT_CSS = """
    BottomBar { width: 1fr; height: 1; }
    """

    def update_bar(self, keys: list[tuple[str, str]], remote_text: str, lang: str) -> None:
        _ = lang
        width = self.size.width or 80
        left = "  ".join(f"{key} {action}" for key, action in keys)
        right = remote_text
        text = Text(style="reverse")
        left_w = cell_len(left)
        right_w = cell_len(right)
        text.append(left, style="reverse")
        gap = max(1, width - left_w - right_w)
        text.append(" " * gap, style="reverse")
        text.append(right, style="reverse")
        if left_w + gap + right_w < width:
            text.append(" " * (width - left_w - gap - right_w), style="reverse")
        self.update(text)


def remote_status(session: dict[str, Any] | None, lang: str) -> str:
    """``远程：无``, ``远程：smu ← 脚本 :53122``, or ``远程：2 台`` (tui-screens.md 2.2)."""
    prefix = "远程：" if i18n.table(lang) is i18n.LANGS["zh"] else "remote: "
    clients = (session or {}).get("clients", [])
    if not clients:
        return prefix + i18n.t("common.none", lang)
    if len(clients) == 1:
        c = clients[0]
        inst = c.get("instrument")
        label = _client_label(c.get("client", ""), lang)
        return f"{prefix}{inst} ← {label}" if inst else f"{prefix}{label}"
    return prefix + i18n.t("picker.clientsN", lang, count=len(clients))


def _client_label(client: str, lang: str) -> str:
    from .log import source_label

    return source_label({"client": client, "kind": "scpi"}, lang)


def log_text(
    app: Any,
    lines: list[LogLine],
    lang: str,
    *,
    width: int,
    virtual: bool = True,
    header: bool = False,
    older_available: bool = False,
) -> Text:
    """The log's rows as rich Text (tui-screens.md 7.1): fixed columns, semantic colors.

    Time and source are muted; the command in the foreground; the result colored by kind
    (``·`` muted, ``→`` foreground, ``✗`` bad). ``width`` is the box's inner width.
    """
    text = Text()
    if header:
        titles = [
            i18n.t("log.colTimeVirtual" if virtual else "log.colTimeReal", lang),
            i18n.t("log.colSource", lang),
            i18n.t("log.colInstrument", lang),
            i18n.t("log.colCommand", lang),
            i18n.t("log.colReply", lang),
        ]
        text.append(_row(titles, width), style=style_of(app, "muted"))
        text.append("\n")
    if older_available:
        text.append(i18n.t("log.olderInRuns", lang), style=style_of(app, "muted"))
        text.append("\n")
    muted = style_of(app, "muted")
    for line in lines:
        time_cell = fmt_ns(line.t_ns) if virtual else _wall_cell(line)
        text.append(_pad_cell(time_cell, LOG_WIDTHS[0]) + _pad_cell(line.source, LOG_WIDTHS[1]), style=muted)
        text.append(_pad_cell(line.instrument, LOG_WIDTHS[2]))
        text.append(_pad_cell(line.command, LOG_WIDTHS[3]))
        result_style = ""
        if line.result_kind == "error":
            result_style = style_of(app, "bad")
        elif line.result_kind == "none":
            result_style = muted
        text.append(_pad_cell(line.result_text(lang), max(0, width - sum(LOG_WIDTHS))), style=result_style)
        text.append("\n")
    return text


def _wall_cell(line: LogLine) -> str:
    from .fmt import fmt_wall

    return fmt_wall(line.wall) if line.wall else "—"


def _pad_cell(text: str, width: int) -> str:
    if cell_len(text) <= width:
        return text + " " * (width - cell_len(text))
    out, cells = "", 0
    for ch in text:
        w = cell_len(ch)
        if cells + w > width - 1:
            return out + "…"
        out += ch
        cells += w
    return out


def _row(cells: list[str], width: int) -> str:
    out = _pad_cell(cells[0], LOG_WIDTHS[0]) + _pad_cell(cells[1], LOG_WIDTHS[1])
    out += _pad_cell(cells[2], LOG_WIDTHS[2]) + _pad_cell(cells[3], LOG_WIDTHS[3])
    return out + _pad_cell(cells[4], max(0, width - sum(LOG_WIDTHS)))


def clock_activity(app: Any, lang: str, mode: str | None, busy: bool) -> list[tuple[str, str]]:
    """The D44 activity status: 等待命令 / 执行命令 / 时钟走动, with its dot's color."""
    if mode == "wall":
        return [("● ", "ok"), (i18n.t("activity.wall", lang), "")]
    if busy:
        return [("● ", "ok"), (i18n.t("activity.busy", lang), "")]
    return [("● ", "muted"), (i18n.t("activity.idle", lang), "")]


def seconds_until(ts: float) -> int:
    return max(0, round(ts - time.monotonic()))
