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

from ..models import categories
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
    """The file bytes' sha256 — the same hash the server reports as a bench document's ``sha256``
    and a session's ``bench_sha256``. Read only on a workspace resync (#1633), never on a timer."""
    if path is None:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def screen_key(template: str | None) -> str:
    """Which screen shows a category (tui-screens.md 2.3): the registry row's screen kind decides
    (D80, #1469) — the scope screen for the scope's kind, the generic instrument screen for every
    other kind and for a class the registry does not know."""
    return "scope" if categories.get(template).screen == "scope" else "instrument"


class SimuBenchApp(App[None]):
    """``simubench tui``: overview, communication log, and the connection states of section 3."""

    TITLE = "SimuBench"  # the exported SVG's window title (it was the class name)

    CSS = """
    Screen { background: $background; scrollbar-size-vertical: 0; scrollbar-size-horizontal: 0; }
    Static#connecting-note { padding: 1 2; color: $muted; }
    Static#message { color: $fg; }
    Static#embedded-log { padding: 0 1; }
    #log-box { border: round $line; border-title-color: $hud; border-title-align: left; }
    #log-rows { padding: 0 1; }
    #attention { height: 1; }
    #cards { height: 1fr; overflow-y: auto; scrollbar-size-vertical: 0; }
    #cards Horizontal { height: auto; align: left top; overflow-x: hidden; }
    #embedded-log { scrollbar-size-vertical: 0; }
    #log-filter { display: none; border: round $focus; }
    /* the command palette (Ctrl+K, /) in the HUD's own panel colours, not Textual's tinted one */
    CommandPalette > Vertical { background: $surface; }
    CommandPalette #--input { border: round $focus; }
    CommandPalette #--input.--list-visible { border-bottom: none; }
    CommandPalette #--results { background: $surface; border: round $focus; border-top: none; }
    CommandPalette OptionList > .option-list--option-highlighted { background: $focus; color: $background; }
    """

    BINDINGS: ClassVar = [
        Binding("q", "quit_app", "", show=False),
        Binding("l", "open('log')", "", show=False),
        Binding("n", "open('nets')", "", show=False),
        Binding("f", "fault", "", show=False),
        Binding("a", "advance", "", show=False),
        Binding("R", "r_key", "", show=False),
        Binding("question_mark", "help", "", show=False),
        Binding("ctrl+k", "palette", "", show=False, priority=True),
    ]
    ENABLE_COMMAND_PALETTE = False  # Textual's own lists its themes; ours is palette.py (Ctrl+K)

    def __init__(
        self,
        url: str,
        sid: str | None = None,
        token: str | None = None,
        launched: bool = False,
        bench_path: Path | None = None,
        bench_name: str = "SimuBench",
    ) -> None:
        super().__init__()
        self.url = url.rstrip("/")
        self.sid = sid
        self.token = token
        self.lang = i18n.default_lang()  # English only (D23)
        self.launched = launched  # the TUI started this service: quit closes it, R rebuilds
        self.bench_path = bench_path
        self._bench_name = bench_name
        self.client = ApiClient(url, token)
        self.store = EventStore()
        self.session_info: dict[str, Any] | None = None
        self.levels: dict[str, Any] | None = None
        self.readings: dict[str, dict[str, Any]] = {}
        self.screen_data: dict[str, dict[str, Any]] = {}  # per-instrument /screen payloads
        #: the dot width each scope screen last asked for: a refetch (an event, a reconnect) must
        #: ask for the same binning, or the frame comes back raw and the trace draws nothing
        self.scope_points: dict[str, int] = {}
        #: a field jump the palette asked for: (instrument, state path, text to type or None);
        #: the instrument screen takes it once its controls exist
        self.pending_field: tuple[str, str, str | None] | None = None
        #: the state tree's folds per instrument (the groups that are open) and the ``h`` switch;
        #: kept here so leaving the tree and coming back finds it as it was
        self.tree_open: dict[str, set[str]] = {}
        self.tree_hidden = False
        self.faults: list[dict[str, Any]] = []  # GET /faults, refetched on fault events (section 1.6)
        self.scpi_history: dict[str, list[str]] = {}  # per-instrument console history (10.3)
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
        self._advance_default = "1 s"
        self.scope_version = 0  # the scope screen's frame alone (display data, not session data)
        self.reason: str | None = None  # why the picker came up again (session closed)
        self._stop = asyncio.Event()
        self._conn_task: asyncio.Task | None = None
        self._backoff = 1.0
        self._dirty_instr: set[str] = set()
        self._dirty_levels = False
        self._dirty_lines = False
        self._dirty_clock = False
        self._dirty_faults = False
        self._maintaining = False
        self._maint_task: asyncio.Task | None = None
        self._session_task: asyncio.Task | None = None
        self._session_queued = False  # a sid event arrived while a session re-read ran
        self._picker_task: asyncio.Task | None = None
        self._workspace_task: asyncio.Task | None = None
        self._workspace_opened = False  # the workspace socket has connected at least once
        self._ws_backoff = 1.0
        self._catalog_loaded = False
        # The theme's variables ($muted, $focus, …) must exist before the CSS parses.
        theme.apply_theme(self)

    # ---- startup ------------------------------------------------------------------------------------

    async def on_mount(self) -> None:
        from .screens.dialogs import load_history

        self.scpi_history = load_history()
        self.set_interval(0.1, self._tick)
        self.set_interval(1.0, self._tick_wall)
        await self.client.open()
        self._workspace_task = asyncio.create_task(self._workspace_loop())
        if self.sid:
            self.push_overview()
            self._conn_task = asyncio.create_task(self._connection_loop())
        else:
            await self._attach_flow()

    async def on_unmount(self) -> None:
        """The app is gone: stop the connection loop and close the client, so the websocket
        closes cleanly instead of leaking until the process ends. Requests in flight land
        first, or closing the client under them would crash the loop's task."""
        self._stop.set()
        tasks = [
            t
            for t in (
                self._conn_task,
                self._maint_task,
                self._session_task,
                self._picker_task,
                self._workspace_task,
            )
            if t is not None and not t.done()
        ]
        if tasks:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2)
        await self.client.close()

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

    def set_caption(self, text: str, right: str = "") -> None:
        """The scripted demo's bottom subtitle (6): left the caption, right `key ▸ enter`."""
        from .widgets import CaptionBar

        bar = self.screen.query_one(CaptionBar)
        bar.set_caption(text, right)

    def push_overview(self) -> None:
        from .screens.overview import OverviewScreen

        self.push_screen(OverviewScreen())

    async def connect(self, sid: str) -> None:
        """Leave the picker (or start up) for a session: show the overview and connect."""
        from .screens.overview import OverviewScreen
        from .screens.states import PickerScreen

        self.sid = sid
        self.conn = "connecting"
        self.file_changed = False  # the previous session's bench hint is not this session's
        if isinstance(self.screen, PickerScreen):
            self.pop_screen()  # the picker leaves the stack
        self.push_screen(OverviewScreen())
        if self._conn_task is None or self._conn_task.done():
            self._conn_task = asyncio.create_task(self._connection_loop())

    def show(self, key: str, *args: Any) -> None:
        """Push a registered screen; no-op when it is already on top (the registry, section 2.3).

        Factories may take arguments (the instrument screen opens with the instrument's name).
        """
        factory = SCREENS.get(key)
        if factory is None:
            return
        current = self.screen
        if isinstance(factory, type) and type(current) is factory and not args:
            return
        self.push_screen(factory(*args))

    def instrument_screen_key(self, name: str) -> str:
        """Which screen shows this instrument: the scope screen for a scope, else the generic one."""
        info = next(
            (i for i in (self.session_info or {}).get("instruments", []) if i.get("name") == name), {}
        )
        return screen_key((info.get("display") or {}).get("template"))

    def open_instrument(self, name: str, replace: bool = False) -> None:
        """Open an instrument's screen. ``replace`` swaps it for the one in front, so ``Esc`` still
        returns to where the user came from (``[`` and ``]`` step through the bench this way)."""
        key = self.instrument_screen_key(name)
        if replace:
            self.pop_screen()
        self.show(key, name)

    def go_home(self) -> None:
        """Pop to the overview — the demo script's ``home`` (issue 684): every screen above it
        leaves, like pressing Esc until the overview is in front."""
        from .screens.overview import OverviewScreen

        while not isinstance(self.screen, OverviewScreen) and len(self.screen_stack) > 1:
            self.pop_screen()

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
                    ws_url(self.url, self.sid, self.token, self.store.max_seq),
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
        # Anything else: the loop backs off and reconnects; the reconnect asks the server for the
        # events after the last one held (#1632), and the store dedupes anything that slips through.

    def _back_to_picker(self) -> None:
        self._picker_task = asyncio.create_task(self._show_picker(i18n.t("connect.gone", self.lang)))

    # ---- workspace events (the /api/events stream, D85) -------------------------------------------------

    async def _workspace_loop(self) -> None:
        """The workspace's event stream, its own connection beside the session's (D85): bench and
        definition file versions, access-port clients — what no session owns. The stream has no
        backlog, so a reopen re-reads what was missed (the Web's #1526 self-heal) instead of
        replaying; the app never polls for any of it."""
        while not self._stop.is_set():
            await self.client.workspace_stream(
                self._on_workspace_event,
                self._on_workspace_open,
                self._on_workspace_close,
                self._stop,
            )
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._ws_backoff)
                return
            except TimeoutError:
                pass
            self._ws_backoff = min(self._ws_backoff * 2, 10.0)

    def _on_workspace_open(self) -> None:
        """The socket (re)opened. On the first open the resync runs only when a query already read
        the server — its answer may predate a change announced to nobody (#1526); with nothing
        read yet, the connection loop's full refresh sees the current state. A reconnect always
        resyncs: events while no socket listened are lost for good."""
        first = not self._workspace_opened
        self._workspace_opened = True
        self._ws_backoff = 1.0
        if first and self.session_info is None:
            return
        self._workspace_resync()

    def _workspace_resync(self) -> None:
        """Re-derive what missed events would have said: the client set re-read, and the bench
        file judged against the hash the session was built from — a missed ``file_changed`` lights
        the same rebuild hint its event would have. The bench file is read once here, on a
        (re)connect — the detection itself is the server's watcher, never a local poll."""
        self._refresh_session_soon()
        if self.bench_path is None:
            return  # attach mode: the file is the server's, its events carry the changes
        sha = _hash_file(self.bench_path)
        if sha is not None and sha != (self.session_info or {}).get("bench_sha256"):
            self.file_changed = True
            self.bump()

    def _on_workspace_event(self, ev: dict[str, Any]) -> None:
        """One workspace event. ``file_changed`` names a workspace-relative file: only the
        session's own bench file lights the rebuild hint (definitions reach the running session
        through a rebuild, which R offers). A sid event re-reads the session: a script's
        arrival at, or departure from, the access ports (D85)."""
        kind = ev.get("kind")
        if kind == "file_changed":
            if ev.get("path") == (self.session_info or {}).get("bench_path"):
                self.file_changed = True
                self.bump()
        elif kind in ("clients_changed", "sessions_changed") and ev.get("sid") == self.sid:
            self._refresh_session_soon()

    def _on_workspace_close(self, code: int | str) -> None:
        if code == 4401:
            self.conn = "token-invalid"
            self._stop.set()
            self.bump()
            return
        # Anything else: the loop backs off and reconnects, then re-derives what it missed
        # (#1526) — there is no backlog to ask for (D85).

    def _refresh_session_soon(self) -> None:
        """One session re-read for the sid events and the resync: coalesced while one is in
        flight, but never dropped — a re-read issued before the change it answers (its fetch
        outran the server) must be followed by one that sees the new state, so an event that
        arrives while one runs queues a trailing re-read instead of being lost (#1633)."""
        if self.sid is None:
            return
        if self._session_task_running():
            self._session_queued = True
            return
        self._session_task = asyncio.create_task(self._run_session_refresh())

    async def _run_session_refresh(self) -> None:
        """The session re-read, followed by any re-read a sid event queued behind it."""
        await self._refresh_session()
        while self._session_queued and not self._stop.is_set():
            self._session_queued = False
            await self._refresh_session()

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
        if kind == "fault":
            self._dirty_faults = True
        if kind == "clock":  # switched while running (#1660): the top bar and the wall tick follow
            self._dirty_clock = True
        if kind in ("session", "remote", "clock"):
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
            self.faults = await self.client.faults(self.sid)
        self._dirty_levels = False
        self._dirty_faults = False
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
        """Readings for the instruments that show them and /screen for the classes whose screen
        data is fetched (section 4.2): the registry row decides, through categories.fetches
        (#1735) — the same predicate the per-event refresh below applies."""
        if self.session_info is None:
            return
        for info in self.session_info.get("instruments", []):
            display = info.get("display") or {}
            if categories.fetches(categories.get(display.get("template")), panel=bool(display.get("panel"))):
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
        if self._dirty_faults:
            self._dirty_faults = False
            with contextlib.suppress(ApiError):
                self.faults = await self.client.faults(self.sid)
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
            display = info.get("display") or {}
            want = categories.fetches(
                categories.get(display.get("template")), panel=bool(display.get("panel"))
            )
            if "readings" in want:
                with contextlib.suppress(ApiError):
                    self.readings[name] = await self.client.readings(self.sid, name)
            if "screen" in want:
                with contextlib.suppress(ApiError):
                    self.screen_data[name] = await self._screen(name, self.scope_points.get(name))

    async def _screen(self, name: str, points: int | None = None) -> dict[str, Any]:
        assert self.sid is not None
        return await self.client.screen(self.sid, name, points)

    async def refresh_scope_frame(self, name: str, points: int) -> None:
        """The scope screen's frame at its own dot width (tui-screens.md 1.6, 6.1).

        The frame is display data of one screen, not session data: it moves ``scope_version``
        — watched by the scope screen alone — and leaves ``data_version``, and with it "the
        app has settled", untouched.
        """
        self.scope_points[name] = points
        try:
            self.screen_data[name] = await self._screen(name, points)
        except ApiError:
            return
        self.scope_version += 1

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
        """Wall or scaled clock: time, levels and readings refresh every second (section 1.6, D44)."""
        if (
            self.clock
            and self.clock.get("mode") in ("wall", "scaled")
            and self.sid
            and self.conn == "connected"
        ):
            with contextlib.suppress(ApiError):
                self.clock = await self.client.clock(self.sid)
                self.levels = await self.client.levels(self.sid)
                self.session_info = await self.client.session(self.sid)
                self.labels = build_labels(self.session_info)
                self._mark_template_instruments()
            self.bump()

    def remote_held(self) -> bool:
        """Whether any instrument lists a connected client (an instrument in remote control)."""
        return any(i.get("remote") for i in (self.session_info or {}).get("instruments", []))

    def _session_task_running(self) -> bool:
        return self._session_task is not None and not self._session_task.done()

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

    def show_reason(self, report: Any) -> None:
        """The reason dialog for ``report``; ⏎ in it jumps to the command in the log (9.1)."""
        from .screens.dialogs import ReasonScreen

        def jump() -> None:
            if report.jump_seq is None:
                return
            self.show("log")
            focus = getattr(self.screen, "focus_seq", None)
            if focus is not None:
                focus(report.jump_seq)

        self.push_screen(ReasonScreen(report, jump))

    def jump_to_field(self, name: str, path: str, text: str | None = None) -> None:
        """Palette: go to the instrument's field and edit it (or set ``text``) where it stands."""
        screen = self.screen
        if getattr(screen, "instrument_name", None) == name and hasattr(screen, "go_to_field"):
            screen.go_to_field(path, text)
            return
        self.pending_field = (name, path, text)
        self.open_instrument(name)

    def action_palette(self) -> None:
        """``Ctrl+K``: the command palette, from any of the TUI's own screens (not a dialog)."""
        from .palette import open_palette
        from .screens.shell import ShellScreen

        if isinstance(self.screen, ShellScreen):
            open_palette(self)

    def action_help(self) -> None:
        """``?``: the keys of the screen in front (a dialog's own keys are on its face)."""
        from .screens.help import HelpScreen, help_rows
        from .screens.shell import ShellScreen

        screen = self.screen
        if not isinstance(screen, ShellScreen):
            return
        title = i18n.t("help.title", self.lang, screen=screen.screen_title())
        self.push_screen(HelpScreen(title, help_rows(screen, self)))

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

    def action_fault(self) -> None:
        """``f``: inject a fault on the focused instrument (tui-screens.md 8); works while remote (D32).
        Without a subject the bottom bar says how to get one (#791), like ``w`` with nothing to explain."""
        from .screens.faultdialog import FaultDialog
        from .widgets import BottomBar

        if self.conn != "connected":
            return
        instrument = self._focused_instrument()
        if instrument is None:
            bars = list(self.screen.query(BottomBar))
            if bars:
                bars[0].flash(i18n.t("attention.noFocus", self.lang))
            return
        self.push_screen(FaultDialog(instrument=instrument))

    def _focused_instrument(self) -> str | None:
        """The instrument the current screen is acting on (tui-screens.md 8): the overview's
        focused card, or an instrument/scope screen's own subject. Without a subject the dialog
        could only compose faults the engine rejects (every kind needs an instrument or a
        component), so ``f`` flashes the bottom bar's focus hint instead (8)."""
        from .screens.instrument import InstrumentScreen
        from .screens.overview import Card, OverviewScreen
        from .screens.scope import ScopeScreen

        screen = self.screen
        if isinstance(screen, OverviewScreen):
            focused = screen.focused
            return focused.card.name if isinstance(focused, Card) else None
        if isinstance(screen, InstrumentScreen | ScopeScreen):
            return screen.instrument_name
        return None

    def action_advance(self) -> None:
        """``a``: step the virtual clock (tui-screens.md 11.2)."""
        from .screens.clockdialog import AdvanceDialog

        if self.conn != "connected" or self.sid is None:
            return
        mode = self.clock_mode()
        if mode in ("wall", "scaled"):
            self.call_after_refresh(self._advance_wall_hint)
            return
        default = self._advance_default
        self.push_screen(AdvanceDialog(default), self._advance_done)

    async def _advance_wall_hint(self) -> None:
        from textual.widgets import Static

        from .widgets import BottomBar

        bars = [w for w in self.screen.query(BottomBar)]
        target = bars[0] if bars else self.screen.query_one(Static)
        target.flash(i18n.t(f"clock.{self.clock_mode()}Hint", self.lang))

    def _advance_done(self, seconds: float | None) -> None:
        if seconds is None:
            return

        def on_done(ok: bool) -> None:
            if ok:
                self.run_worker(self._do_advance(seconds), exclusive=True)

        from .screens.states import ConfirmScreen

        if (self.session_info or {}).get("clients"):
            lang = self.lang
            self.push_screen(
                ConfirmScreen(
                    i18n.t("clock.advanceTitle", lang),
                    [i18n.t("clock.advanceForce", lang, seconds=seconds)],
                    i18n.t("common.confirm", lang),
                    i18n.t("common.cancel", lang),
                ),
                on_done,
            )
        else:
            on_done(True)

    async def _do_advance(self, seconds: float) -> None:
        from .screens.clockdialog import format_duration

        self._advance_default = format_duration(seconds)
        await self.client.advance(self.sid, seconds, force=bool((self.session_info or {}).get("clients")))

    def action_r_key(self) -> None:
        """`R`: the runs screen; when the bench file changed, rebuild instead (7, D25, 11.3)."""
        if self.file_changed:
            self.action_rebuild()
            return
        if self.conn == "connected":
            self.show("runs")

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
        self._dirty_instr = set()
        self._dirty_faults = False
        self.faults = []
        await self._full_refresh()
        self.bump()

    def action_open(self, key: str) -> None:
        """Open a registered screen (``l`` today; T2/T3 add instrument/nets/faults/runs)."""
        self.show(key)


def _default_screens() -> None:
    from .screens.instrument import InstrumentScreen
    from .screens.logscreen import LogScreen
    from .screens.nets import NetsScreen
    from .screens.runs import RunsScreen
    from .screens.scope import ScopeScreen
    from .screens.statetree import StateTreeScreen

    register_screen("log", LogScreen)
    register_screen("instrument", InstrumentScreen)
    register_screen("scope", ScopeScreen)
    register_screen("nets", NetsScreen)
    register_screen("runs", RunsScreen)
    register_screen("states", StateTreeScreen)


_default_screens()
