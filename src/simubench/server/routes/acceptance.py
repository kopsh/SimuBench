"""The acceptance page: try a definition without a bench (7.2, T2) and run its cases."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...library.coverage import cases_for_model, run_cases
from ...models import api as M
from .context import ApiContext


def build_router(ctx: ApiContext) -> APIRouter:
    manager = ctx.manager
    library = ctx.library
    files = ctx.files
    auth = ctx.auth
    not_found = ctx.not_found
    router = APIRouter()

    # ---- try a definition without a bench (7.2, T2) ---------------------------------------------------

    def try_entry(sid: str) -> Any:
        try:
            return manager.tries.get(sid)
        except KeyError:
            raise HTTPException(404, f"no try session {sid!r}") from None

    def while_idle(e: Any, fn: Any, *args: Any) -> Any:
        """Run ``fn`` on the try session held exclusively: a batch of try_definition or a retirement
        (MAX, replace) waits for it, and one that closed the session meanwhile is a 404."""
        with e.busy:
            if e.closed:
                raise HTTPException(404, "the try session was closed")
            return fn(*args)

    @router.get(
        "/api/try",
        response_model=list[M.TryOpened],
        operation_id="listTrySessions",
        dependencies=[Depends(auth)],
    )
    def try_sessions() -> list[M.TryOpened]:
        """The open try sessions (temporary sessions with one instrument each)."""
        return [
            M.TryOpened(
                session_id=e.served.id,
                definition_id=e.definition,
                instrument=e.definition,
                now_ns=e.served.session.now_ns,
            )
            for e in manager.tries.all()
        ]

    @router.post(
        "/api/try/{def_id}",
        response_model=M.TryOpened,
        operation_id="openTrySession",
        dependencies=[Depends(auth)],
    )
    def open_try(def_id: str) -> M.TryOpened:
        """Open a try session: the definition's instrument alone, no bench file, no SCPI port, no
        run record. Opening the same definition again replaces the previous session."""
        try:
            e = manager.tries.open(def_id)
        except KeyError as exc:
            raise not_found(exc) from None
        except (ValueError, UnicodeDecodeError) as exc:  # the definition loads but does not build
            raise HTTPException(422, str(exc).splitlines()[0]) from None
        return M.TryOpened(
            session_id=e.served.id,
            definition_id=e.definition,
            instrument=e.definition,
            now_ns=e.served.session.now_ns,
        )

    @router.delete(
        "/api/try/{sid}",
        status_code=204,
        operation_id="closeTrySession",
        dependencies=[Depends(auth)],
    )
    def close_try(sid: str) -> None:
        """Close a try session (closing an unknown one does nothing)."""
        manager.tries.close(sid)

    @router.post(
        "/api/try/{sid}/scpi",
        response_model=M.ScpiResponse,
        operation_id="tryScpi",
        dependencies=[Depends(auth)],
    )
    async def try_scpi(sid: str, req: M.ScpiRequest) -> M.ScpiResponse:
        """Send one program message to the try session's instrument (the same reply format as a
        bench session's scpi interface)."""
        e = try_entry(sid)
        return await asyncio.to_thread(while_idle, e, manager.scpi, e.served, e.definition, req.command)

    @router.post(
        "/api/try/{sid}/configure",
        response_model=M.ConfigureResponse,
        operation_id="tryConfigure",
        dependencies=[Depends(auth)],
    )
    async def try_configure(sid: str, req: M.ConfigureRequest) -> M.ConfigureResponse:
        """Set one of the try session's settings (the same interface as a bench session's)."""
        e = try_entry(sid)
        return await asyncio.to_thread(
            while_idle, e, manager.configure, e.served, e.definition, req.path, req.value
        )

    @router.post(
        "/api/try/{sid}/panel",
        response_model=M.PanelResponse,
        operation_id="tryPanel",
        dependencies=[Depends(auth)],
    )
    async def try_panel(sid: str, req: M.PanelRequest) -> M.PanelResponse:
        """A front-panel button on the try session's instrument (D32)."""
        e = try_entry(sid)
        return await asyncio.to_thread(while_idle, e, manager.panel, e.served, e.definition, req.action)

    @router.get(
        "/api/try/{sid}/instrument",
        response_model=M.InstrumentInfo,
        operation_id="getTryInstrument",
        dependencies=[Depends(auth)],
    )
    def try_instrument(sid: str) -> M.InstrumentInfo:
        """The try session's instrument: identity, ports, settings, front panel and deviations (the
        acceptance page's overview and settings tables)."""
        e = try_entry(sid)
        name = e.definition
        try:
            inst = e.served.session.instrument(name)
        except KeyError as exc:
            raise not_found(exc) from None
        index = list(e.served.session.instruments).index(name)
        info = manager.describe_instrument(e.served.session, inst, index, None)
        info.remote, info.local = e.served.remote_clients(name), name in e.served.local
        return info

    @router.get(
        "/api/try/{sid}/internals",
        response_model=M.InstrumentInternals,
        operation_id="getTryInstrumentInternals",
        dependencies=[Depends(auth)],
    )
    def try_internals(sid: str) -> M.InstrumentInternals:
        """The try session's read-only component graph (issue 20), as the acceptance page shows it."""
        e = try_entry(sid)
        try:
            return manager.internals(e.served, e.definition)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.get(
        "/api/definitions/{def_id}/cases",
        response_model=list[M.CaseFile],
        operation_id="listDefinitionCases",
        dependencies=[Depends(auth)],
    )
    def definition_cases(def_id: str) -> list[M.CaseFile]:
        """The workspace's cases files whose model is this definition (the acceptance page's case tab)."""
        try:
            library.get(def_id)
        except KeyError as exc:
            raise not_found(exc) from None
        return [M.CaseFile(path=p, cases=n) for p, n in cases_for_model(manager.workspace, def_id)]

    @router.post(
        "/api/definitions/{def_id}/cases/run",
        response_model=M.CaseRunResult,
        operation_id="runDefinitionCases",
        dependencies=[Depends(auth)],
    )
    async def run_definition_cases(def_id: str, req: M.CaseRunRequest) -> M.CaseRunResult:
        """Run a cases file the way `simubench cases` does (each case on a freshly powered bench)
        and return every step with what was expected and got."""
        try:
            library.get(def_id)
        except KeyError as exc:
            raise not_found(exc) from None

        def run() -> M.CaseRunResult:
            cases_file = files.resolve(req.path)
            results = run_cases(cases_file, [manager.workspace / "instruments"], collect=True)
            return M.CaseRunResult(
                path=req.path,
                passed=sum(r.passed for r in results),
                failed=sum(not r.passed for r in results),
                cases=[
                    M.CaseRun(
                        name=r.name,
                        manual=r.manual,
                        passed=r.passed,
                        seconds=r.seconds,
                        step=r.step,
                        command=r.command,
                        expected=r.expected,
                        actual=r.actual,
                        error=r.error,
                        steps=[M.StepResult(**vars(s)) for s in r.steps or []],
                    )
                    for r in results
                ],
            )

        try:
            return await asyncio.to_thread(run)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "no such cases file") from None
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None

    return router
