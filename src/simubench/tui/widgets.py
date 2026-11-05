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
LOG_WIDTHS = (13, 14, 7, 26)


def style_of(app: Any, tokens: str) -> str:
    """Resolve semantic tokens (``muted bad``) to concrete rich styles from the theme."""
    variables = app.get_css_variables()
    out = []
    for token in tokens.split():
        value = variables.get(token)
        if value == "ansi_default":
            value = "default"  # rich's name for the terminal's own colour (the mono theme)
        if token == "chip":  # the amber badge: dark text on the focus colour
            value = f"bold {variables.get('bar', 'black')} on {variables.get('focus', 'yellow')}"
            value = "bold reverse" if variables.get("focus") == "ansi_default" else value
        out.append(value if value else (token if token in ("bold", "reverse", "dim") else ""))
    return " ".join(s for s in out if s)


def render_segments(app: Any, segments: list[tuple[str, str]], base: str = "") -> Text:
    """One line's segments as rich Text with resolved styles."""
    text = Text(style=base)
    for value, tokens in segments:
        text.append(value, style=style_of(app, tokens))
    return text


def is_mono(app: Any) -> bool:
    """``NO_COLOR``: the tokens carry no colour, so the bars fall back to reverse video."""
    return getattr(app, "theme", "") == "simubench-mono"


def decorate_title(app: Any, title: Any, tight: bool = False) -> Text:
    """A box title between amber brackets (``▌ awg · SDG2042X ▐``): the HUD's mark of a panel.
    ``tight`` keeps only the left bracket, for the 32-column cards whose border holds 26 cells
    of title — a model name must not end in ``…`` for a mark."""
    mark = style_of(app, "focus")
    text = Text()
    text.append("▌" if tight else "▌ ", style=mark)
    text.append_text(title) if isinstance(title, Text) else text.append(str(title).strip())
    if not tight:
        text.append(" ▐", style=mark)
    return text


def bar_text(app: Any, left: list[tuple[str, str]], right: list[tuple[str, str]], width: int) -> Text:
    """One bar row: ``left`` and ``right`` segments ``(text, tokens)`` with a gap between, drawn
    as HUD bars on ``$bar`` — or, under ``NO_COLOR``, as the reverse-video strip the spec began
    with (1.1)."""
    mono = is_mono(app)
    bar = "reverse" if mono else f"on {style_of(app, 'bar')}"

    def styled(tokens: str) -> str:
        if mono:  # no colour to carry: reverse video, and bold where the token asks for it
            return "reverse bold" if "bold" in tokens.split() else "reverse"
        if "chip" in tokens:  # the badge brings its own background
            return style_of(app, tokens)
        return f"{bar} {style_of(app, tokens)}".strip()

    text = Text(style=bar)
    for value, tokens in left:
        text.append(value, style=styled(tokens))
    right_w = sum(cell_len(v) for v, _ in right)
    gap = max(1, width - text.cell_len - right_w)
    text.append(" " * gap, style=bar)
    for value, tokens in right:
        text.append(value, style=styled(tokens))
    if text.cell_len < width:
        text.append(" " * (width - text.cell_len), style=bar)
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
        left: list[tuple[str, str]] = (
            [(" SimuBench ", "bold")] if is_mono(self.app) else [(" SimuBench ", "chip")]
        )
        left.append((f" — {bench_name} | ", "muted"))
        left.append((screen_name, "hud"))
        right_parts: list[tuple[str, str]] = []
        if file_changed:
            right_parts.append((i18n.t("topbar.fileChanged", lang) + "  ", "focus"))
        right_parts.extend(status_segments)
        if clock_mode and now_ns is not None:
            right_parts.append(
                (" · " + i18n.t(f"clockMode.{clock_mode}", lang) + f" · t = {fmt_ns(now_ns)} ", "")
            )
        self.update(bar_text(self.app, left, right_parts, self.size.width or 80))


class BottomBar(Static):
    """The focused screen's keys on the left; the remote status on the right (tui-screens.md 2.2)."""

    DEFAULT_CSS = """
    BottomBar { width: 1fr; height: 1; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._last: tuple[list[tuple[str, str]], str, str] = ([], "", "zh")
        self._flash_text: str | None = None

    def update_bar(self, keys: list[tuple[str, str]], remote_text: str, lang: str) -> None:
        self._last = (keys, remote_text, lang)
        self._repaint()

    def flash(self, text: str, seconds: float = 3.0) -> None:
        """One transient message in place of the keys (11.2's 墙钟 hint), then back (2.2)."""
        self._flash_text = text
        self.set_timer(seconds, self._clear_flash)
        self._repaint()

    def _clear_flash(self) -> None:
        self._flash_text = None
        self._repaint()

    def _repaint(self) -> None:
        keys, remote_text, _lang = self._last
        width = self.size.width or 80
        left: list[tuple[str, str]] = [(" ", "")]
        if self._flash_text:
            left.append((self._flash_text, ""))
            remote_text = ""
        else:
            for key, action in keys:
                left.append((key, "bold hud"))
                left.append((f" {action}  ", "muted"))
        self.update(bar_text(self.app, left, [(remote_text + " ", "muted")], width))


class CaptionBar(Static):
    """The scripted demo's subtitle, docked above the bottom bar (tui-screens.md 6).

    Hidden until the first caption; the right side names the key or command the viewer is
    watching (`key ▸ enter`, `script ▸ :SYSTem:ERRor?`).
    """

    DEFAULT_CSS = """
    CaptionBar {
        height: auto;
        display: none;
        padding: 0 1;
        background: $background;
    }
    CaptionBar.-show { display: block; }
    """

    def set_caption(self, text: str, right: str = "") -> None:
        from rich.text import Text

        t = Text(text)
        if right:
            t.append("    " + right, style="bold")
        self.update(t)
        self.add_class("-show")
        self.screen.refresh(layout=True)  # showing a docked widget makes the body give up a row
        # Screens that size their boxes from the body's height redraw once the row is gone.
        self.screen.call_after_refresh(getattr(self.screen, "_relayout", lambda: None))


def remote_status(session: dict[str, Any] | None, lang: str) -> str:
    """``远程：无``, ``远程：smu ← 脚本 :53122``, or ``远程：2 台`` (tui-screens.md 2.2)."""
    prefix = "remote: "
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
    hints: bool = False,
    highlight_seq: int | None = None,
) -> Text:
    """The log's rows as rich Text (tui-screens.md 7.1): fixed columns, semantic colors.

    Time and source are muted; the command in the foreground; the result colored by kind
    (``·`` muted, ``→`` foreground, ``✗`` bad). ``width`` is the box's inner width. ``hints``
    renders the not-executed wording under the line, indented (the fullscreen view, 9.2).
    ``highlight_seq`` reverses one row: the command the reason dialog jumped to (9.1).
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
    from .log import hint_text

    for line in lines:
        time_cell = fmt_ns(line.t_ns) if virtual else _wall_cell(line)
        base = "reverse" if (highlight_seq is not None and line.seq == highlight_seq) else ""
        text.append(
            _pad_cell(time_cell, LOG_WIDTHS[0]) + _pad_cell(line.source, LOG_WIDTHS[1]),
            style=(base + " " + muted).strip(),
        )
        text.append(_pad_cell(line.instrument, LOG_WIDTHS[2]), style=base)
        text.append(_pad_cell(line.command, LOG_WIDTHS[3]), style=base)
        result_style = base
        if line.result_kind == "error":
            result_style = (base + " " + style_of(app, "bad")).strip()
        elif line.result_kind == "none":
            result_style = (base + " " + muted).strip()
        text.append(_pad_cell(line.result_text(lang), max(0, width - sum(LOG_WIDTHS))), style=result_style)
        text.append("\n")
        if hints:
            hint = hint_text(line, lang)
            if hint:
                text.append("  " + hint, style=muted)
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


def box_text(app: Any, lines: list[list[tuple[str, str]]], width: int) -> Text:
    """A box's fixed-width lines as rich Text: segments per line, padded to ``width`` cells.

    The last segment of an overflowing line is truncated with … (as the overview cards do).
    """
    from .fmt import cell_len, fit

    text = Text()
    for n, line in enumerate(lines):
        if n:
            text.append("\n")
        used = 0
        for value, tokens in line:
            room = width - used
            if room <= 0:
                break
            if cell_len(value) > room:
                text.append(fit(value, room), style=style_of(app, tokens))
                used = width
                break
            text.append(value, style=style_of(app, tokens))
            used += cell_len(value)
        if width - used > 0:
            text.append(" " * (width - used))
    return text


def box_title(title: str, right: str, width: int) -> str:
    """``┌─ 错误 ───── 队列 2 ─┐``'s title row: title left, counter right."""
    suffix = f" {right} " if right else ""
    total = max(0, width - cell_len(title) - cell_len(suffix) - 6)
    return f" {title} " + "─" * total + suffix
