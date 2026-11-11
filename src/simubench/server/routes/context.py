"""What every router shares: the manager, the file stores, the access token and the error mapping.

The routers (one module per area) are built from one :class:`ApiContext`, so a route is a plain
function of the context rather than a closure inside ``create_app``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from ...engine.edit import EditError
from ...library.loader import DefinitionLibrary
from ..benchfiles import BenchFiles, Conflict
from ..definitionfiles import DefinitionFiles
from ..errors import FILE_ERRORS
from ..manager import BenchInvalid, ClientsConnected, InstrumentRemote, ServedSession, SessionManager

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

#: The 409 of an operation that connected scripts stand in the way of (#121).
IN_USE: dict[int | str, dict[str, Any]] = {
    409: {"description": "Scripts are connected: the instrument is remote, or the operation needs force=true"}
}


def assistant_of(manager: SessionManager) -> Any:
    """The manager's assistant service, made on first use (tests put their own on the manager)."""
    if getattr(manager, "assistant", None) is None:
        from ...assistant.service import AssistantService

        manager.assistant = AssistantService(manager)  # type: ignore[attr-defined]
    return manager.assistant  # type: ignore[attr-defined]


def host_ok(value: str | None) -> bool:
    if not value:
        return True
    host = value.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in LOOPBACK_HOSTS


def _first_line(exc: Exception) -> str:
    return (str(exc).splitlines() or [""])[0]


@dataclass
class ApiContext:
    """``token``: when given, every data route and the event socket require it; None serves without one."""

    manager: SessionManager
    token: str | None
    library: DefinitionLibrary
    files: BenchFiles
    definition_files: DefinitionFiles
    assistant: Any = None  # AssistantService (imported lazily: PydanticAI loads only with the app)

    @classmethod
    def of(cls, manager: SessionManager, token: str | None) -> ApiContext:
        return cls(
            manager=manager,
            token=token,
            library=DefinitionLibrary([manager.workspace / "instruments"]),
            files=manager.files,
            definition_files=DefinitionFiles(manager.files),
            assistant=assistant_of(manager),
        )

    def auth(self, request: Request) -> None:
        if self.token is None:
            return
        supplied = request.headers.get("x-simubench-token") or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="missing or wrong access token")

    def client_of(self, request: Request) -> str:
        """The client a write comes from: 'ui' (Web Bench) by default, 'tui' from the terminal
        console (D18). Strict: a wrong header value is a 400 rather than a mislabelled record."""
        value = request.headers.get("x-simubench-client") or "ui"
        if value not in ("ui", "tui"):
            raise HTTPException(400, f"unknown X-SimuBench-Client {value!r}: must be 'ui' or 'tui'")
        return value

    def served(self, sid: str) -> ServedSession:
        try:
            return self.manager.get(sid)
        except KeyError:
            raise HTTPException(404, f"no session {sid!r}") from None

    def not_found(self, exc: KeyError) -> HTTPException:
        return HTTPException(404, str(exc).strip("'\""))

    def http_error(self, exc: Exception, **options: Any) -> HTTPException:
        """The HTTP error for a domain error (see :meth:`error_row`); anything else is re-raised as
        it is, so a bug stays the 500 it is. Call it inside the ``except`` that caught ``exc``."""
        err = self.error_row(exc, **options)
        if err is None:
            raise exc
        return err

    def error_row(
        self,
        exc: Exception,
        *,
        missing: str | None = None,
        key: str = "not_found",
        full_detail: bool = False,
        files: bool = True,
    ) -> HTTPException | None:
        """The one table, for every route (#936): the HTTP error of a domain error, None for
        anything that is not one.

        ``missing``: the text of a 404 for a file that is not there (default: the error's own).
        ``key``: where a ``KeyError`` goes, ``"not_found"`` (404) or ``"unprocessable"`` (422).
        ``full_detail``: a 422 carries the whole message, not its first line.
        ``files``: False when the operation reads no file, so a file error is not its to map."""
        if isinstance(exc, (ClientsConnected, InstrumentRemote)):
            return self.in_use(exc)
        if files and isinstance(exc, PermissionError):
            return HTTPException(403, str(exc))
        if files and isinstance(exc, FileNotFoundError):
            return HTTPException(404, missing or str(exc))
        if isinstance(exc, Conflict):
            return HTTPException(409, {"message": str(exc), "current_sha256": exc.current_sha256})
        if isinstance(exc, BenchInvalid):
            return HTTPException(
                422, {"message": str(exc), "diagnostics": [d.model_dump() for d in exc.diagnostics]}
            )
        if isinstance(exc, EditError):
            return HTTPException(422, str(exc))
        if isinstance(exc, KeyError):
            return self.not_found(exc) if key == "not_found" else HTTPException(422, _first_line(exc))
        if isinstance(exc, (ValueError, UnicodeDecodeError)):
            return HTTPException(422, str(exc) if full_detail else _first_line(exc))
        return None

    def in_use(self, exc: InstrumentRemote | ClientsConnected) -> HTTPException:
        """409 for an operation the connected scripts stand in the way of (#121)."""
        detail: dict[str, Any] = {"message": str(exc), "clients": exc.clients}
        if isinstance(exc, InstrumentRemote):
            detail.update(reason="remote", instrument=exc.instrument)
        else:
            detail["reason"] = "clients_connected"
        return HTTPException(409, detail)

    def file_errors(self, fn: Any) -> Any:
        """Run ``fn``; a domain error becomes the HTTP error of :meth:`http_error`."""
        try:
            return fn()
        except (*FILE_ERRORS, KeyError, Conflict, EditError, ValueError) as exc:
            raise self.http_error(exc, missing="no such bench file") from None
