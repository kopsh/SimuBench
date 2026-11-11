"""Operations: metrics, manifest, doctor, diagnostics bundle, run records (M4, D36)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from ... import __version__
from ...models import api as M
from .context import ApiContext


def _status_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    auth = ctx.auth

    @router.get(
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

    @router.get("/api/manifest", operation_id="getManifest", dependencies=[Depends(auth)])
    def get_manifest() -> dict[str, Any]:
        """Version manifest: engine, dependencies, definitions and their hashes, web UI build."""
        from ...ops import manifest

        return manifest(manager.files.workspace, manager.record_dir)

    @router.get("/api/doctor", operation_id="getDoctor", dependencies=[Depends(auth)])
    def get_doctor() -> list[dict[str, str]]:
        from dataclasses import asdict

        from ...ops import doctor

        return [asdict(c) for c in doctor(manager.files.workspace)]


def _diagnostic_and_run_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    auth = ctx.auth

    @router.get(
        "/api/diagnostics",
        operation_id="getDiagnostics",
        dependencies=[Depends(auth)],
        response_class=Response,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def get_diagnostics() -> Response:
        """A diagnostics bundle (zip) to attach to a bug report; it contains no access token."""
        from ...ops import bundle

        data = bundle(manager, manager.files.workspace, record_dir=manager.record_dir)
        name = f"simubench-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @router.get(
        "/api/runs", response_model=list[M.RunSummary], operation_id="listRuns", dependencies=[Depends(auth)]
    )
    def list_runs() -> list[M.RunSummary]:
        """Run records in the record folder, newest first (empty without --record-dir)."""
        return manager.runs.list()

    @router.get(
        "/api/runs/{run_id}", response_model=M.RunDetail, operation_id="readRun", dependencies=[Depends(auth)]
    )
    def read_run(run_id: str) -> M.RunDetail:
        try:
            return manager.runs.detail(run_id)
        except FileNotFoundError:
            raise HTTPException(404, "no such run record") from None

    @router.post(
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


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _status_routes(ctx, router)
    _diagnostic_and_run_routes(ctx, router)
    return router
