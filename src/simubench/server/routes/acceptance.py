"""The acceptance page: try a definition without a bench (7.2, T2) and run its cases."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...library.coverage import (
    BUILTIN_PREFIX,
    builtin_cases_file,
    builtin_cases_for_model,
    cases_for_model,
    run_cases,
)
from ...models import api as M
from .. import drive, views
from .context import ApiContext


def try_entry(ctx: ApiContext, sid: str) -> Any:
    try:
        return ctx.manager.tries.get(sid)
    except KeyError as exc:
        raise ctx.http_error(exc, missing=f"no try session {sid!r}") from None


def while_idle(e: Any, fn: Any, *args: Any) -> Any:
    """Run ``fn`` on the try session held exclusively: a batch of try_definition or a retirement
    (MAX, replace) waits for it, and one that closed the session meanwhile is a 404."""
    with e.busy:
        if e.closed:
            raise HTTPException(404, "the try session was closed")
        return fn(*args)


def _try_session_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    auth = ctx.auth

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
        except Exception as exc:  # an unknown id, or a definition that loads but does not build
            raise ctx.http_error(exc) from None
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
        e = try_entry(ctx, sid)
        return await asyncio.to_thread(while_idle, e, drive.scpi, e.served, e.definition, req.command)


def _try_panel_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    auth = ctx.auth
    not_found = ctx.not_found

    @router.post(
        "/api/try/{sid}/configure",
        response_model=M.ConfigureResponse,
        operation_id="tryConfigure",
        dependencies=[Depends(auth)],
    )
    async def try_configure(sid: str, req: M.ConfigureRequest) -> M.ConfigureResponse:
        """Set one of the try session's settings (the same interface as a bench session's)."""
        e = try_entry(ctx, sid)
        return await asyncio.to_thread(
            while_idle, e, drive.configure, e.served, e.definition, req.path, req.value
        )

    @router.post(
        "/api/try/{sid}/panel",
        response_model=M.PanelResponse,
        operation_id="tryPanel",
        dependencies=[Depends(auth)],
    )
    async def try_panel(sid: str, req: M.PanelRequest) -> M.PanelResponse:
        """A front-panel button on the try session's instrument (D32)."""
        e = try_entry(ctx, sid)
        return await asyncio.to_thread(while_idle, e, drive.panel, e.served, e.definition, req.action)

    @router.get(
        "/api/try/{sid}/instrument",
        response_model=M.InstrumentInfo,
        operation_id="getTryInstrument",
        dependencies=[Depends(auth)],
    )
    def try_instrument(sid: str) -> M.InstrumentInfo:
        """The try session's instrument: identity, ports, settings, front panel and deviations (the
        acceptance page's overview and settings tables)."""
        e = try_entry(ctx, sid)
        name = e.definition
        try:
            inst = e.served.session.instrument(name)
        except KeyError as exc:
            raise not_found(exc) from None
        index = list(e.served.session.instruments).index(name)
        info = manager.views.describe_instrument(e.served.session, inst, index, None)
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
        e = try_entry(ctx, sid)
        try:
            return views.internals(e.served, e.definition)
        except KeyError as exc:
            raise not_found(exc) from None


def _try_routes(ctx: ApiContext, router: APIRouter) -> None:
    _try_session_routes(ctx, router)
    _try_panel_routes(ctx, router)


def _definition_case_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    library = ctx.library
    files = ctx.files
    auth = ctx.auth
    not_found = ctx.not_found

    def _case_ancestors(def_id: str) -> list[str]:
        """The models a workspace definition is a copy of (extends, nearest first): their shipped
        cases are its regression cases. A built-in model has its own cases only."""
        instruments = (manager.workspace / "instruments").resolve()
        if instruments not in library.path(def_id).resolve().parents:
            return []
        return [m for m, _ in library.ancestors(def_id)]

    def _is_cases_of(name: str, model: str) -> bool:
        try:
            builtin_cases_file(name, model)
        except ValueError:
            return False
        return True

    @router.get(
        "/api/definitions/{def_id}/cases",
        response_model=list[M.CaseFile],
        operation_id="listDefinitionCases",
        dependencies=[Depends(auth)],
    )
    def definition_cases(def_id: str) -> list[M.CaseFile]:
        """The cases files whose model is this definition (the acceptance page's case tab): the manual
        cases shipped with a built-in model first (#1352), then the workspace's."""
        try:
            library.get(def_id)
        except KeyError as exc:
            raise not_found(exc) from None
        shipped = [M.CaseFile(path=p, cases=n, builtin=True) for p, n in builtin_cases_for_model(def_id)]
        inherited = [
            M.CaseFile(path=p, cases=n, builtin=True, from_model=parent)
            for parent in _case_ancestors(def_id)
            for p, n in builtin_cases_for_model(parent)
        ]
        own = [M.CaseFile(path=p, cases=n) for p, n in cases_for_model(manager.workspace, def_id)]
        return shipped + inherited + own

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
            # A built-in model's manual cases (#1352) are named, not given as a path: only that
            # model's own file can be run that way.
            model = None  # a copy runs the cases of the model it is based on in that model's place
            if req.path.startswith(BUILTIN_PREFIX):
                owner = next(
                    (m for m in [def_id, *_case_ancestors(def_id)] if _is_cases_of(req.path, m)),
                    def_id,  # none: builtin_cases_file below says why
                )
                cases_file = builtin_cases_file(req.path, owner)
                model = def_id if owner != def_id else None
            else:
                cases_file = files.resolve(req.path)
            results = run_cases(cases_file, [manager.workspace / "instruments"], collect=True, model=model)
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
        except Exception as exc:
            raise ctx.http_error(exc, missing="no such cases file") from None


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _try_routes(ctx, router)
    _definition_case_routes(ctx, router)
    return router
