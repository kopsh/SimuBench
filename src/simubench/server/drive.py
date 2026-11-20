"""Actions on a served session: a SCPI program message, a setting, a front-panel button. Each returns
the DTO every consumer (HTTP API, MCP tools, TUI) shows. Projections are in :mod:`simubench.server.views`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..contracts import api as M
from ..engine.events import binary_summary
from ..engine.faults import LinkDown
from ..engine.record import jsonable
from ..engine.session import Session
from ..scpi.errors import ScpiError

if TYPE_CHECKING:
    from .manager import ServedSession


def scpi(served: ServedSession, instrument: str, command: str, client: str = "ui") -> M.ScpiResponse:
    return scpi_response(served.session, instrument, command, client)


def configure(
    served: ServedSession, instrument: str, path: str, value: Any, client: str = "ui"
) -> M.ConfigureResponse:
    s = served.session
    old = jsonable(s.instrument(instrument).state.get(path))
    err = s.try_configure(instrument, path, value, client=client)
    current = jsonable(s.instrument(instrument).state.get(path))
    if err is None:
        # an equal-value write survives validation and changes nothing (write_tx's no-op rule):
        # ok, but not changed -- the caller can tell a settled write from a swallowed one (#1678)
        return M.ConfigureResponse(ok=True, value=current, changed=current != old)
    return M.ConfigureResponse(ok=False, error=M.ErrorItem(code=err.code, message=err.message), value=current)


def panel(served: ServedSession, instrument: str, action: str, client: str = "ui") -> M.PanelResponse:
    try:
        served.session.panel(instrument, action, client=client)
    except ScpiError as err:
        return M.PanelResponse(ok=False, error=M.ErrorItem(code=err.code, message=err.message))
    return M.PanelResponse(ok=True)


def reply_text(out: bytes) -> str:
    """A reply as text; a binary one (a ``:WAVeform:DATA?`` block in WORD or BYTE format, a
    screen image) as its block header and size, as the log writes it — decoded, its bytes are
    only replacement characters, tens of kilobytes of them."""
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError:
        return binary_summary(out)
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text):
        return binary_summary(out)
    return text.rstrip("\n")


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
        response=reply_text(out) if out is not None else None,
        errors=[M.ErrorItem(code=e.code, message=e.message) for e in result.errors],
        timed_out=result.is_query and out is None,
        now_ns=s.now_ns,
    )
