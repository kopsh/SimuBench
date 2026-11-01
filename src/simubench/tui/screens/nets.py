"""The nets screen (tui-screens.md 11.1): every net's level on the left, the selected net's
probe on the right — level, fidelity, waveform, sources, lines, or why it reads 0 V.
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from .. import i18n
from ..cards import opposite_ends
from ..fmt import braille, fmt
from ..reasons import net_report
from .dialogs import ReasonScreen
from .shell import ShellScreen


def net_rows(session: dict[str, Any], levels: dict[str, Any], lang: str) -> tuple[list[dict[str, Any]], str]:
    """The left list's rows: one per net, in session order, plus the unconnected-ports line.

    Each row carries the net id, its endpoints (4.3's rendering), the RMS (a DC net's DC) and
    whether it carries a signal — everything the screen shows, so the unit tests can verify the
    interface without a running app.
    """
    level_of = {lv["net"]: lv for lv in (levels or {}).get("nets", [])}
    rows: list[dict[str, Any]] = []
    for net in (session or {}).get("nets", []):
        endpoints: list[str] = []
        for member in net.get("members") or []:
            for far in opposite_ends(member, session, lang):
                if far not in endpoints:
                    endpoints.append(far)
        lv = level_of.get(net.get("id", ""), {})
        rms = float(lv.get("rms") or 0.0)
        dc = float(lv.get("dc") or 0.0)
        active = bool(lv.get("active"))
        shown = rms if (active or not dc) else dc
        rows.append(
            {
                "net": net.get("id", ""),
                "endpoints": " · ".join(endpoints[:2]) + (" …" if len(endpoints) > 2 else ""),
                "value": fmt(shown, "V"),
                "active": active,
            }
        )
    unconnected: list[str] = []
    for inst in (session or {}).get("instruments", []):
        for port in inst.get("ports", []):
            if not port.get("net"):
                unconnected.append(f"{inst['name']}.{port['name']}")
    line = i18n.t("nets.unconnected", lang, ports=" · ".join(unconnected)) if unconnected else ""
    return rows, line


def silence_text(reasons: list[dict[str, Any]], lang: str) -> str:
    """Why the net reads 0 V, per 6.3's table."""
    out = []
    for r in reasons:
        kind = r.get("kind")
        text = {
            "unconnected": i18n.t("nets.silUnconnected", lang),
            "source_off": i18n.t("nets.silSourceOff", lang, source=r.get("source") or ""),
            "zero_setting": i18n.t("nets.silZero", lang, source=r.get("source") or ""),
            "no_source": i18n.t("nets.silNoSource", lang),
            "fault": i18n.t("nets.silFault", lang, fault_id=r.get("fault_id") or ""),
        }.get(kind, kind or "")
        out.append(text)
    return "；".join(out)


class NetsScreen(ShellScreen):
    """`n` (tui-screens.md 11.1)."""

    screen_key = "nets"

    AUTO_FOCUS = "#simubench-entry-no-focus"  # nothing on entry, like the instrument screen

    DEFAULT_CSS = """
    NetsScreen {
        #net-list-box { width: 46; border: round $panel; padding: 0 1; }
        #net-unconnected { color: $muted; }
        #net-detail-box { width: 1fr; margin-left: 1; border: round $panel; padding: 0 1; }
        #net-wave { height: 1fr; color: $text; }
    }
    """

    BINDINGS: ClassVar = [
        Binding("up", "cursor('up')", show=False),
        Binding("down", "cursor('down')", show=False),
        Binding("enter", "driver", show=False),
        Binding("w", "wave", show=False),
        Binding("question_mark", "reason", show=False),
        Binding("f", "fault", show=False),
    ]

    def __init__(self, net: str | None = None) -> None:
        super().__init__()
        self.selected = net
        self.probe: dict[str, Any] | None = None
        self.show_lines = False
        self._built = False
        self._last_version = -1
        self._probe_net: str | None = None

    def screen_title(self) -> str:
        return i18n.t("nets.title", self.app.lang)

    def body(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="net-list-box"):
                yield Static("", id="net-list")
                yield Static("", id="net-unconnected")
            with Vertical(id="net-detail-box"):
                yield Static("", id="net-detail-head")
                yield Static("", id="net-wave")
                yield Static("", id="net-detail-rest")

    def keys(self) -> list[tuple[str, str]]:
        lang = self.app.lang
        return [
            ("↑↓", i18n.t("keys.select", lang)),
            ("⏎", i18n.t("nets.keyDriver", lang)),
            ("w", i18n.t("nets.keyWave", lang)),
            ("?", i18n.t("keys.reason", lang)),
            ("f", i18n.t("keys.fault", lang)),
            ("Esc", i18n.t("keys.back", lang)),
            ("q", i18n.t("common.quit", lang)),
        ]

    # ---- data -------------------------------------------------------------------------------------------

    def _rows(self) -> tuple[list[dict[str, Any]], str]:
        return net_rows(self.app.session_info or {}, self.app.levels, self.app.lang)

    def refresh_content(self) -> None:
        super().refresh_content()
        app: Any = self.app
        if not app.session_info:
            return
        rows, unconnected = self._rows()
        nets = [r["net"] for r in rows]
        if nets and self.selected not in nets:
            self.selected = nets[0]
        self._render_list(rows, unconnected)
        self._render_detail()
        if not self._built:
            self._built = True
        if self.selected and self.selected != self._probe_net and app.conn == "connected" and app.sid:
            self._probe_net = self.selected
            self.run_worker(self._fetch_probe(), exclusive=True)

    async def _fetch_probe(self) -> None:
        app: Any = self.app
        try:
            probe = await app.client.probe(app.sid, self.selected)
        except Exception:
            return
        if self.selected != probe.get("net"):
            return
        self.probe = probe
        self._render_detail()

    # ---- rendering ---------------------------------------------------------------------------------------

    def _render_list(self, rows: list[dict[str, Any]], unconnected: str) -> None:
        app: Any = self.app
        lang = app.lang
        lines = [i18n.t("nets.header", lang)]
        for r in rows:
            cursor = "▸ " if r["net"] == self.selected else "  "
            mark = "●" if r["active"] else "○"
            lines.append(f"{cursor}{r['net']:<8} {r['endpoints']:<24} {r['value']:>10} {mark}")
        self.query_one("#net-list", Static).update("\n".join(lines))
        self.query_one("#net-unconnected", Static).update(unconnected)

    def _render_detail(self) -> None:
        app: Any = self.app
        lang = app.lang
        head = self.query_one("#net-detail-head", Static)
        wave = self.query_one("#net-wave", Static)
        rest = self.query_one("#net-detail-rest", Static)
        if not self.probe or self.probe.get("net") != self.selected:
            head.update("")
            wave.update("")
            rest.update("")
            return
        p = self.probe
        title = i18n.t("nets.detailTitle", lang, net=p["net"], members=" · ".join(p.get("members", [])[:3]))
        level = i18n.t(
            "nets.level",
            lang,
            dc=fmt(float(p.get("dc") or 0), "V"),
            rms=fmt(float(p.get("rms") or 0), "V"),
            peak=fmt(float(p.get("peak") or 0), "V"),
        )
        notes = ["≈ " + n for n in p.get("notes") or []]
        head.update(f"━ {title} " + "━" * 8 + "\n" + "\n".join([level, *notes]))
        rows: list[str] = []
        silence = silence_text(p.get("silence") or [], lang)
        if silence:
            rows.append(silence)
        for s in (p.get("sources") or [])[:4]:
            rows.append(
                i18n.t(
                    "nets.source",
                    lang,
                    source=s.get("source", ""),
                    rms=fmt(float(s.get("rms_contribution") or 0), "V"),
                )
            )
        lines = p.get("lines") or []
        if self.show_lines:
            for ln in lines[:16]:
                rows.append(
                    i18n.t(
                        "nets.line",
                        lang,
                        freq=fmt(float(ln.get("freq_hz") or 0), "Hz"),
                        amp=fmt(float(ln.get("amplitude") or 0), "V"),
                    )
                )
            wave.update("")
        else:
            wf = p.get("waveform") or {}
            cols = max(20, (app.size.width or 100) - 60)
            wave.update(braille(list(wf.get("volts") or []), cols))
            for ln in lines[:5]:
                rows.append(
                    i18n.t(
                        "nets.line",
                        lang,
                        freq=fmt(float(ln.get("freq_hz") or 0), "Hz"),
                        amp=fmt(float(ln.get("amplitude") or 0), "V"),
                    )
                )
        rest.update("\n".join(rows))

    # ---- keys ---------------------------------------------------------------------------------------------

    def action_cursor(self, direction: str) -> None:
        rows, _ = self._rows()
        nets = [r["net"] for r in rows]
        if not nets:
            return
        index = nets.index(self.selected) if self.selected in nets else 0
        self.selected = nets[max(0, min(len(nets) - 1, index + (1 if direction == "down" else -1)))]
        self.probe = None
        self._probe_net = None
        self.refresh_content()

    def action_driver(self) -> None:
        """⏎ opens the net's driver: the instrument behind the largest contribution (11.1)."""
        for s in (self.probe or {}).get("sources") or []:
            if s.get("instrument"):
                self.app.show("instrument", s["instrument"])
                return

    def action_wave(self) -> None:
        self.show_lines = not self.show_lines  # the fuller lines list vs the waveform (w)
        self._render_detail()

    def action_reason(self) -> None:
        app: Any = self.app
        if not self.probe:
            return
        report = net_report(self.probe, app.session_info or {}, app.store.events, app.lang)
        app.push_screen(ReasonScreen(report))

    def action_fault(self) -> None:
        from .faultdialog import FaultDialog

        self.app.push_screen(FaultDialog(net=self.selected))
