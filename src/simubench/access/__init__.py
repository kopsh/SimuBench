"""The ports scripts connect to: one small shape for what each access server is and knows.

An access server is one row of the session registry (:attr:`ServedSession.servers`, #1475): the
manager loops the table to stop the servers, count their clients and judge LOCAL, so a new access
protocol declares these three members and registers — no per-protocol edit anywhere else. What is
*not* in the protocol is each server's port semantics (SCPI opens one port per instrument, HiSLIP
and VXI-11 share one, the bench link is a single door): that is the protocol's own knowledge, and
it shows through :meth:`AccessServer.addresses`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..engine.faults import LinkDown


@dataclass(frozen=True)
class ClientConnection:
    """One client on a port server, as the projections show it (views.describe) and the guards
    count it (ServedSession.remote_clients): who it is, whose port it is on, and whether it holds
    the instrument's lock (a script in control; a raw socket never locks)."""

    client: str
    instrument: str | None
    locked: bool = False


class AccessServer(Protocol):
    """What one access server of a served session promises the registry (#1475)."""

    def connections(self) -> list[ClientConnection]:
        """The open clients (who, whose port, holding whose lock)."""
        ...

    def addresses(self) -> dict[str, str]:
        """Instrument -> the resource address this server serves it at (empty if it serves none
        per instrument: the bench link is one door to every resource, not one per instrument)."""
        ...

    async def stop(self) -> None:
        """Close the port and every client on it."""
        ...


async def engine_call(
    op: Callable[..., Any], *args: Any, closing: Callable[[], Awaitable[None]] | None = None
) -> Any:
    """One blocking engine call, run off the event loop — the access servers' passthrough of
    ``Session.write``/``exchange``/``clear_output`` and friends (D63, #1475).

    ``closing`` tears the client's connection down when the engine reports a link fault (a
    ``disconnect`` the model raised): the fault is re-raised after it, so the connection's own
    error path logs it too."""
    try:
        return await asyncio.to_thread(op, *args)
    except LinkDown:
        if closing is not None:
            await closing()
        raise
