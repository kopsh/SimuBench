"""Served sessions: lifecycle, instruments, nets, clock and faults."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from ...models import api as M
from ...models.bench import Fault
from .. import drive, views
from ..manager import ClientsConnected, InstrumentRemote
from ..services import clock as clock_service
from ..services import faults
from .context import IN_USE, ApiContext


def instrument_info(ctx: ApiContext, sid: str, name: str) -> M.InstrumentInfo:
    s = ctx.served(sid)
    try:
        inst = s.session.instrument(name)
    except KeyError as exc:
        raise ctx.not_found(exc) from None
    index = list(s.session.instruments).index(name)
    port = next((lst.port for lst in (s.scpi.listeners if s.scpi else []) if lst.instrument == name), None)
    info = ctx.manager.views.describe_instrument(
        s.session,
        inst,
        index,
        port,
        s.hislip.addresses().get(name) if s.hislip else None,
        s.vxi11.addresses().get(name) if s.vxi11 else None,
    )
    info.remote, info.local = s.remote_clients(name), name in s.local
    return info


def _lifecycle_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    auth = ctx.auth
    served = ctx.served
    in_use = ctx.in_use

    @router.get(
        "/api/sessions",
        response_model=list[M.SessionInfo],
        operation_id="listSessions",
        dependencies=[Depends(auth)],
    )
    def sessions() -> list[M.SessionInfo]:
        return [manager.views.describe(s) for s in manager.sessions.values()]

    @router.post(
        "/api/sessions",
        response_model=M.SessionInfo,
        operation_id="startSession",
        dependencies=[Depends(auth)],
        responses=IN_USE,
    )
    async def start(req: M.StartSessionRequest) -> M.SessionInfo:
        try:
            # The clients guard lives in manager.start: starting a bench stops the running one,
            # whatever facade asked for it (#121, issue 624).
            s = await manager.start(
                req.bench_path, clock=req.clock, seed=req.seed, force=req.force, time_scale=req.time_scale
            )
        except Exception as exc:
            err = ctx.lifecycle_error(exc)
            if err is None:
                raise
            raise err from None
        return manager.views.describe(s)

    @router.get(
        "/api/sessions/{sid}",
        response_model=M.SessionInfo,
        operation_id="getSession",
        dependencies=[Depends(auth)],
    )
    def get_session(sid: str) -> M.SessionInfo:
        return manager.views.describe(served(sid))

    @router.delete(
        "/api/sessions/{sid}",
        status_code=204,
        operation_id="stopSession",
        dependencies=[Depends(auth)],
        responses={409: {"description": "Scripts are connected; repeat with force=true to drop them"}},
    )
    async def stop(sid: str, force: bool = Query(False)) -> None:
        served(sid)  # 404 for an unknown session; manager.stop returns silently for one
        try:
            await manager.stop(sid, force=force)
        except ClientsConnected as exc:
            raise in_use(exc) from None

    @router.post(
        "/api/sessions/{sid}/reset",
        response_model=M.SessionInfo,
        operation_id="resetSession",
        dependencies=[Depends(auth)],
        responses=IN_USE,
    )
    async def reset(sid: str, req: M.ResetSessionRequest) -> M.SessionInfo:
        """Power-cycle the bench: fresh instruments, a new record, the same session id."""
        try:
            s = await manager.reset(sid, clock=req.clock, seed=req.seed, force=req.force)
        except Exception as exc:
            err = ctx.lifecycle_error(exc, files=False)
            if err is None:
                raise
            raise err from None
        return manager.views.describe(s)


def _instrument_routes(ctx: ApiContext, router: APIRouter) -> None:
    auth = ctx.auth
    client_of = ctx.client_of
    served = ctx.served
    not_found = ctx.not_found
    in_use = ctx.in_use

    @router.get(
        "/api/sessions/{sid}/instruments/{name}",
        response_model=M.InstrumentInfo,
        operation_id="getInstrument",
        dependencies=[Depends(auth)],
    )
    def instrument(sid: str, name: str) -> M.InstrumentInfo:
        return instrument_info(ctx, sid, name)

    @router.get(
        "/api/sessions/{sid}/instruments/{name}/internals",
        response_model=M.InstrumentInternals,
        operation_id="getInstrumentInternals",
        dependencies=[Depends(auth)],
    )
    def internals(sid: str, name: str) -> M.InstrumentInternals:
        s = served(sid)
        try:
            return views.internals(s, name)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.post(
        "/api/sessions/{sid}/instruments/{name}/scpi",
        response_model=M.ScpiResponse,
        operation_id="sendScpi",
        dependencies=[Depends(auth)],
        responses=IN_USE,
    )
    async def scpi(
        sid: str, name: str, req: M.ScpiRequest, client: str = Depends(client_of)
    ) -> M.ScpiResponse:
        s = served(sid)
        try:
            s.require_local(name)
            return await asyncio.to_thread(drive.scpi, s, name, req.command, client)
        except KeyError as exc:
            raise not_found(exc) from None
        except InstrumentRemote as exc:
            raise in_use(exc) from None

    @router.post(
        "/api/sessions/{sid}/instruments/{name}/configure",
        response_model=M.ConfigureResponse,
        operation_id="configure",
        dependencies=[Depends(auth)],
        responses=IN_USE,
    )
    async def configure(
        sid: str, name: str, req: M.ConfigureRequest, client: str = Depends(client_of)
    ) -> M.ConfigureResponse:
        s = served(sid)
        try:
            s.require_local(name)
            return await asyncio.to_thread(drive.configure, s, name, req.path, req.value, client)
        except KeyError as exc:
            raise not_found(exc) from None
        except InstrumentRemote as exc:
            raise in_use(exc) from None


def _panel_routes(ctx: ApiContext, router: APIRouter) -> None:
    auth = ctx.auth
    client_of = ctx.client_of
    served = ctx.served
    not_found = ctx.not_found
    in_use = ctx.in_use

    @router.get(
        "/api/sessions/{sid}/instruments/{name}/screen",
        response_model=M.ScopeScreen,
        operation_id="getScopeScreen",
        dependencies=[Depends(auth)],
    )
    async def screen(sid: str, name: str, points: int | None = Query(None, ge=2, le=4096)) -> M.ScopeScreen:
        """``points`` (tui-screens.md 6.1): return per-bin min/max instead of every sample."""
        s = served(sid)
        try:
            return await asyncio.to_thread(views.scope_screen, s, name, points)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.post(
        "/api/sessions/{sid}/instruments/{name}/panel",
        response_model=M.PanelResponse,
        operation_id="panel",
        dependencies=[Depends(auth)],
        responses=IN_USE,
    )
    async def panel(
        sid: str, name: str, req: M.PanelRequest, client: str = Depends(client_of)
    ) -> M.PanelResponse:
        """A front-panel button (Run, Stop, Single), inside the instrument: not a SCPI command (D32)."""
        s = served(sid)
        try:
            s.require_local(name)
            return await asyncio.to_thread(drive.panel, s, name, req.action, client)
        except KeyError as exc:
            raise not_found(exc) from None
        except InstrumentRemote as exc:
            raise in_use(exc) from None

    @router.post(
        "/api/sessions/{sid}/instruments/{name}/local",
        response_model=M.InstrumentInfo,
        operation_id="goLocal",
        dependencies=[Depends(auth)],
    )
    def go_local(sid: str, name: str, client: str = Depends(client_of)) -> M.InstrumentInfo:
        """LOCAL: take an instrument in remote control back to the UI, until a remote client's next
        command (as the LOCAL key of an instrument). Recorded as a ``remote`` event (#121)."""
        s = served(sid)
        try:
            s.go_local(name, client)
        except KeyError as exc:
            raise not_found(exc) from None
        return instrument_info(ctx, sid, name)

    @router.get(
        "/api/sessions/{sid}/levels",
        response_model=M.NetLevels,
        operation_id="getNetLevels",
        dependencies=[Depends(auth)],
    )
    async def net_levels(sid: str) -> M.NetLevels:
        """Every net's level and a thumbnail, for the canvas (D44)."""
        return await asyncio.to_thread(views.net_levels, served(sid))


def _net_and_clock_routes(ctx: ApiContext, router: APIRouter) -> None:
    auth = ctx.auth
    served = ctx.served
    not_found = ctx.not_found
    in_use = ctx.in_use

    @router.get(
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
            return await asyncio.to_thread(views.net_waveform, s, net, window_s, points)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.get(
        "/api/sessions/{sid}/nets/{net}/probe",
        response_model=M.NetProbe,
        operation_id="probeNet",
        dependencies=[Depends(auth)],
    )
    async def probe_net(sid: str, net: str) -> M.NetProbe:
        """A virtual probe: the net's signal without loading it, broken down by source (M4)."""
        s = served(sid)
        try:
            return await asyncio.to_thread(views.probe, s, net)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.get(
        "/api/sessions/{sid}/assets",
        response_model=M.AssetRegister,
        operation_id="getAssets",
        dependencies=[Depends(auth)],
    )
    def get_assets(sid: str) -> M.AssetRegister:
        """The virtual calibration register, with due dates against the bench's calendar."""
        from ...engine.calibration import register_view

        s = served(sid).session
        now = s.calendar()
        return M.AssetRegister(calendar=now.isoformat(), assets=register_view(s.bench.assets, now.date()))

    @router.get(
        "/api/sessions/{sid}/clock",
        response_model=M.ClockInfo,
        operation_id="getClock",
        dependencies=[Depends(auth)],
    )
    def clock(sid: str) -> M.ClockInfo:
        return clock_service.info(served(sid))

    @router.post(
        "/api/sessions/{sid}/clock/advance",
        response_model=M.ClockInfo,
        operation_id="advanceClock",
        dependencies=[Depends(auth)],
        responses=IN_USE,
    )
    def advance(sid: str, req: M.AdvanceRequest) -> M.ClockInfo:
        try:
            return clock_service.advance(served(sid), req.seconds, force=req.force)
        except ClientsConnected as exc:  # a RuntimeError: map before the plain 409 below
            raise in_use(exc) from None
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None


def _reading_and_fault_routes(ctx: ApiContext, router: APIRouter) -> None:
    auth = ctx.auth
    client_of = ctx.client_of
    served = ctx.served
    not_found = ctx.not_found

    @router.post(
        "/api/sessions/{sid}/clock/scale",
        response_model=M.ClockInfo,
        operation_id="setClockScale",
        dependencies=[Depends(auth)],
    )
    def clock_scale(sid: str, req: M.TimeScaleRequest) -> M.ClockInfo:
        """Change a scaled clock's rate while it runs; time already passed stays passed."""
        try:
            return clock_service.set_scale(served(sid), req.scale)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get(
        "/api/sessions/{sid}/instruments/{name}/readings",
        response_model=M.InstrumentReadings,
        operation_id="instrumentReadings",
        dependencies=[Depends(auth)],
    )
    def readings(sid: str, name: str) -> M.InstrumentReadings:
        try:
            return views.readings(served(sid), name)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.get(
        "/api/sessions/{sid}/faults",
        response_model=list[M.FaultInfo],
        operation_id="listFaults",
        dependencies=[Depends(auth)],
    )
    def list_faults(sid: str) -> list[M.FaultInfo]:
        return faults.list_faults(served(sid))

    @router.post(
        "/api/sessions/{sid}/faults",
        response_model=M.FaultInfo,
        operation_id="injectFault",
        dependencies=[Depends(auth)],
    )
    def inject_fault(sid: str, spec: Fault, client: str = Depends(client_of)) -> M.FaultInfo:
        try:
            return faults.inject(served(sid), spec, source=client)
        except KeyError as exc:
            raise not_found(exc) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.delete(
        "/api/sessions/{sid}/faults/{fault_id}",
        response_model=M.FaultInfo,
        operation_id="clearFault",
        dependencies=[Depends(auth)],
    )
    def clear_fault(sid: str, fault_id: str) -> M.FaultInfo:
        try:
            return faults.clear(served(sid), fault_id)
        except KeyError as exc:
            raise not_found(exc) from None


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _lifecycle_routes(ctx, router)
    _instrument_routes(ctx, router)
    _panel_routes(ctx, router)
    _net_and_clock_routes(ctx, router)
    _reading_and_fault_routes(ctx, router)
    return router
