"""Sessions served by ``simubench serve``: start, look up, describe."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..access.bench_link import BenchLinkServer
from ..access.hislip_server import HislipServer
from ..access.hislip_tls import SecureConfig, read_users
from ..access.scpi_server import ScpiServer
from ..access.vxi11_server import Vxi11Server
from ..api import open_bench
from ..engine.portref import split
from ..engine.record import definition_hash
from ..engine.session import Session
from ..library.loader import load_bench
from ..models import api as M
from ..models.bench import BenchDefinition
from .benchfiles import BenchFiles, scan
from .runs import Runs
from .views import SessionViews, served_addresses

log = logging.getLogger(__name__)


class BenchInvalid(ValueError):
    """The bench cannot start; ``diagnostics`` say why and where."""

    def __init__(self, diagnostics: list[M.Diagnostic]) -> None:
        first = next(d for d in diagnostics if d.severity == "error")
        super().__init__(f"{first.pointer}: {first.message}")
        self.diagnostics = diagnostics


class InstrumentRemote(RuntimeError):
    """A UI operation on an instrument a script is connected to (remote, #121)."""

    def __init__(self, instrument: str, clients: list[str]) -> None:
        super().__init__(f"{instrument} is in remote control by {', '.join(clients)}; take it to local first")
        self.instrument = instrument
        self.clients = clients


class ClientsConnected(RuntimeError):
    """An operation that drops or disturbs the scripts connected to the bench, asked without force."""

    def __init__(self, clients: list[str], what: str) -> None:
        super().__init__(f"{what} affects connected scripts ({', '.join(clients)}); confirm with force")
        self.clients = clients


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


def is_remote(client: str | None) -> bool:
    """A client on the SCPI ports, as opposed to the UI, MCP, SDK or a replay."""
    return bool(client) and client.startswith(("socket:", "hislip:", "vxi11:", "bench:"))  # type: ignore[union-attr]


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
        session = open_bench(bench, definition_dirs=[self.files.workspace / "instruments"])
        served = ServedSession(uuid.uuid4().hex[:12], session, None)
        entry = TryEntry(served=served, definition=def_id, used=time.monotonic())
        self._tries[served.id] = entry
        return entry

    def get(self, sid: str) -> TryEntry:
        with self._lock:
            entry = self._tries.get(sid)
            if entry is None:
                raise KeyError(sid)
            # ``>=``: with ``idle_s == 0`` the session is reaped on the first look even when the
            # clock tick has not advanced (Windows' monotonic resolution is ~15.6 ms).
            if time.monotonic() - entry.used >= self.idle_s:
                self._close(sid)
                raise KeyError(sid)
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
    def __init__(self, sid: str, session: Session, bench_path: Path | None) -> None:
        self.id = sid
        self.session = session
        self.bench_path = bench_path
        self.bench_sha256: str | None = None
        self.bench_fingerprint: str | None = None
        self.scpi: ScpiServer | None = None
        self.hislip: HislipServer | None = None
        self.vxi11: Vxi11Server | None = None
        self.link: BenchLinkServer | None = None
        self.frames: dict[str, int] = {}  # screen frames of running scopes, by instrument (noise of each)
        self.clock: str | None = None  # startup settings, kept for a reset
        self.time_scale: float | None = None
        self.seed: int | None = None
        #: Instruments taken back to local from the UI (LOCAL), until a remote client's next command.
        self.local: set[str] = set()
        session.subscribe(self._follow_remote)
        # The engine raises what this raises: an explicit advance refuses while scripts are
        # connected to the ports, unless confirmed (#121, issue 624).
        session.confirm_advance = self._confirm_advance

    def _confirm_advance(self, force: bool) -> None:
        self.require_no_clients("advancing the clock", force)

    def _follow_remote(self, ev: dict[str, Any]) -> None:
        # As on hardware, a command from the remote interface returns the instrument to remote.
        name = ev.get("instrument")
        if (
            ev.get("kind") == "scpi"
            and ev.get("dir") == "write"
            and name in self.local
            and is_remote(ev.get("client"))
        ):
            self.local.discard(name)
            self.session.emit(
                {"kind": "remote", "instrument": name, "action": "remote", "source": ev["client"]}
            )

    def remote_clients(self, instrument: str | None = None) -> list[str]:
        """Clients connected to the ports of ``instrument`` (or of any instrument), sorted."""
        out = [
            c
            for lst in (self.scpi.listeners if self.scpi else [])
            if instrument is None or lst.instrument == instrument
            for c in lst.labels
        ]
        if self.hislip:
            out += [
                hs.client
                for hs in self.hislip.sessions()
                if instrument is None or hs.instrument == instrument
            ]
        if self.vxi11:
            out += self.vxi11.clients(instrument)
        if self.link:
            out += self.link.clients(instrument)
        return sorted(out)

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
        self.session.emit({"kind": "remote", "instrument": instrument, "action": "local", "source": client})


class SessionManager:
    def __init__(
        self,
        workspace: Path,
        *,
        scpi_host: str = "127.0.0.1",
        scpi_base_port: int | None = 5025,
        hislip_port: int | None = 4880,
        vxi11_port: int | None = None,
        portmapper_port: int | None = None,
        hislip_overlapped: bool = False,
        hislip_secure: SecureConfig | None = None,
        link_port: int | None = None,
        record_dir: Path | None = None,
        record_raw: bool = False,
        allow_plugins: bool = False,
    ) -> None:
        self.workspace = workspace
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
        self.allow_plugins = allow_plugins
        self.sessions: dict[str, ServedSession] = {}
        self.files = BenchFiles(workspace)
        self.runs = Runs(record_dir, [self.workspace / "instruments"])
        self.tries = TrySessions(self.files)
        self.views = SessionViews(self)
        # Workspace plugins run code from the workspace: only with the engineer's consent (D3),
        # and before any bench is validated, so plugin types are known to the checks.
        if self.plugins_allowed():
            from ..plugins import load_workspace_plugins

            load_workspace_plugins(workspace / "components")

    def plugins_allowed(self) -> bool:
        """``--allow-plugins``, or the workspace setting (``.simubench/settings.json``)."""
        from ..plugins import workspace_allows_plugins

        return self.allow_plugins or workspace_allows_plugins(self.workspace)

    # ---- bench files --------------------------------------------------------------------------------

    def bench_files(self) -> list[M.BenchFile]:
        out: list[M.BenchFile] = []
        for p in scan(self.workspace):
            rel = p.relative_to(self.workspace).as_posix()
            try:
                b = load_bench(p)
                out.append(
                    M.BenchFile(
                        path=rel, name=b.name, description=b.description, instruments=len(b.instruments)
                    )
                )
            except Exception as exc:  # show broken files instead of hiding them
                out.append(M.BenchFile(path=rel, name=p.name, error=str(exc).splitlines()[0]))
        return out

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
            definition_dirs=[self.workspace / "instruments"],  # as validated (BenchFiles.library)
        )
        served = ServedSession(sid or uuid.uuid4().hex[:12], session, path)
        served.bench_sha256 = doc.sha256
        served.bench_fingerprint = doc.fingerprint
        served.clock, served.seed, served.time_scale = clock, seed, time_scale
        # One bench serves the SCPI ports at a time; a new session takes them over.
        for other in list(self.sessions.values()):
            await self.stop(other.id, force=True)  # confirmed above, as part of starting
        if self.scpi_base_port is not None:
            served.scpi = ScpiServer(session, self.scpi_host, self.scpi_base_port)
            await served.scpi.start()
            if self.hislip_port is not None:  # HiSLIP starts alongside the sockets (D41)
                served.hislip = HislipServer(
                    session,
                    self.scpi_host,
                    self.hislip_port,
                    overlapped=self.hislip_overlapped,
                    secure=self.hislip_secure,
                )
                await served.hislip.start()
            if self.vxi11_port is not None:  # and VXI-11 beside it (D63)
                served.vxi11 = Vxi11Server(session, self.scpi_host, self.vxi11_port, self.portmapper_port)
                await served.vxi11.start()
            if self.link_port is not None:  # scripts in other processes drive this bench (D63)
                link = BenchLinkServer(session, self.scpi_host, self.link_port)
                try:
                    await link.start()
                    link.served = served_addresses(served)
                    served.link = link
                except OSError as exc:  # another service holds the port: this one still works
                    log.warning("bench link not started (port %s: %s)", self.link_port, exc)
        self.sessions[served.id] = served
        return served

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
            raise KeyError(sid)
        bench_path = served.bench_path
        if bench_path is None:
            raise KeyError(sid)
        served.require_no_clients("a reset", force)
        rel = bench_path.relative_to(self.files.workspace).as_posix()
        clock = clock if clock is not None else served.clock
        seed = seed if seed is not None else served.seed
        await self.stop(sid, force=True)  # confirmed above, as part of the reset
        return await self.start(
            rel, clock=clock, seed=seed, sid=sid, force=True, time_scale=served.time_scale
        )

    async def stop(self, sid: str, force: bool = False) -> None:
        """Stop a session and close its ports; refused while scripts are connected, unless
        ``force`` (#121, issue 624)."""
        served = self.sessions.get(sid)
        if served is None:
            return
        served.require_no_clients("stopping the bench", force)
        del self.sessions[sid]
        if served.link:
            await served.link.stop()
        if served.vxi11:
            await served.vxi11.stop()
        if served.hislip:
            await served.hislip.stop()
        if served.scpi:
            await served.scpi.stop()
        await asyncio.to_thread(served.session.close)

    def preview(self, bench_path: str) -> M.BenchPreview:
        """Build the bench without running it, leaving out what cannot be built."""
        doc = self.files.read(bench_path)
        out = M.BenchPreview(path=bench_path, sha256=doc.sha256)
        errors = [d for d in doc.diagnostics if d.severity == "error"]
        if any(d.code == "E-SCHEMA" for d in errors):
            out.error = "文件不符合测试台格式"
            return out
        data = copy.deepcopy(doc.data)
        instruments, parts = data.get("instruments") or {}, data.get("parts") or {}
        dropped_wires: set[int] = set()
        for d in errors:
            segs = [p.replace("~1", "/").replace("~0", "~") for p in d.pointer.split("/")[1:]]
            if d.code.startswith("E-STATE-") and len(segs) >= 4:
                instruments.get(segs[1], {}).get("state", {}).pop(segs[3], None)
                out.omitted.append(f"{segs[1]}.{segs[3]}")
            elif d.code == "E-MODEL-UNKNOWN" and d.subject:
                instruments.pop(d.subject, None)
                out.omitted.append(d.subject)
            elif d.code in ("E-COMPONENT-UNKNOWN", "E-NAME-DUPLICATE") and d.subject:
                parts.pop(d.subject, None)
                out.omitted.append(d.subject)
            elif d.subject and d.subject.startswith("wire:"):
                dropped_wires.add(int(d.subject[5:]))
        names = set(instruments) | set(parts)
        wires = data.get("wires") or []
        kept: list[int] = []
        for i, w in enumerate(wires):
            ends = (str(w.get("from", "")), str(w.get("to", "")))
            if i in dropped_wires or any(split(e).owner not in names for e in ends):
                out.omitted.append(f"wire:{i}")
            else:
                kept.append(i)
        data["wires"] = [wires[i] for i in kept]
        path = self.resolve(bench_path)
        try:
            bench = BenchDefinition.model_validate(data)
            session = Session(bench, self.files.library(path), base_dir=path.parent)
        except Exception as exc:
            out.error = str(exc).splitlines()[0]
            return out
        try:
            info = self.views.describe(ServedSession("preview", session, path))
        finally:
            session.close()
        for w in info.wires:
            w.index = kept[w.index]
        info.bench_sha256 = doc.sha256
        info.bench_fingerprint = doc.fingerprint
        out.session = info
        return out

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

    def get(self, sid: str) -> ServedSession:
        try:
            return self.sessions[sid]
        except KeyError:
            raise KeyError(sid) from None
