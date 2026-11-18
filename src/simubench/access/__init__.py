"""The ports scripts connect to: one small shape for what each server knows about its clients."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientConnection:
    """One client on a port server, as the projections show it (views.describe) and the guards
    count it (ServedSession.remote_clients): who it is, whose port it is on, and whether it holds
    the instrument's lock (a script in control; a raw socket never locks)."""

    client: str
    instrument: str | None
    locked: bool = False
