"""FastAPI application of ``simubench serve`` (D18, D21).

Security for a local service (roadmap.md, D22): the service listens on the loopback interface,
rejects requests whose Host or Origin header is not a loopback name, so that a web page cannot reach
it through DNS rebinding, and, when started with an access token (opt-in, D43), requires it on every
data route.

``serve --mcp`` adds the MCP service over streamable HTTP at ``/mcp`` in the same process (#1188):
an agent and the engineer then share one set of sessions, the same Host/Origin check covers it, and
the access token, when there is one, is required on it too (header ``x-simubench-token`` or
``Authorization: Bearer``).

The routes live in ``server/routes/`` (one router per area); this module assembles them.
"""

from __future__ import annotations

import inspect
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..models import api as M
from .manager import SessionManager
from .routes import acceptance, assistant, benches, definitions, events, ops, sessions
from .routes.context import IN_USE, LOOPBACK_HOSTS, ApiContext, host_ok

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

__all__ = ["IN_USE", "LOOPBACK_HOSTS", "MCP_PATH", "WEB_DIST", "LocalOnly", "create_app"]

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


#: Where ``serve --mcp`` answers MCP (streamable HTTP).
MCP_PATH = "/mcp"


def _mcp_token_guard(inner: Any, token: str) -> Any:
    """The MCP endpoint behind the access token (D43): same token as the data routes."""

    async def guarded(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            bearer = headers.get("authorization", "")
            supplied = headers.get("x-simubench-token") or (
                bearer[7:] if bearer[:7].lower() == "bearer " else ""
            )
            if not supplied or not secrets.compare_digest(supplied, token):
                await JSONResponse({"detail": "missing or wrong access token"}, status_code=401)(
                    scope, receive, send
                )
                return
        await inner(scope, receive, send)

    return guarded


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


def create_app(
    manager: SessionManager,
    token: str | None = None,
    web_dist: Path | None = None,
    mcp: MCPServer | None = None,
) -> FastAPI:
    """``token``: when given, every data route and the event socket require it; None serves without one.
    ``mcp``: also answer MCP over streamable HTTP at ``/mcp`` (``serve --mcp``)."""
    lifespan = None
    mcp_routes: list[Any] = []
    if mcp is not None:
        from mcp.server.transport_security import TransportSecuritySettings

        # LocalOnly (below) checks Host and Origin on every route, /mcp included, with the same
        # loopback rule as the UI; the SDK's own check would only repeat it, more strictly on ports.
        mcp_app = mcp.streamable_http_app(
            streamable_http_path=MCP_PATH,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        mcp_routes = list(mcp_app.routes)
        if token is not None:
            for route in mcp_routes:
                route.app = _mcp_token_guard(route.app, token)

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
            # A lifespan replaces the app's on_event hooks (cli._serve's startup banner): run them here.
            async with mcp.session_manager.run():
                for hook in app.router.on_startup:
                    await _maybe_await(hook())
                try:
                    yield
                finally:
                    for hook in app.router.on_shutdown:
                        await _maybe_await(hook())

    app = FastAPI(
        title="SimuBench",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.token = token
    app.state.manager = manager
    app.add_middleware(LocalOnly)
    ctx = ApiContext.of(manager, token)

    @app.get("/api/health", response_model=M.Health, operation_id="health")
    def health(request: Request) -> M.Health:
        # The address the browser reached, so the port is the one really served.
        mcp_url = str(request.base_url).rstrip("/") + MCP_PATH if mcp is not None else None
        return M.Health(version=__version__, mcp_url=mcp_url)

    # The order is the order of the OpenAPI document (and of web/src/api/generated).
    for area in (benches, ops, definitions, acceptance, sessions, events, assistant):
        app.include_router(area.build_router(ctx))

    app.router.routes.extend(mcp_routes)  # before the UI's catch-all route

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
