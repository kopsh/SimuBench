"""The SimuBench terminal console app: connection state machine, data refresh, screen registry.

The TUI is one client of the served HTTP/WebSocket interface (D18). All state lives here; the
screens re-render from it on a short poll, which keeps cross-task messaging out of the picture.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App
from textual.binding import Binding

from . import i18n, theme
from .client import ApiClient, ApiError, stream_events, ws_url
from .log import LogLine, build_lines
from .store import EventStore
from .widgets import clock_activity

#: The screens one can open (tui-screens.md 2.3). T2/T3 register theirs here, e.g.
#: ``register_screen("instrument", InstrumentScreen)`` — the app only needs ``show(name)``.
SCREENS: dict[str, Callable[[], Any]] = {}


def register_screen(key: str, factory: Callable[[], Any]) -> None:
    SCREENS[key] = factory


def build_labels(session: dict[str, Any] | None) -> dict[tuple[str, str], str]:
    """(instrument, state path) → display label, for the log's ``面板：<标签>`` lines."""
    out: dict[tuple[str, str], str] = {}
    for info in (session or {}).get("instruments", []):
        for sv in info.get("state", []):
            label = sv.get("label") or sv.get("label_en") or sv["path"].rsplit(".", 1)[-1]
            out[(info["name"], sv["path"])] = label
    return out


def _hash_file(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class SimuBenchApp(App[None]):
    """``simubench tui``: overview, communication log, and the connection states of section 3."""

    CSS = """
    Screen { background: $background; scrollbar-size-vertical: 0; scrollbar-size-horizontal: 0; }
    Static#connecting-note { padding: 1 2; color: $muted; }
    Static#message { color: $fg; }
    Static#embedded-log { padding: 0 1; }
    #log-box { border: round $muted 60%; border-title-align: left; }
    #log-rows { padding: 0 1; }
    #cards { height: 1fr; overflow-y: auto; scrollbar-size-vertical: 0; }
    #cards Horizontal { height: auto; align: left top; overflow-x: hidden; }
    #embedded-log { scrollbar-size-vertical: 0; }
    #log-filter { display: none; border: round $focus; }
    """

    BINDINGS: ClassVar = [
        Binding("q", "quit_app", "", show=False),
        Binding("l", "open('log')", "", show=False),
        Binding("R", "rebuild", "", show=False),
    ]

    def __init__(
        self,
        url: str,
        sid: str | None = None,
        token: str | None = None,
        lang: str | None = None,
        launched: bool = False,
        bench_path: Path | None = None,
        bench_name: str = "SimuBench",
    ) -> None:
        super().__init__()
        self.url = url.rstrip("/")
        self.sid = sid
        self.token = token
        self.lang = lang or i18n.default_lang()
        self.launched = launched  # the TUI started this service: quit closes it, R rebuilds
        self.bench_path = bench_path
        self._bench_sha: str | None = None
        self._bench_name = bench_name
        self.client = ApiClient(url, token)
        self.store = EventStore()
        self.session_info: dict[str, Any] | None = None
        self.levels: dict[str, Any] | None = None
        self.readings: dict[str, dict[str, Any]] = {}
        self.screen_data: dict[str, dict[str, Any]] = {}  # per-instrument /screen payloads
        self.catalog: dict[str, Any] = {}
        self.clock: dict[str, Any] | None = None
        self.lines: list[LogLine] = []
        self.labels: dict[tuple[str, str], str] = {}
        #: connecting | connected | disconnected | token-invalid | session-gone
        self.conn = "connecting"
        self.conn_attempts = 0
        self.retry_at = 0.0
        self.retry_seconds = 0
        self.file_changed = False
        self.data_version = 0
        self.reason: str | None = None  # why the picker came up again (session closed)
        self._stop = asyncio.Event()
        self._conn_task: asyncio.Task | None = None
        self._backoff = 1.0
        self._dirty_instr: set[str] = set()
        self._dirty_levels = False
        self._dirty_lines = False
        self._dirty_clock = False
        self._maintaining = False
        self._maint_task: asyncio.Task | None = None
        self._session_task: asyncio.Task | None = None
        self._picker_task: asyncio.Task | None = None
        self._catalog_loaded = False
        # The theme's variables ($muted, $focus, …) must exist before the CSS parses.
        theme.apply_theme(self)

    # ---- startup ------------------------------------------------------------------------------------

    async def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)
        self.set_interval(1.0, self._tick_wall)
        self.set_interval(2.0, self._tick_bench)
        await self.client.open()
        if self.sid:
            self.push_overview()
            self._bench_sha = _hash_file(self.bench_path)
            self._conn_task = asyncio.create_task(self._connection_loop())
        else:
            await self._attach_flow()

    async def _attach_flow(self) -> None:
        """``--attach``: one session connects directly, several show the picker, none a message."""
        from .screens.states import MessageScreen

        try:
            sessions = await self.client.sessions()
        except ApiError as exc:
            if exc.status == 401:
                self.conn = "token-invalid"
                self.push_screen(MessageScreen(i18n.t("connect.tokenInvalid", self.lang), code=2))
                return
            self.push_screen(
                MessageScreen(
                    i18n.t("connect.unreachable", self.lang, url=self.url, error=exc.message), code=2
                )
            )
            return
        if len(sessions) == 1:
            await self.connect(sessions[0]["id"])
            return
        if not sessions:
            self.push_screen(MessageScreen(i18n.t("connect.noSessions", self.lang)))
            return
        await self._show_picker()

    async def _show_picker(self, reason: str | None = None) -> None:
        """Show the session picker, replacing whatever main screen is on top (tui-screens.md 3)."""
        from .screens.states import PickerScreen

        self.sid = None
        self.reason = reason or self.reason
        while len(self.screen_stack) > 1:  # keep the app's default screen at the bottom
            self.pop_screen()
        if not isinstance(self.screen, PickerScreen):
            self.push_screen(PickerScreen(self.url, reason=self.reason))
        self.reason = None

    def push_overview(self) -> None:
        from .screens.overview import OverviewScreen

        self.push_screen(OverviewScreen())

    async def connect(self, sid: str) -> None:
        """Leave the picker (or start up) for a session: show the overview and connect."""
        from .screens.overview import OverviewScreen
        from .screens.states import PickerScreen

        self.sid = sid
        self.conn = "connecting"
        if isinstance(self.screen, PickerScreen):
            self.pop_screen()  # the picker leaves the stack
        self.push_screen(OverviewScreen())
        if self._conn_task is None or self._conn_task.done():
            self._conn_task = asyncio.create_task(self._connection_loop())

    def show(self, key: str) -> None:
        """Push a registered screen; no-op when it is already on top (the registry, section 2.3)."""
        factory = SCREENS.get(key)
        if factory is None:
            return
        current = self.screen
        if type(current) is factory:
            return
        self.push_screen(factory())

    # ---- connection loop ----------------------------------------------------------------------------

    async def _connection_loop(self) -> None:
        while not self._stop.is_set() and self.sid is not None:
            self.conn_attempts += 1
            try:
                await self._full_refresh()
                self.conn = "connected"
                self._backoff = 1.0
                self.bump()
                await stream_events(
                    ws_url(self.url, self.sid, self.token),
                    self._on_event,
                    self._on_ws_open,
                    self._on_ws_close,
                    self._stop,
                )
                if self._stop.is_set():
                    return
            except ApiError as exc:
                if exc.status == 401:
                    self.conn = "token-invalid"
                    self.bump()
                    return
                if exc.status == 404:
                    # The session is gone (closed elsewhere): back to the picker with a reason (section 3).
                    self.conn = "session-gone"
                    self.bump()
                    self.call_after_refresh(self._back_to_picker)
                    return
            self.conn = "disconnected"
            self.retry_seconds = max(1, int(self._backoff))
            self.retry_at = time.monotonic() + self._backoff
            self.bump()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._backoff)
                return
            except TimeoutError:
                pass
            self._backoff = min(self._backoff * 2, 10.0)
            self.conn = "connecting"
            self.bump()

    def _on_ws_open(self) -> None:
        self.conn = "connected"
        self.bump()

    def _on_ws_close(self, code: int | str) -> None:
        if code == 4401:
            self.conn = "token-invalid"
            self._stop.set()
            self.bump()
            return
        if code == 4404:
            self.conn = "session-gone"
            self._stop.set()
            self.bump()
            self.call_after_refresh(self._back_to_picker)
            return
        # Anything else: the loop backs off and reconnects (the store dedupes the replay).

    def _back_to_picker(self) -> None:
        self._picker_task = asyncio.create_task(self._show_picker(i18n.t("connect.gone", self.lang)))

    # ---- events and data refresh ----------------------------------------------------------------------

    def _on_event(self, ev: dict[str, Any]) -> None:
        if not self.store.add(ev):
            return
        kind = ev.get("kind")
        if kind in ("scpi", "state", "error") and ev.get("instrument"):
            self._dirty_instr.add(ev["instrument"])
            self._dirty_clock = True
        if kind == "state":
            self._dirty_levels = True
        if kind in ("session", "remote"):
            self._session_task = asyncio.create_task(self._refresh_session())
        self._dirty_lines = True
        self.bump()

    async def _full_refresh(self) -> None:
        """Everything the screens need: session, readings, screens, levels, clock (section 1.6)."""
        assert self.sid is not None
        if not self._catalog_loaded:
            try:
                docs = await self.client.catalog()
                self.catalog = {d["type"]: d for d in docs}
                self._catalog_loaded = True
            except ApiError:
                pass
        self.session_info = await self.client.session(self.sid)
        self.labels = build_labels(self.session_info)
        self._mark_template_instruments()
        await self._fetch_instruments(self._dirty_instr)
        with contextlib.suppress(ApiError):
            self.levels = await self.client.levels(self.sid)
            self.clock = await self.client.clock(self.sid)
        self._dirty_levels = False
        self._dirty_lines = True
        self.bump()

    async def _refresh_session(self) -> None:
        """Session/remote events: the client list and the instruments' remote state changed."""
        if self.sid is None:
            return
        try:
            self.session_info = await self.client.session(self.sid)
            self.labels = build_labels(self.session_info)
        except ApiError:
            return
        self._dirty_lines = True
        self.bump()

    def _mark_template_instruments(self) -> None:
        """Readings for the templates that show them (smu, multimeter, calibrator) and /screen
        for the oscilloscopes' run state (section 4.2)."""
        if self.session_info is None:
            return
        for info in self.session_info.get("instruments", []):
            template = (info.get("display") or {}).get("template")
            if template in ("smu", "multimeter", "calibrator", "oscilloscope"):
                self._dirty_instr.add(info["name"])

    def _tick(self) -> None:
        if self._maintaining:
            return
        if not (self._dirty_instr or self._dirty_levels or self._dirty_lines or self._dirty_clock):
            return
        self._maintaining = True

        async def run() -> None:
            try:
                await self._maintenance()
            finally:
                self._maintaining = False

        self._maint_task: asyncio.Task | None = None
        self._session_task: asyncio.Task | None = None
        self._picker_task: asyncio.Task | None = None
        self._catalog_loaded = False

        self._maint_task = asyncio.create_task(run())

    async def _maintenance(self) -> None:
        """Debounced fetches: per-instrument after its events, levels after state, lines rebuild."""
        if self.sid is None:
            return
        if self._dirty_instr:
            names, self._dirty_instr = self._dirty_instr, set()
            await self._fetch_instruments(names)
        if self._dirty_levels:
            self._dirty_levels = False
            with contextlib.suppress(ApiError):
                self.levels = await self.client.levels(self.sid)
        if self._dirty_clock:
            self._dirty_clock = False
            with contextlib.suppress(ApiError):
                self.clock = await self.client.clock(self.sid)
        if self._dirty_lines:
            self._dirty_lines = False
            self.lines = build_lines(self.store.events, self.lang, self.labels)
        self.bump()

    async def _fetch_instruments(self, names: set[str]) -> None:
        if self.sid is None:
            return
        for name in names:
            try:
                info = await self.client.instrument(self.sid, name)
            except ApiError:
                continue
            self._merge_instrument(info)
            template = (info.get("display") or {}).get("template")
            if template in ("smu", "multimeter", "calibrator"):
                with contextlib.suppress(ApiError):
                    self.readings[name] = await self.client.readings(self.sid, name)
            if template == "oscilloscope":
                with contextlib.suppress(ApiError):
                    self.screen_data[name] = await self._screen(name)

    async def _screen(self, name: str) -> dict[str, Any]:
        assert self.sid is not None
        r = await self.client.http.get(f"/api/sessions/{self.sid}/instruments/{name}/screen")
        if r.status_code != 200:
            raise ApiError(r.status_code, r.text)
        return r.json()  # type: ignore[no-any-return]

    def _merge_instrument(self, info: dict[str, Any]) -> None:
        if self.session_info is None:
            return
        instruments = self.session_info.setdefault("instruments", [])
        for i, existing in enumerate(instruments):
            if existing.get("name") == info.get("name"):
                instruments[i] = info
                break

    def bump(self) -> None:
        self.data_version += 1

    # ---- timers -------------------------------------------------------------------------------------

    async def _tick_wall(self) -> None:
        """Wall clock: time, levels and readings refresh every second (section 1.6, D44)."""
        if self.clock and self.clock.get("mode") == "wall" and self.sid and self.conn == "connected":
            with contextlib.suppress(ApiError):
                self.clock = await self.client.clock(self.sid)
                self.levels = await self.client.levels(self.sid)
                self.session_info = await self.client.session(self.sid)
                self.labels = build_labels(self.session_info)
                self._mark_template_instruments()
            self.bump()

    def _tick_bench(self) -> None:
        """D25: the launched bench file is hashed; a change lights the top bar's rebuild hint."""
        if self.bench_path is None:
            return
        sha = _hash_file(self.bench_path)
        if sha is not None and sha != self._bench_sha:
            self.file_changed = True
            self.bump()

    # ---- top bar data ---------------------------------------------------------------------------------

    def bench_name(self) -> str:
        if self.session_info:
            return self.session_info.get("bench_name") or self._bench_name
        return self._bench_name

    def clock_mode(self) -> str | None:
        return (self.clock or {}).get("mode")

    def now_ns(self) -> int | None:
        return (self.clock or {}).get("now_ns")

    def status_segments(self) -> list[tuple[str, str]]:
        """The right side of the top bar: the D44 activity, or a connection state (tui-screens.md 2.1)."""
        lang = self.lang
        if self.conn == "token-invalid":
            return [("✕ ", "bad"), (i18n.t("connect.tokenInvalid", lang), "bad")]
        if self.conn == "session-gone":
            return [("✕ ", "bad"), (i18n.t("connect.gone", lang), "bad")]
        if self.conn == "connecting":
            return [("◌ ", "focus"), (i18n.t("topbar.connecting", lang, url=self.url), "focus")]
        if self.conn == "disconnected":
            remaining = max(0, round(self.retry_at - time.monotonic()))
            return [("✕ ", "bad"), (i18n.t("topbar.disconnected", lang, seconds=remaining), "bad")]
        events = self.store.events
        busy = bool(events) and events[-1].get("kind") == "scpi" and events[-1].get("dir") == "write"
        return clock_activity(self, lang, self.clock_mode(), busy)

    # ---- quit and rebuild -----------------------------------------------------------------------------

    def action_quit_app(self) -> None:
        """``q``: when this process serves the bench, confirm first if scripts are connected (10.1)."""
        from .screens.states import ConfirmScreen, confirm_quit

        if isinstance(self.screen, ConfirmScreen):
            self.exit()  # asked twice: quit without asking again
            return
        if self.launched and (self.session_info or {}).get("clients"):
            confirm_quit(self, lambda ok: self.exit() if ok else None)
        else:
            self.exit()

    def action_rebuild(self) -> None:
        """``R`` when the bench file changed: confirm, then reset with force (D25)."""
        from .screens.states import confirm_rebuild

        if not self.file_changed or self.sid is None:
            return
        confirm_rebuild(self, self._do_rebuild)

    async def _do_rebuild(self, ok: bool) -> None:
        if not ok or self.sid is None:
            return
        try:
            self.session_info = await self.client.reset_session(self.sid, force=True)
            self.labels = build_labels(self.session_info)
        except ApiError:
            return
        self.store.clear()
        self.file_changed = False
        self._bench_sha = _hash_file(self.bench_path)
        self._dirty_instr = set()
        await self._full_refresh()
        self.bump()

    def action_open(self, key: str) -> None:
        """Open a registered screen (``l`` today; T2/T3 add instrument/nets/faults/runs)."""
        self.show(key)


def _default_screens() -> None:
    from .screens.logscreen import LogScreen

    register_screen("log", LogScreen)


_default_screens()
