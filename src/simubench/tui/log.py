"""The communication log's row model (tui-screens.md 7.1).

One line per command: a client's ``scpi`` write merges with the reply read of the same
client + instrument (waiting up to 1 s of virtual time, or until that client's next write —
even across other events). An ``error`` event caused by the command merges into the line.
The engine emits the error *during* the command's processing, i.e. **before** the ``scpi``
write event, so errors are buffered and matched to the write by instrument and tick
(``t_start_ns``). Everything is a pure function of the event stream, unit-tested without a UI.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .fmt import cell_len, fmt, fmt_ns, fmt_seconds
from .i18n import t

#: A write waits this much virtual time for its reply before it shows ``·`` (tui-screens.md 7.1).
REPLY_WINDOW_NS = 1_000_000_000


@dataclass
class LogLine:
    """One row of the communication log (additive: later phases may carry more fields)."""

    seq: int  # seq of the line's first event (the write for a paired line)
    t_ns: int  # virtual time of the write
    wall: float | None = None  # real time of the write
    instrument: str = ""
    source: str = ""  # display label per tui-screens.md 1.5
    kind: str = "scpi"  # scpi | state | panel | fault | acquisition | advance | remote
    command: str = ""
    result: str = ""  # reply text (without the → prefix) or the error text
    result_kind: str = "none"  # none (·) | ok (→) | error (✗) | binary
    binary_size: int | None = None
    has_error: bool = False  # an error event merged into this line
    path: str | None = None  # state path, for a `state` line
    fault: str | None = None

    @property
    def time_text(self) -> str:
        return fmt_ns(self.t_ns)

    @property
    def is_error(self) -> bool:
        return self.result_kind == "error"

    def result_text(self, lang: str = "zh") -> str:
        """The result column's text: ``·``, ``→ reply``, ``✗ code message``, the binary summary."""
        if self.result_kind == "none":
            return t("log.noReply", lang)
        if self.result_kind == "binary":
            head = (self.result or "")[:12].rstrip("…")  # the display adds the ellipsis itself
            size = f"{self.binary_size or 0:,}".replace(",", " ")
            return t("log.binarySummary", lang, head=head + "…" if head else head, size=size)
        prefix = "✗ " if self.result_kind == "error" else "→ "
        return prefix + self.result


def _format_value(value: Any, lang: str) -> str:
    """A state value for the log: booleans read 开/On, numbers with a unit when obvious."""
    if isinstance(value, bool):
        return t("common.on" if value else "common.off", lang)
    if isinstance(value, float):
        return fmt(value)
    return str(value)


def source_label(ev: Mapping[str, Any], lang: str = "zh") -> str:
    """The ``client`` (or ``source``) as shown in the log (tui-screens.md 1.5)."""
    client = ev.get("client") or ev.get("source") or ""
    kind = ev.get("kind")
    if kind == "fault" and client == "bench":
        return t("source.bench", lang)
    if not client:
        return t("source.event", lang)
    if client in ("ui", "tui", "mcp", "pyvisa", "replay"):
        return t(f"source.{client}", lang)
    for scheme in ("socket:", "hislip:"):
        if client.startswith(scheme):
            host, _, port = client[len(scheme) :].rpartition(":")
            if host in ("", "127.0.0.1", "localhost"):
                return t("source.script_port", lang, port=port)
            return t("source.script_host_port", lang, host=host, port=port)
    return client


def _close_expired(open_writes: dict[tuple[str, str], LogLine], t_ns: int) -> None:
    """Writes older than the reply window can no longer receive their reply: they show ``·``."""
    for key, line in list(open_writes.items()):
        if line.t_ns + REPLY_WINDOW_NS <= t_ns:
            del open_writes[key]


def build_lines(
    events: Iterable[Mapping[str, Any]],
    lang: str = "zh",
    labels: Mapping[tuple[str, str], str] | None = None,
) -> list[LogLine]:
    """The log's rows from the session's events, in stream order.

    ``labels`` maps ``(instrument, state path)`` to the variable's display label for ``state``
    lines (``面板：<标签>``); without it the path's last segment is shown.
    """
    labels = labels or {}
    lines: list[LogLine] = []
    open_writes: dict[tuple[str, str], LogLine] = {}
    pending_errors: list[Mapping[str, Any]] = []

    def merge_error(line: LogLine, ev: Mapping[str, Any]) -> None:
        line.has_error = True
        line.result_kind = "error"
        line.result = f"{ev.get('code', '')} {ev.get('message', '')}".strip()
        line.fault = ev.get("fault")

    for ev in events:
        kind = ev.get("kind")
        t_ns = int(ev.get("t_ns", 0))
        _close_expired(open_writes, t_ns)
        if kind == "error":
            # The engine emits the error while processing the command, before its write event; the
            # spec words it as following the write. Both orders merge by instrument and tick.
            inst = ev.get("instrument") or ""
            open_line = next(
                (
                    line
                    for line in reversed(list(open_writes.values()))
                    if line.instrument == inst and line.t_ns == t_ns
                ),
                None,
            )
            if open_line is not None:
                merge_error(open_line, ev)
            else:
                pending_errors.append(ev)
            continue
        if kind == "scpi":
            client = ev.get("client") or ""
            inst = ev.get("instrument") or ""
            key = (client, inst)
            if ev.get("dir") == "write":
                open_writes.pop(key, None)  # the pair's previous write never got a reply: it shows ·
                start_ns = ev.get("t_start_ns")
                start_ns = t_ns if start_ns is None else int(start_ns)
                # An error raised while processing this command precedes the write event, same tick.
                err = next(
                    (
                        e
                        for e in pending_errors
                        if e.get("instrument") == inst and int(e.get("t_ns", 0)) in (start_ns, t_ns)
                    ),
                    None,
                )
                if err is not None:
                    pending_errors.remove(err)
                line = LogLine(
                    seq=int(ev.get("seq", 0)),
                    t_ns=start_ns,
                    wall=ev.get("wall"),
                    instrument=inst,
                    source=source_label(ev, lang),
                    command=ev.get("text") or f"<{ev.get('size', 0)} B binary>",
                    path=None,
                )
                if err is not None:
                    merge_error(line, err)
                lines.append(line)
                open_writes[key] = line
            else:  # read
                line = open_writes.pop(key, None)
                if line is not None:
                    if line.result_kind != "error":  # the error's ✗ wins over the reply text
                        line.result_kind = "binary" if ev.get("size") else "ok"
                        line.result = ev.get("text") or ""
                        line.binary_size = ev.get("size")
                else:
                    # A reply without its write in the window (the write expired, or an early connect).
                    line = LogLine(
                        seq=int(ev.get("seq", 0)),
                        t_ns=t_ns,
                        wall=ev.get("wall"),
                        instrument=inst,
                        source=source_label(ev, lang),
                        command="",
                        result_kind="binary" if ev.get("size") else "ok",
                        result=ev.get("text") or "",
                        binary_size=ev.get("size"),
                    )
                    lines.append(line)
            continue

        # Everything else is a fullscreen-only line (tui-screens.md 7.1).
        inst = ev.get("instrument") or ""
        source = source_label(ev, lang)
        base = dict(seq=int(ev.get("seq", 0)), t_ns=t_ns, instrument=inst, source=source)
        if kind == "state":
            path = ev.get("path") or ""
            label = labels.get((inst, path)) or (path.rsplit(".", 1)[-1] if path else "")
            lines.append(
                LogLine(
                    command=t("log.panel", lang, label=label),
                    result=f"{_format_value(ev.get('old'), lang)} → {_format_value(ev.get('new'), lang)}",
                    result_kind="ok",
                    path=path,
                    **base,
                )
            )
        elif kind == "panel":
            action = ev.get("action") or "run"
            lines.append(LogLine(command=t(f"log.panelAction.{action}", lang), **base))
        elif kind == "fault":
            status = ev.get("reason") or ev.get("source") or "active"
            key = "log.faultActive" if status in ("injected", "active", "pending") else "log.faultEnded"
            lines.append(
                LogLine(
                    command=t(key, lang, id=ev.get("fault", "")),
                    result=ev.get("message") or "",
                    result_kind="error" if status in ("injected", "active") else "none",
                    fault=ev.get("fault"),
                    **base,
                )
            )
        elif kind == "acquisition":
            lines.append(LogLine(command=t("log.acquisition", lang), **base))
        elif kind == "advance":
            dt = int(ev.get("dt_ns", 0)) / 1e9
            lines.append(LogLine(command=t("log.advance", lang, dt=fmt_seconds(dt)), **base))
        elif kind == "remote":
            action = ev.get("action")
            command = t("log.remoteLocal", lang) if action == "local" else t("log.remoteScript", lang)
            lines.append(LogLine(command=command, **base))
        # Other kinds (session, …) have no line in the log.

    return lines


def pending_writes(lines: list[LogLine]) -> list[LogLine]:
    """Writes still waiting for their reply (the D44 “执行命令” activity of the top bar)."""
    return [line for line in lines if line.kind == "scpi" and line.result_kind == "none"]


def visible(lines: list[LogLine], embedded: bool, instrument: str | None = None) -> list[LogLine]:
    """The lines one view shows: the embedded view only shows commands; screens may filter."""
    out = lines
    if embedded:
        out = [line for line in lines if line.kind == "scpi"]
    if instrument:
        out = [line for line in out if line.instrument == instrument]
    return out


def filter_lines(lines: list[LogLine], words: list[str], errors_only: bool = False) -> list[LogLine]:
    """The fullscreen filter: space-separated words, all must match (instrument, source, command
    text, ``error``); ``e`` shows only lines with an error."""
    out = lines
    if errors_only:
        out = [line for line in out if line.is_error]
    for w in words:
        wl = w.lower()
        out = [
            line
            for line in out
            if wl in line.instrument.lower()
            or wl in line.source.lower()
            or wl in line.command.lower()
            or (wl == "error" and line.is_error)
        ]
    return out


def row_cells(
    line: LogLine,
    widths: tuple[int, int, int, int] = (13, 12, 7, 26),
    virtual: bool = True,
) -> list[str]:
    """The fixed-width columns of a row (tui-screens.md 7.1): time, source, instrument, command."""
    time_text = fmt_ns(line.t_ns) if virtual else fmt_ns(0)  # real time rendering shares the column
    cells = [time_text, line.source, line.instrument, line.command]
    return [_pad_cell(c, w) for c, w in zip(cells, widths, strict=False)]


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
