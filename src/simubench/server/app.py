"""FastAPI application of ``simubench serve`` (D18, D21).

Security for a local service (roadmap.md, D22): the service listens on the loopback interface,
rejects requests whose Host or Origin header is not a loopback name, so that a web page cannot reach
it through DNS rebinding, and, when started with an access token (opt-in, D43), requires it on every
data route.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..engine.edit import EditError
from ..engine.faults import FaultState
from ..engine.record import encode_bytes
from ..library.loader import DefinitionLibrary
from ..models import api as M
from ..models.bench import Fault
from ..models.definition import InstrumentDefinition
from .benchfiles import Conflict, diagnostics
from .catalog import components as component_catalog
from .manager import BenchInvalid, ServedSession, SessionManager, _plain

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
#: The built UI: inside an installed wheel (scripts/build-wheel.sh), else web/dist in a checkout.
_PACKAGED_UI = Path(__file__).resolve().parents[1] / "web_dist"
WEB_DIST = (
    _PACKAGED_UI
    if (_PACKAGED_UI / "index.html").exists()
    else Path(__file__).resolve().parents[3] / "web" / "dist"
)


def _host_ok(value: str | None) -> bool:
    if not value:
        return True
    host = value.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in LOOPBACK_HOSTS


class LocalOnly(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not _host_ok(request.headers.get("host")) or not _host_ok(request.headers.get("origin")):
            return JSONResponse({"detail": "only loopback hosts and origins are allowed"}, status_code=403)
        return await call_next(request)


def create_app(manager: SessionManager, token: str | None = None, web_dist: Path | None = None) -> FastAPI:
    """``token``: when given, every data route and the event socket require it; None serves without one."""
    app = FastAPI(
        title="SimuBench", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.state.token = token
    app.state.manager = manager
    app.add_middleware(LocalOnly)
    library = DefinitionLibrary([manager.workspace / "instruments"])

    def auth(request: Request) -> None:
        if token is None:
            return
        supplied = request.headers.get("x-simubench-token") or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="missing or wrong access token")

    def served(sid: str) -> ServedSession:
        try:
            return manager.get(sid)
        except KeyError:
            raise HTTPException(404, f"no session {sid!r}") from None

    def not_found(exc: KeyError) -> HTTPException:
        return HTTPException(404, str(exc).strip("'\""))

    @app.get("/api/health", response_model=M.Health, operation_id="health")
    def health() -> M.Health:
        return M.Health(version=__version__)

    @app.get(
        "/api/benches",
        response_model=list[M.BenchFile],
        operation_id="listBenches",
        dependencies=[Depends(auth)],
    )
    def benches() -> list[M.BenchFile]:
        return manager.bench_files()

    files = manager.files

    def file_errors(fn: Any) -> Any:
        try:
            return fn()
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "no such bench file") from None
        except Conflict as exc:
            raise HTTPException(409, {"message": str(exc), "current_sha256": exc.current_sha256}) from None
        except EditError as exc:
            raise HTTPException(422, str(exc)) from None
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None

    @app.get(
        "/api/benches/file",
        response_model=M.BenchDocument,
        operation_id="readBench",
        dependencies=[Depends(auth)],
    )
    def read_bench(path: str = Query(...)) -> M.BenchDocument:
        return file_errors(lambda: files.read(path))  # type: ignore[no-any-return]

    @app.get(
        "/api/benches/preview",
        response_model=M.BenchPreview,
        operation_id="previewBench",
        dependencies=[Depends(auth)],
    )
    def preview_bench(path: str = Query(...)) -> M.BenchPreview:
        return file_errors(lambda: manager.preview(path))  # type: ignore[no-any-return]

    @app.put(
        "/api/benches/file",
        response_model=M.BenchDocument,
        operation_id="saveBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def save_bench(req: M.SaveBenchRequest) -> M.BenchDocument:
        return file_errors(lambda: files.save(req.path, req.base_sha256, req.data, req.source))  # type: ignore[no-any-return]

    @app.post(
        "/api/benches/edit",
        response_model=M.BenchDocument,
        operation_id="editBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def edit_bench(req: M.EditBenchRequest) -> M.BenchDocument:
        return file_errors(lambda: files.edit(req.path, req.base_sha256, req.ops, req.source))  # type: ignore[no-any-return]

    @app.get(
        "/api/benches/history",
        response_model=list[M.BenchVersion],
        operation_id="benchHistory",
        dependencies=[Depends(auth)],
    )
    def bench_history(path: str = Query(...)) -> list[M.BenchVersion]:
        """Saved versions of a bench file, newest first (D35)."""
        return file_errors(lambda: files.versions(path))  # type: ignore[no-any-return]

    @app.get(
        "/api/benches/version",
        response_model=M.BenchVersionDocument,
        operation_id="readBenchVersion",
        dependencies=[Depends(auth)],
    )
    def read_bench_version(path: str = Query(...), seq: int = Query(...)) -> M.BenchVersionDocument:
        return file_errors(lambda: files.version(path, seq))  # type: ignore[no-any-return]

    @app.post(
        "/api/benches/restore",
        response_model=M.BenchDocument,
        operation_id="restoreBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def restore_bench(req: M.RestoreBenchRequest) -> M.BenchDocument:
        """Write an earlier version back as the newest one; later versions stay in the history."""
        return file_errors(lambda: files.restore(req.path, req.seq, req.base_sha256))  # type: ignore[no-any-return]

    @app.post(
        "/api/benches/validate",
        response_model=M.ValidateResponse,
        operation_id="validateBench",
        dependencies=[Depends(auth)],
    )
    def validate_bench(req: M.ValidateRequest) -> M.ValidateResponse:
        report = file_errors(lambda: files.validate(req.data, req.path))
        return M.ValidateResponse(ok=report.ok, diagnostics=diagnostics(report))

    @app.get(
        "/api/metrics",
        operation_id="getMetrics",
        dependencies=[Depends(auth)],
        response_class=Response,
        responses={200: {"content": {"text/plain": {}}}},
    )
    def get_metrics() -> Response:
        """Prometheus text format: sessions, events by kind, circuit solves, virtual time (M4)."""
        lines = [
            "# HELP simubench_info SimuBench version.",
            "# TYPE simubench_info gauge",
            f'simubench_info{{version="{__version__}"}} 1',
            "# HELP simubench_sessions Running sessions.",
            "# TYPE simubench_sessions gauge",
            f"simubench_sessions {len(manager.sessions)}",
            "# HELP simubench_events_total Session events by kind.",
            "# TYPE simubench_events_total counter",
        ]
        rest: list[str] = [
            "# HELP simubench_solves_total Circuit solves.",
            "# TYPE simubench_solves_total counter",
        ]
        timing = [
            "# HELP simubench_solve_seconds_total Time spent solving circuits.",
            "# TYPE simubench_solve_seconds_total counter",
        ]
        clock = [
            "# HELP simubench_virtual_seconds Virtual time of a session.",
            "# TYPE simubench_virtual_seconds gauge",
        ]
        for sid, served in list(manager.sessions.items()):
            s = served.session
            label = f'session="{sid}",bench="{s.bench.name}"'
            for kind, n in sorted(s.stats.items()):
                if kind not in ("solves", "solve_seconds"):
                    lines.append(f'simubench_events_total{{{label},kind="{kind}"}} {int(n)}')
            rest.append(f"simubench_solves_total{{{label}}} {int(s.stats.get('solves', 0))}")
            timing.append(f"simubench_solve_seconds_total{{{label}}} {s.stats.get('solve_seconds', 0.0):.6f}")
            clock.append(f"simubench_virtual_seconds{{{label}}} {s.now_ns / 1e9:.9f}")
        body = "\n".join([*lines, *rest, *timing, *clock]) + "\n"
        return Response(body, media_type="text/plain; version=0.0.4")

    @app.get("/api/manifest", operation_id="getManifest", dependencies=[Depends(auth)])
    def get_manifest() -> dict[str, Any]:
        """Version manifest: engine, dependencies, definitions and their hashes, web UI build."""
        from ..ops import manifest

        return manifest(manager.files.workspace, manager.record_dir)

    @app.get("/api/doctor", operation_id="getDoctor", dependencies=[Depends(auth)])
    def get_doctor() -> list[dict[str, str]]:
        from dataclasses import asdict

        from ..ops import doctor

        return [asdict(c) for c in doctor(manager.files.workspace)]

    @app.get(
        "/api/diagnostics",
        operation_id="getDiagnostics",
        dependencies=[Depends(auth)],
        response_class=Response,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def get_diagnostics() -> Response:
        """A diagnostics bundle (zip) to attach to a bug report; it contains no access token."""
        from ..ops import bundle

        data = bundle(manager, manager.files.workspace, record_dir=manager.record_dir)
        name = f"simubench-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get(
        "/api/runs", response_model=list[M.RunSummary], operation_id="listRuns", dependencies=[Depends(auth)]
    )
    def list_runs() -> list[M.RunSummary]:
        """Run records in the record folder, newest first (empty without --record-dir)."""
        return manager.runs.list()

    @app.get(
        "/api/runs/{run_id}", response_model=M.RunDetail, operation_id="readRun", dependencies=[Depends(auth)]
    )
    def read_run(run_id: str) -> M.RunDetail:
        try:
            return manager.runs.detail(run_id)
        except FileNotFoundError:
            raise HTTPException(404, "no such run record") from None

    @app.post(
        "/api/runs/{run_id}/replay",
        response_model=M.ReplayResult,
        operation_id="replayRun",
        dependencies=[Depends(auth)],
    )
    def replay_run(run_id: str) -> M.ReplayResult:
        """Replay the record and compare every reply (D36)."""
        try:
            return manager.runs.replay(run_id)
        except FileNotFoundError:
            raise HTTPException(404, "no such run record") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get(
        "/api/catalog",
        response_model=M.Catalog,
        operation_id="getCatalog",
        dependencies=[Depends(auth)],
    )
    def catalog() -> M.Catalog:
        return files.catalog()

    @app.get(
        "/api/components",
        response_model=list[M.ComponentDoc],
        operation_id="listComponents",
        dependencies=[Depends(auth)],
    )
    def list_components() -> list[M.ComponentDoc]:
        """Every component type, internal ones included: the building blocks of instrument definitions."""
        return component_catalog()

    @app.get(
        "/api/definitions",
        response_model=list[str],
        operation_id="listDefinitions",
        dependencies=[Depends(auth)],
    )
    def definitions() -> list[str]:
        return library.ids()

    @app.get(
        "/api/definitions/{def_id}",
        response_model=InstrumentDefinition,
        response_model_by_alias=True,
        operation_id="getDefinition",
        dependencies=[Depends(auth)],
    )
    def definition(def_id: str) -> InstrumentDefinition:
        try:
            return library.get(def_id)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.get(
        "/api/sessions",
        response_model=list[M.SessionInfo],
        operation_id="listSessions",
        dependencies=[Depends(auth)],
    )
    def sessions() -> list[M.SessionInfo]:
        return [manager.describe(s) for s in manager.sessions.values()]

    @app.post(
        "/api/sessions",
        response_model=M.SessionInfo,
        operation_id="startSession",
        dependencies=[Depends(auth)],
    )
    async def start(req: M.StartSessionRequest) -> M.SessionInfo:
        try:
            s = await manager.start(req.bench_path, clock=req.clock, seed=req.seed)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from None
        except BenchInvalid as exc:
            raise HTTPException(
                422, {"message": str(exc), "diagnostics": [d.model_dump() for d in exc.diagnostics]}
            ) from None
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None
        return manager.describe(s)

    @app.get(
        "/api/sessions/{sid}",
        response_model=M.SessionInfo,
        operation_id="getSession",
        dependencies=[Depends(auth)],
    )
    def get_session(sid: str) -> M.SessionInfo:
        return manager.describe(served(sid))

    @app.delete(
        "/api/sessions/{sid}", status_code=204, operation_id="stopSession", dependencies=[Depends(auth)]
    )
    async def stop(sid: str) -> None:
        served(sid)
        await manager.stop(sid)

    @app.post(
        "/api/sessions/{sid}/reset",
        response_model=M.SessionInfo,
        operation_id="resetSession",
        dependencies=[Depends(auth)],
    )
    async def reset(sid: str, req: M.ResetSessionRequest) -> M.SessionInfo:
        """Power-cycle the bench: fresh instruments, a new record, the same session id."""
        try:
            served(sid)
            s = await manager.reset(sid, clock=req.clock, seed=req.seed)
        except BenchInvalid as exc:
            raise HTTPException(
                422, {"message": str(exc), "diagnostics": [d.model_dump() for d in exc.diagnostics]}
            ) from None
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None
        return manager.describe(s)

    @app.get(
        "/api/sessions/{sid}/instruments/{name}",
        response_model=M.InstrumentInfo,
        operation_id="getInstrument",
        dependencies=[Depends(auth)],
    )
    def instrument(sid: str, name: str) -> M.InstrumentInfo:
        s = served(sid)
        try:
            inst = s.session.instrument(name)
        except KeyError as exc:
            raise not_found(exc) from None
        index = list(s.session.instruments).index(name)
        port = next(
            (lst.port for lst in (s.scpi.listeners if s.scpi else []) if lst.instrument == name), None
        )
        return manager.describe_instrument(s.session, inst, index, port)

    @app.get(
        "/api/sessions/{sid}/instruments/{name}/internals",
        response_model=M.InstrumentInternals,
        operation_id="getInstrumentInternals",
        dependencies=[Depends(auth)],
    )
    def internals(sid: str, name: str) -> M.InstrumentInternals:
        s = served(sid)
        try:
            return manager.internals(s, name)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.post(
        "/api/sessions/{sid}/instruments/{name}/scpi",
        response_model=M.ScpiResponse,
        operation_id="sendScpi",
        dependencies=[Depends(auth)],
    )
    async def scpi(sid: str, name: str, req: M.ScpiRequest) -> M.ScpiResponse:
        s = served(sid)
        try:
            return await asyncio.to_thread(manager.scpi, s, name, req.command)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.post(
        "/api/sessions/{sid}/instruments/{name}/configure",
        response_model=M.ConfigureResponse,
        operation_id="configure",
        dependencies=[Depends(auth)],
    )
    async def configure(sid: str, name: str, req: M.ConfigureRequest) -> M.ConfigureResponse:
        s = served(sid)
        try:
            return await asyncio.to_thread(manager.configure, s, name, req.path, req.value)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.get(
        "/api/sessions/{sid}/instruments/{name}/screen",
        response_model=M.ScopeScreen,
        operation_id="getScopeScreen",
        dependencies=[Depends(auth)],
    )
    async def screen(sid: str, name: str) -> M.ScopeScreen:
        s = served(sid)
        try:
            return await asyncio.to_thread(manager.scope_screen, s, name)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.post(
        "/api/sessions/{sid}/instruments/{name}/panel",
        response_model=M.PanelResponse,
        operation_id="panel",
        dependencies=[Depends(auth)],
    )
    async def panel(sid: str, name: str, req: M.PanelRequest) -> M.PanelResponse:
        """A front-panel button (Run, Stop, Single), inside the instrument: not a SCPI command (D32)."""
        s = served(sid)
        try:
            return await asyncio.to_thread(manager.panel, s, name, req.action)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.get(
        "/api/sessions/{sid}/levels",
        response_model=M.NetLevels,
        operation_id="getNetLevels",
        dependencies=[Depends(auth)],
    )
    async def net_levels(sid: str) -> M.NetLevels:
        """Every net's level and a thumbnail, for the canvas (D44)."""
        return await asyncio.to_thread(manager.net_levels, served(sid))

    @app.get(
        "/api/sessions/{sid}/nets/{net}/waveform",
        response_model=M.NetWaveform,
        operation_id="getNetWaveform",
        dependencies=[Depends(auth)],
    )
    async def net_waveform(
        sid: str, net: str, window_s: float = Query(2e-3, gt=0), points: int = Query(500, ge=2, le=20000)
    ) -> M.NetWaveform:
        s = served(sid)
        try:
            return await asyncio.to_thread(manager.net_waveform, s, net, window_s, points)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.get(
        "/api/sessions/{sid}/nets/{net}/probe",
        response_model=M.NetProbe,
        operation_id="probeNet",
        dependencies=[Depends(auth)],
    )
    async def probe_net(sid: str, net: str) -> M.NetProbe:
        """A virtual probe: the net's signal without loading it, broken down by source (M4)."""
        s = served(sid)
        try:
            return await asyncio.to_thread(manager.probe, s, net)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.get(
        "/api/sessions/{sid}/assets",
        response_model=M.AssetRegister,
        operation_id="getAssets",
        dependencies=[Depends(auth)],
    )
    def get_assets(sid: str) -> M.AssetRegister:
        """The virtual calibration register, with due dates against the bench's calendar."""
        from ..engine.calibration import register_view

        s = served(sid).session
        now = s.calendar()
        return M.AssetRegister(calendar=now.isoformat(), assets=register_view(s.bench.assets, now.date()))

    @app.get(
        "/api/sessions/{sid}/clock",
        response_model=M.ClockInfo,
        operation_id="getClock",
        dependencies=[Depends(auth)],
    )
    def clock(sid: str) -> M.ClockInfo:
        s = served(sid).session
        return M.ClockInfo(mode=s.clock_mode, now_ns=s.now_ns)  # type: ignore[arg-type]

    @app.post(
        "/api/sessions/{sid}/clock/advance",
        response_model=M.ClockInfo,
        operation_id="advanceClock",
        dependencies=[Depends(auth)],
    )
    def advance(sid: str, req: M.AdvanceRequest) -> M.ClockInfo:
        s = served(sid).session
        try:
            s.advance(req.seconds)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return M.ClockInfo(mode=s.clock_mode, now_ns=s.now_ns)  # type: ignore[arg-type]

    @app.get(
        "/api/sessions/{sid}/instruments/{name}/readings",
        response_model=M.InstrumentReadings,
        operation_id="instrumentReadings",
        dependencies=[Depends(auth)],
    )
    def readings(sid: str, name: str) -> M.InstrumentReadings:
        try:
            return manager.readings(served(sid), name)
        except KeyError as exc:
            raise not_found(exc) from None

    @app.get(
        "/api/sessions/{sid}/faults",
        response_model=list[M.FaultInfo],
        operation_id="listFaults",
        dependencies=[Depends(auth)],
    )
    def list_faults(sid: str) -> list[M.FaultInfo]:
        s = served(sid)
        with s.session._lock:
            return [fault_info(f) for f in s.session.faults.snapshot()]

    @app.post(
        "/api/sessions/{sid}/faults",
        response_model=M.FaultInfo,
        operation_id="injectFault",
        dependencies=[Depends(auth)],
    )
    def inject_fault(sid: str, spec: Fault) -> M.FaultInfo:
        s = served(sid)
        try:
            with s.session._lock:
                return fault_info(s.session.faults.inject(spec, source="ui"))
        except KeyError as exc:
            raise not_found(exc) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.delete(
        "/api/sessions/{sid}/faults/{fault_id}",
        response_model=M.FaultInfo,
        operation_id="clearFault",
        dependencies=[Depends(auth)],
    )
    def clear_fault(sid: str, fault_id: str) -> M.FaultInfo:
        s = served(sid)
        try:
            with s.session._lock:
                return fault_info(s.session.faults.clear(fault_id))
        except KeyError as exc:
            raise not_found(exc) from None

    @app.websocket("/api/sessions/{sid}/events")
    async def events(ws: WebSocket, sid: str) -> None:
        if not _host_ok(ws.headers.get("host")) or not _host_ok(ws.headers.get("origin")):
            await ws.close(code=4403)
            return
        supplied = ws.query_params.get("token") or ""
        if token is not None and not secrets.compare_digest(supplied, token):
            await ws.close(code=4401)
            return
        try:
            s = manager.get(sid)
        except KeyError:
            await ws.close(code=4404)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        counter = {"seq": 0}

        def push(ev: dict[str, Any]) -> None:
            counter["seq"] += 1
            msg = to_event(ev, counter["seq"])
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_put, queue, msg)

        with s.session._lock:  # backlog and subscription without a gap or a duplicate
            backlog = list(s.session.history)
            unsubscribe = s.session.subscribe(push)
        for ev in backlog:
            counter["seq"] += 1
            await ws.send_json(to_event(ev, counter["seq"]))
        try:
            while True:
                msg = await queue.get()
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            unsubscribe()

    dist = web_dist or WEB_DIST
    if dist.is_dir() and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = dist / path
            if path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def _put(queue: asyncio.Queue[dict[str, Any]], msg: dict[str, Any]) -> None:
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()  # drop the oldest rather than block the bench
    queue.put_nowait(msg)


def to_event(ev: dict[str, Any], seq: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "seq": ev.get("eid", seq),
        "t_ns": int(ev.get("t_ns", 0)),
        "kind": ev.get("kind", "unknown"),
    }
    for key in (
        "instrument",
        "dir",
        "duration_ns",
        "client",
        "path",
        "source",
        "code",
        "message",
        "dt_ns",
        "reason",
        "wall",
        "fault",
        "action",
    ):
        if key in ev:
            out[key] = ev[key]
    if "old" in ev:
        out["old"] = _plain(ev["old"])
    if "new" in ev:
        out["new"] = _plain(ev["new"])
    data = ev.get("data")
    if isinstance(data, bytes):
        enc = encode_bytes(data)
        out["size"] = len(data)
        out["text"] = enc.get("text") if "text" in enc else _summary(data)
    return M.Event.model_validate(out).model_dump(exclude_none=True)


def fault_info(f: FaultState) -> M.FaultInfo:
    return M.FaultInfo(
        id=f.id,
        status=f.status,
        description=f.describe(),
        source=f.source,
        started_ns=f.started_ns,
        ends_ns=f.ends_ns,
        remaining=f.remaining,
        spec=f.spec,
    )


def _summary(data: bytes) -> str:
    """Printable summary of binary SCPI data: the block header and the byte count."""
    if data.startswith(b"#") and len(data) > 2 and data[1:2].isdigit():
        n = int(data[1:2])
        return data[: 2 + n].decode(errors="replace") + f"… ({len(data)} B)"
    return f"<{len(data)} B binary>"
