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
from ..manager import ClientsConnected, InstrumentRemote, ServedSession, SessionManager

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

    def in_use(self, exc: InstrumentRemote | ClientsConnected) -> HTTPException:
        """409 for an operation the connected scripts stand in the way of (#121)."""
        detail: dict[str, Any] = {"message": str(exc), "clients": exc.clients}
        if isinstance(exc, InstrumentRemote):
            detail.update(reason="remote", instrument=exc.instrument)
        else:
            detail["reason"] = "clients_connected"
        return HTTPException(409, detail)

    def file_errors(self, fn: Any) -> Any:
        try:
            return fn()
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, "no such bench file") from None
        except KeyError as exc:
            raise self.not_found(exc) from None
        except Conflict as exc:
            raise HTTPException(409, {"message": str(exc), "current_sha256": exc.current_sha256}) from None
        except EditError as exc:
            raise HTTPException(422, str(exc)) from None
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None
