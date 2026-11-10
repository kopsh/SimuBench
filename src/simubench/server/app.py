"""FastAPI application of ``simubench serve`` (D18, D21).

Security for a local service (roadmap.md, D22): the service listens on the loopback interface,
rejects requests whose Host or Origin header is not a loopback name, so that a web page cannot reach
it through DNS rebinding, and, when started with an access token (opt-in, D43), requires it on every
data route.

The routes live in ``server/routes/`` (one router per area); this module assembles them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..models import api as M
from .manager import SessionManager
from .routes import acceptance, benches, definitions, events, ops, sessions
from .routes.context import IN_USE, LOOPBACK_HOSTS, ApiContext, host_ok

__all__ = ["IN_USE", "LOOPBACK_HOSTS", "WEB_DIST", "LocalOnly", "create_app"]

#: The built UI: inside an installed wheel (scripts/build-wheel.sh), else web/dist in a checkout.
_PACKAGED_UI = Path(__file__).resolve().parents[1] / "web_dist"
WEB_DIST = (
    _PACKAGED_UI
    if (_PACKAGED_UI / "index.html").exists()
    else Path(__file__).resolve().parents[3] / "web" / "dist"
)

_host_ok = host_ok


class LocalOnly(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not host_ok(request.headers.get("host")) or not host_ok(request.headers.get("origin")):
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
    ctx = ApiContext.of(manager, token)

    @app.get("/api/health", response_model=M.Health, operation_id="health")
    def health() -> M.Health:
        return M.Health(version=__version__)

    # The order is the order of the OpenAPI document (and of web/src/api/generated).
    for area in (benches, ops, definitions, acceptance, sessions, events):
        app.include_router(area.build_router(ctx))

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
