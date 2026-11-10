"""Actions on a served session: a SCPI program message, a setting, a front-panel button. Each returns
the DTO every consumer (HTTP API, MCP tools, TUI) shows. Projections are in :mod:`simubench.server.views`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..engine.faults import LinkDown
from ..engine.record import jsonable
from ..engine.session import Session
from ..models import api as M
from ..scpi.errors import ScpiError

if TYPE_CHECKING:
    from .manager import ServedSession


def scpi(served: ServedSession, instrument: str, command: str, client: str = "ui") -> M.ScpiResponse:
    return scpi_response(served.session, instrument, command, client)


def configure(
    served: ServedSession, instrument: str, path: str, value: Any, client: str = "ui"
) -> M.ConfigureResponse:
    s = served.session
    err = s.try_configure(instrument, path, value, client=client)
    current = jsonable(s.instrument(instrument).state.get(path))
    if err is None:
        return M.ConfigureResponse(ok=True, value=current)
    return M.ConfigureResponse(ok=False, error=M.ErrorItem(code=err.code, message=err.message), value=current)


def panel(served: ServedSession, instrument: str, action: str, client: str = "ui") -> M.PanelResponse:
    try:
        served.session.panel(instrument, action, client=client)
    except ScpiError as err:
        return M.PanelResponse(ok=False, error=M.ErrorItem(code=err.code, message=err.message))
    return M.PanelResponse(ok=True)


def scpi_response(s: Session, instrument: str, command: str, client: str = "ui") -> M.ScpiResponse:
    """The one SCPI-exchange shape every consumer assembles (issue 627): the web API returns it
    as is, the MCP tools dump it (the fault_info precedent — they used to carry their own, with
    seconds instead of ns). ``timed_out`` is the engine's own verdict (CommandResult.is_query),
    not a guess from the command text."""
    try:
        result = s.command(instrument, command, client=client)
    except LinkDown as exc:
        return M.ScpiResponse(link_error=str(exc), now_ns=s.now_ns)
    out = result.reply
    return M.ScpiResponse(
        response=out.decode(errors="replace").rstrip("\n") if out is not None else None,
        errors=[M.ErrorItem(code=e.code, message=e.message) for e in result.errors],
        timed_out=result.is_query and out is None,
        now_ns=s.now_ns,
    )
