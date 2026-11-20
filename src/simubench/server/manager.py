"""Sessions served by ``simubench serve``. The manager's job is threefold (issue 1383): the
sessions' lifecycle (start, reset, stop, and the port servers that come up with them), the access
services' supervision (who is connected, remote state, LOCAL, and the guards that refuse an
operation that would drop or disturb connected scripts), and the registries (sessions by id, try
sessions, runs). The projections — what a session looks like to the API, the tools and the TUI —
live in :mod:`simubench.server.views` and :mod:`simubench.server.preview`; the manager neither
assembles DTOs nor touches a component's insides."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..access.bench_link import BenchLinkServer
from ..access.hislip_server import HislipServer
from ..access.hislip_tls import SecureConfig, read_users
from ..access.scpi_server import DEFAULT_SCPI_PORT, ScpiServer
from ..access.vxi11_server import Vxi11Server
from ..api import bench_files_dir, open_bench
from ..contracts import api as M
from ..engine.record import definition_hash
from ..library.workspace_layout import definitions_dir
from ..models.bench import BenchDefinition
from .benchfiles import BenchFiles
from .errors import PORT_OPTION, BenchInvalid, ClientsConnected, InstrumentRemote, PortInUse, Unknown
from .runs import Runs
from .views import SessionViews, served_addresses
from .workspace_events import WorkspaceEvents, WorkspaceWatcher

if TYPE_CHECKING:
    from ..engine.session import Session

log = logging.getLogger(__name__)

#: Registry keys of the access servers a bench starts with (#1475): the service names the
#: addresses and the port-fallback notices show. A new access protocol adds its start-up row —
#: nothing else in the manager knows the servers by name.
SCPI_SERVICE, HISLIP_SERVICE, VXI11_SERVICE, LINK_SERVICE = "SCPI", "HiSLIP", "VXI-11", "bench link"


def vxi11_port_of(args: Any) -> int | None:
    """The VXI-11 core port the command line asks for: ``--vxi11-port`` (0 lets the OS choose), or
    implied by ``--portmapper-port``; ``None`` leaves VXI-11 off, as it is by default (D63)."""
    if args.no_scpi:
        return None
    if args.vxi11_port is not None:
        return int(args.vxi11_port)
    return 0 if args.portmapper_port is not None else None


def hislip_options_of(args: Any) -> dict[str, Any]:
    """``SessionManager`` keyword arguments for the HiSLIP options of the command line (D41).

    ``--hislip-cert`` and ``--hislip-key`` switch on the secure connection capability; they come
    as a pair. ``--hislip-users`` (``name:password`` lines) turns the login from ANONYMOUS into PLAIN;
    it needs ``--hislip-tls initial`` or ``mandatory``, the modes in which every client must log in.
    The login covers the HiSLIP port only: raw sockets and VXI-11 on the same bench have none."""
    options: dict[str, Any] = {"hislip_overlapped": bool(args.hislip_overlapped)}
    if args.hislip_users and args.hislip_tls == "optional":
        # With optional encryption a client that never asks for it uses HiSLIP without any login
        # (and so does a 1.0 client): a users file would look like protection and be none.
        raise SystemExit(
            "--hislip-users only gates connections that must be encrypted: add --hislip-tls initial or mandatory"
        )
    if args.hislip_cert or args.hislip_key:
        if not (args.hislip_cert and args.hislip_key):
            raise SystemExit("--hislip-cert and --hislip-key go together")
        mode = args.hislip_tls
        options["hislip_secure"] = SecureConfig.from_files(
            args.hislip_cert,
            args.hislip_key,
            mandatory=mode == "mandatory",
            initial=mode == "initial",
            users=read_users(args.hislip_users) if args.hislip_users else None,
        )
    elif args.hislip_users or args.hislip_tls != "optional":
        raise SystemExit("--hislip-tls and --hislip-users need --hislip-cert and --hislip-key")
    return options


@dataclass
class TryEntry:
    """One try session: which definition it serves, and when it was last used."""

    served: ServedSession
    definition: str
    used: float
    #: Held while a batch runs on the session; closing waits for it (never closes a running batch).
    busy: threading.Lock = field(default_factory=threading.Lock, repr=False)
    closed: bool = False


class TrySessions:
    """Temporary sessions with one instrument each (T2, instrument-authoring.md 7.2 and 7.4): try a
    definition without a bench file, from the acceptance page or ``try_definition``.

    A try session binds no SCPI port, writes no run record and never touches the bench session the
    engineer has open (starting a bench stops other *bench* sessions only). It is closed explicitly,
    or reaped after :data:`IDLE_S` of disuse; opening beyond :data:`MAX` retires the least recently
    used one.
    """

    #: Concurrent try sessions: the page tries one instrument at a time, a few more for compares.
    MAX = 4
    #: A try session unused for this long (wall time) is closed at the next look.
    IDLE_S = 600.0

    def __init__(self, files: BenchFiles, idle_s: float = IDLE_S) -> None:
        self.files = files
        self.idle_s = idle_s
        self._tries: dict[str, TryEntry] = {}
        self._lock = threading.Lock()

    def open(self, def_id: str) -> TryEntry:
        """A fresh session with the definition's instrument alone; another for the same definition
        replaces the previous one (it is a scratch session). For callers that need a clean start
        (the acceptance page's "restart"); repeated use goes through :meth:`using`."""
        with self._lock:
            return self._open(def_id, replace=True)

    @contextmanager
    def using(self, def_id: str) -> Iterator[TryEntry]:
        """The definition's open try session (a new one if none), held exclusively for one batch.

        Consecutive batches for the same definition run on the same session (id, state and virtual
        time carry on) until it falls idle or is retired. A concurrent batch, a close or a
        replacing :meth:`open` waits for this one instead of running on or closing a session in use.
        """
        while True:
            with self._lock:
                self._sweep()
                entry = next((e for e in self._tries.values() if e.definition == def_id), None)
                if entry is None:
                    entry = self._open(def_id, replace=False)
                entry.used = time.monotonic()
            with entry.busy:
                if entry.closed:  # closed while this call waited for the batch ahead of it
                    continue
                try:
                    yield entry
                finally:
                    # no pool lock here: a close holds it while waiting for ``busy`` (deadlock)
                    entry.used = time.monotonic()
                return

    def _open(self, def_id: str, *, replace: bool) -> TryEntry:
        # called with ``self._lock`` held
        self._sweep()
        self.files.library(None).get(def_id)  # KeyError for an unknown id, before building
        for sid, entry in list(self._tries.items()):
            if replace and entry.definition == def_id:
                self._close(sid)
        while len(self._tries) >= self.MAX:
            self._close(min(self._tries, key=lambda sid: self._tries[sid].used))
        bench = BenchDefinition.model_validate(
            {
                "schema": "simubench.bench/2",
                "name": f"try-{def_id}",
                "instruments": {def_id: {"model": def_id}},
            }
        )
        session = open_bench(bench, definition_dirs=[definitions_dir(self.files.workspace)])
        served = ServedSession(uuid.uuid4().hex[:12], session, None)
        entry = TryEntry(served=served, definition=def_id, used=time.monotonic())
        self._tries[served.id] = entry
        return entry

    def get(self, sid: str) -> TryEntry:
        with self._lock:
            entry = self._tries.get(sid)
            if entry is None:
                raise Unknown(sid)
            # ``>=``: with ``idle_s == 0`` the session is reaped on the first look even when the
            # clock tick has not advanced (Windows' monotonic resolution is ~15.6 ms).
            if time.monotonic() - entry.used >= self.idle_s:
                self._close(sid)
                raise Unknown(sid)
            entry.used = time.monotonic()
            return entry

    def all(self) -> list[TryEntry]:
        """The open try sessions, oldest use first, after reaping the expired ones."""
        with self._lock:
            self._sweep()
            return sorted(self._tries.values(), key=lambda e: e.used)

    def close(self, sid: str) -> None:
        """Close a try session; closing an unknown or already closed one does nothing."""
        with self._lock:
            self._close(sid)

    def _close(self, sid: str) -> None:
        entry = self._tries.pop(sid, None)
        if entry is not None:
            with entry.busy:  # waits for a running batch
                entry.closed = True
                entry.served.session.close()

    def _sweep(self) -> None:
        now = time.monotonic()
        for sid in [sid for sid, e in self._tries.items() if now - e.used >= self.idle_s]:
            self._close(sid)


class ServedSession:
    def __init__(self, sid: str, session: Session, bench_path: Path | None, preview: bool = False) -> None:
        self.id = sid
        #: A projection of a file, not a served session (server/preview.py): stale-definition and
        #: other served-session-only guards skip it without a string sentinel (#1488).
        self.preview = preview
        #: This build of the bench (#1299): a reset or a rebuild serves a new one, even under the same id.
        self.build = uuid.uuid4().hex[:12]
        self.session = session
        self.bench_path = bench_path
        self.bench_sha256: str | None = None
        self.bench_fingerprint: str | None = None
        #: The access servers serving this bench, by service name — the one registry (#1475): a
        #: new access protocol adds its start-up row and every collection (clients, LOCAL, stop)
        #: follows it; the servers' port semantics stay in their own modules. The named access
        #: below is for the places that show one server's protocol-specific facts.
        self.servers: dict[str, Any] = {}
        #: Projection state, deliberately engine-external: how many times each instrument's scope
        #: screen has been read — the redraw counter a running screen draws its frame with (D47).
        #: It lives here, not in the session, so a passive view's polling (D55) advances no virtual
        #: time, takes no acquisition and leaves the engine's deterministic state (seed, records,
        #: clocks) alone.
        self.frames: dict[str, int] = {}
        self.clock: str | None = None  # startup settings, kept for a reset
        self.time_scale: float | None = None
        self.seed: int | None = None
        #: Instruments taken back to local from the UI (LOCAL), until a remote client's next command.
        self.local: set[str] = set()
        #: Why an address is not the configured one (a taken default port, #1197), for the page.
        self.port_notices: list[str] = []
        session.events.subscribe(self._follow_remote)
        # The engine raises what this raises: an explicit advance refuses while scripts are
        # connected to the ports, unless confirmed (#121, issue 624).
        session.clock.confirm_advance = self._confirm_advance

    # Named access to the servers that come with a bench. The registry (``servers``) is what the
    # loops read; these are for the seams that show one protocol's facts (a socket port, the
    # HiSLIP address, the link address).

    @property
    def scpi(self) -> ScpiServer | None:
        return self.servers.get(SCPI_SERVICE)

    @property
    def hislip(self) -> HislipServer | None:
        return self.servers.get(HISLIP_SERVICE)

    @property
    def vxi11(self) -> Vxi11Server | None:
        return self.servers.get(VXI11_SERVICE)

    @property
    def link(self) -> BenchLinkServer | None:
        return self.servers.get(LINK_SERVICE)

    def _confirm_advance(self, force: bool) -> None:
        self.require_no_clients("advancing the clock", force)

    def is_remote(self, client: str | None) -> bool:
        """Whether ``client`` is a connection on the access servers' ports, as opposed to the UI,
        MCP, SDK or a replay. Judged from the connections themselves, not from the label's
        spelling (#1475): the client labels (``socket:``, ``hislip:``, ``vxi11:``, ``bench:``)
        are display names, so a new protocol's clients are remote without a prefix table to
        extend — one that a missed entry would silently break."""
        return bool(client) and any(
            c.client == client for server in self.servers.values() for c in server.connections()
        )

    def _follow_remote(self, ev: dict[str, Any]) -> None:
        # As on hardware, a command from the remote interface returns the instrument to remote.
        name = ev.get("instrument")
        if (
            ev.get("kind") == "scpi"
            and ev.get("dir") == "write"
            and name in self.local
            and self.is_remote(ev.get("client"))
        ):
            self.local.discard(name)
            self.session.events.emit(
                {"kind": "remote", "instrument": name, "action": "remote", "source": ev["client"]}
            )

    def remote_clients(self, instrument: str | None = None) -> list[str]:
        """Clients connected to the ports of ``instrument`` (or of any instrument), sorted."""
        return sorted(
            c.client
            for server in self.servers.values()
            for c in server.connections()
            if instrument is None or c.instrument == instrument
        )

    def require_local(self, instrument: str) -> None:
        """Refuse a UI operation on an instrument in remote control (#121)."""
        self.session.instrument(instrument)  # KeyError for an unknown name, before the remote check
        clients = self.remote_clients(instrument)
        if clients and instrument not in self.local:
            raise InstrumentRemote(instrument, clients)

    def require_no_clients(self, what: str, force: bool) -> None:
        """Refuse an operation that drops or disturbs connected scripts unless confirmed (#121)."""
        clients = self.remote_clients()
        if clients and not force:
            raise ClientsConnected(clients, what)

    def go_local(self, instrument: str, client: str = "ui") -> None:
        """LOCAL: the UI takes the instrument back from the remote clients (recorded as an event)."""
        self.session.instrument(instrument)
        if instrument in self.local:
            return
        self.local.add(instrument)
        self.session.events.emit(
            {"kind": "remote", "instrument": instrument, "action": "local", "source": client}
        )


class SessionManager:
    def __init__(
        self,
        workspace: Path,
        *,
        scpi_host: str = "127.0.0.1",
        scpi_base_port: int | None = DEFAULT_SCPI_PORT,
        hislip_port: int | None = 4880,
        vxi11_port: int | None = None,
        portmapper_port: int | None = None,
        hislip_overlapped: bool = False,
        hislip_secure: SecureConfig | None = None,
        link_port: int | None = None,
        record_dir: Path | None = None,
        record_raw: bool = False,
        compute_budget_s: float | None = None,
        allow_plugins: bool = False,
        port_fallback: frozenset[str] = frozenset(),
    ) -> None:
        self.workspace = workspace
        #: Services ("SCPI", "HiSLIP") whose taken port is replaced by one the OS picks instead of
        #: refusing the bench: the double-click start's default ports (#1197). An explicit port never is.
        self.port_fallback = port_fallback
        self.scpi_host = scpi_host
        self.scpi_base_port = scpi_base_port
        self.hislip_port = hislip_port
        self.vxi11_port = vxi11_port
        self.portmapper_port = portmapper_port
        self.hislip_overlapped = hislip_overlapped
        self.hislip_secure = hislip_secure
        self.link_port = link_port
        self.record_dir = record_dir
        self.record_raw = record_raw
        #: Real seconds one read of a served session may compute (None: no limit; D88).
        self.compute_budget_s = compute_budget_s
        self.allow_plugins = allow_plugins
        #: The Web service's own port, when this manager serves one (``simubench serve``).
        self.web_port: int | None = None
        self.sessions: dict[str, ServedSession] = {}
        #: The workspace event stream (issue #1484): the file write points and the access
        #: servers' connection bookkeeping publish here; ``/api/events`` forwards to the Web.
        self.events = WorkspaceEvents(WorkspaceWatcher(workspace, self._publish))
        self.files = BenchFiles(workspace, events=self.events)
        self.runs = Runs(record_dir, [definitions_dir(self.workspace)])
        self.tries = TrySessions(self.files)
        self.views = SessionViews(self)
        # Workspace plugins run code from the workspace: only with the engineer's consent (D3),
        # and before any bench is validated, so plugin types are known to the checks.
        if self.plugins_allowed():
            from ..engine.plugins import load_workspace_plugins

            load_workspace_plugins(workspace / "components")

    def plugins_allowed(self) -> bool:
        """``--allow-plugins``, or the workspace setting (``.simubench/settings.json``)."""
        from ..engine.plugins import workspace_allows_plugins

        return self.allow_plugins or workspace_allows_plugins(self.workspace)

    def _publish(self, kind: str, **fields: Any) -> None:
        """The watcher's publish path (the write points reach the hub through BenchFiles)."""
        self.events.publish(kind, **fields)

    def _clients_changed(self, served: ServedSession) -> Callable[[], None]:
        """What the access servers fire when their connection set changes (issue #1484): one
        workspace event, whose sid the Web's session queries re-key on instead of polling."""
        return lambda: self.events.publish("clients_changed", sid=served.id)

    # ---- bench files --------------------------------------------------------------------------------

    def bench_files(self) -> list[M.BenchFile]:
        """The workspace's bench files; the assembly is the views' (:meth:`SessionViews.bench_files`).
        This delegate stays for the seams that reach only the manager (the MCP tools and the
        assistant's tools, issue 1383)."""
        return self.views.bench_files()

    def resolve(self, bench_path: str) -> Path:
        return self.files.resolve(bench_path)

    # ---- sessions -----------------------------------------------------------------------------------

    async def start(
        self,
        bench_path: str,
        clock: str | None = None,
        seed: int | None = None,
        sid: str | None = None,
        force: bool = False,
        time_scale: float | None = None,
        first_event_id: int = 0,
    ) -> ServedSession:
        # Starting a bench stops the running one (one bench serves the ports) and its scripts drop:
        # the guard lives here, so no facade can start a bench over connected scripts (#121, 624).
        clients = sorted({c for other in self.sessions.values() for c in other.remote_clients()})
        if clients and not force:
            raise ClientsConnected(clients, "starting a bench")
        path = self.resolve(bench_path)
        doc = self.files.read(bench_path)
        if not doc.ok:
            raise BenchInvalid(doc.diagnostics)
        # Sockets and HiSLIP serve other processes, whose sleep cannot be intercepted: a bench
        # configured for 'sleep' runs as 'off' here (D47).
        idle = "off" if (doc.data.get("run") or {}).get("idle") == "sleep" else None
        session = open_bench(
            path,
            clock=clock,
            seed=seed,
            idle=idle,
            time_scale=time_scale,
            record_dir=self.record_dir,
            record_raw=self.record_raw,
            compute_budget_s=self.compute_budget_s,
            definition_dirs=[definitions_dir(self.workspace)],  # as validated (BenchFiles.library)
            files_dir=bench_files_dir(self.workspace, path),  # kept across runs (#1069)
            first_event_id=first_event_id,
        )
        served = ServedSession(sid or uuid.uuid4().hex[:12], session, path)
        served.bench_sha256 = doc.sha256
        served.bench_fingerprint = doc.fingerprint
        served.clock, served.seed, served.time_scale = clock, seed, time_scale
        # One bench serves the SCPI ports at a time; a new session takes them over.
        for other in list(self.sessions.values()):
            await self.stop(other.id, force=True)  # confirmed above, as part of starting
        if self.scpi_base_port is not None:
            try:
                served.servers[SCPI_SERVICE] = await self._open(
                    served,
                    SCPI_SERVICE,
                    self.scpi_base_port,
                    lambda port: ScpiServer(
                        session, self.scpi_host, port, on_change=self._clients_changed(served)
                    ),
                )
                if self.hislip_port is not None:  # HiSLIP starts alongside the sockets (D41)
                    served.servers[HISLIP_SERVICE] = await self._open(
                        served,
                        HISLIP_SERVICE,
                        self.hislip_port,
                        lambda port: HislipServer(
                            session,
                            self.scpi_host,
                            port,
                            overlapped=self.hislip_overlapped,
                            secure=self.hislip_secure,
                            on_change=self._clients_changed(served),
                        ),
                    )
                if self.vxi11_port is not None:  # and VXI-11 beside it (D63)
                    served.servers[VXI11_SERVICE] = await self._open(
                        served,
                        VXI11_SERVICE,
                        self.vxi11_port,
                        lambda port: Vxi11Server(
                            session,
                            self.scpi_host,
                            port,
                            self.portmapper_port,
                            on_change=self._clients_changed(served),
                        ),
                    )
            except PortInUse:
                # Close what did open (the instruments before the taken port), and the session.
                for server in reversed(list(served.servers.values())):
                    await server.stop()
                await asyncio.to_thread(session.close)
                raise
            if self.link_port is not None:  # scripts in other processes drive this bench (D63)
                link = BenchLinkServer(
                    session, self.scpi_host, self.link_port, on_change=self._clients_changed(served)
                )
                try:
                    await link.start()
                    link.served = served_addresses(served)
                    served.servers[LINK_SERVICE] = link
                except OSError as exc:  # another service holds the port: this one still works
                    log.warning("bench link not started (port %s: %s)", self.link_port, exc)
        self.sessions[served.id] = served
        self.events.publish("sessions_changed", sid=served.id)
        return served

    async def _open(self, served: ServedSession, service: str, port: int, make: Any) -> Any:
        """Start one port server; a port that cannot be opened is :class:`PortInUse`, or, for a
        service in ``port_fallback``, a port the OS picks plus a notice saying so (#1197)."""
        server = make(port)
        try:
            await server.start()
            return server
        except OSError as exc:
            await server.stop()  # what did open (the instruments before the taken port)
            if port == 0 or service not in self.port_fallback:
                raise PortInUse(service, exc) from exc
            server = make(0)
            try:
                await server.start()
            except OSError as again:
                await server.stop()
                raise PortInUse(service, again) from again
            notice = (
                f"{service} 默认端口 {port} 被占用（{exc.strerror or exc}），已改用系统分配的空闲端口；"
                f"脚本请按界面上列出的地址连接。要固定端口：SimuBench.exe {PORT_OPTION[service]} <端口>"
            )
            log.warning(notice)
            served.port_notices.append(notice)
            return server

    async def reset(
        self, sid: str, clock: str | None = None, seed: int | None = None, force: bool = False
    ) -> ServedSession:
        """Power-cycle: a fresh session under the same id, with the startup settings applied.

        Everything goes back to power-on defaults (a new record starts too); scripts connected
        to the ports are dropped, as on hardware — refused while they are connected, unless
        ``force`` (#121, issue 624).
        """
        served = self.sessions.get(sid)
        if served is None:
            raise Unknown(sid)
        bench_path = served.bench_path
        if bench_path is None:
            raise Unknown(sid)
        served.require_no_clients("a reset", force)
        rel = bench_path.relative_to(self.files.workspace).as_posix()
        clock = clock if clock is not None else served.clock
        seed = seed if seed is not None else served.seed
        # The stream's cursor survives the reset: the new session counts on from the old one's last
        # event, so a client reconnecting with its old ``since`` gets every new event (D91, #1642).
        last_event = served.session.events.event_id
        await self.stop(sid, force=True)  # confirmed above, as part of the reset
        return await self.start(
            rel,
            clock=clock,
            seed=seed,
            sid=sid,
            force=True,
            time_scale=served.time_scale,
            first_event_id=last_event,
        )

    async def stop(self, sid: str, force: bool = False) -> None:
        """Stop a session and close its ports; refused while scripts are connected, unless
        ``force`` (#121, issue 624). An unknown id raises ``Unknown``: whoever stops a session
        must answer "no such session", not succeed silently — every seam stops on this one path,
        so the contract lives here with the lifecycle (#1485)."""
        served = self.sessions.get(sid)
        if served is None:
            raise Unknown(sid)
        served.require_no_clients("stopping the bench", force)
        del self.sessions[sid]
        self.events.publish("sessions_changed", sid=sid)
        served.session.cancel()  # a read still computing ends at its next frame, not after it (D88)
        for server in reversed(list(served.servers.values())):  # last served closes first
            await server.stop()
        await asyncio.to_thread(served.session.close)

    def stale_definitions(self, served: ServedSession) -> list[str]:
        """Definitions the session's instruments use that changed on disk since it started."""
        lib = self.files.library(served.bench_path)
        out: list[str] = []
        for inst in served.session.instruments.values():
            d = inst.definition
            if d.id in out:
                continue
            try:
                current = definition_hash(lib.get(d.id))
            except Exception:  # removed, or no longer loads: the session runs an old version too
                current = None
            if current != definition_hash(d):
                out.append(d.id)
        return out

    def sessions_using_stale_definition(self, def_id: str) -> list[str]:
        """The running sessions whose instruments use an older version of this definition than the
        file now holds: they keep the old one until restarted (D25), so both seams can warn."""
        return [sid for sid, sv in self.sessions.items() if def_id in self.stale_definitions(sv)]

    def get(self, sid: str) -> ServedSession:
        try:
            return self.sessions[sid]
        except KeyError:
            raise Unknown(sid) from None
