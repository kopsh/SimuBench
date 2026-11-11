"""The session clock as a service (#894).

``advance`` rides the clients guard inside ``Session.advance`` (``ServedSession`` wires it): check
and clock move are one action, and no seam can forget the guard (#121, issue 624). Raises
``ClientsConnected`` (scripts are connected and ``force`` is off) and ``RuntimeError`` (a clock
that cannot be advanced); the caller maps them.
"""

from __future__ import annotations

from typing import Any, cast

from ...models import api as M
from ..manager import ServedSession


def info(sv: ServedSession) -> M.ClockInfo:
    s = sv.session
    return M.ClockInfo(mode=cast(Any, s.clock_mode), now_ns=s.now_ns, scale=s.time_scale)


def advance(sv: ServedSession, seconds: float, *, force: bool = False) -> M.ClockInfo:
    sv.session.advance(seconds, force=force)
    return info(sv)


def set_scale(sv: ServedSession, scale: float) -> M.ClockInfo:
    sv.session.set_time_scale(scale)
    return info(sv)
