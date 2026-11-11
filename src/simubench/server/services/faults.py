"""Fault injection as a service (#894).

The one rule that used to live in two places (#480, #489): a fault's report reads live state, so
the injection (or clearing) and the conversion to the DTO happen under one ``session.view()`` hold —
a tick racing the read cannot tear it. Raises ``KeyError`` (no such target or fault) and
``ValueError`` (a fault the engine refuses); the caller maps them.
"""

from __future__ import annotations

from ...models import api as M
from ...models.bench import Fault
from ..manager import ServedSession
from ..views import fault_info


def list_faults(sv: ServedSession) -> list[M.FaultInfo]:
    with sv.session.view():
        return [fault_info(f) for f in sv.session.faults.snapshot()]


def inject(sv: ServedSession, spec: Fault, *, source: str) -> M.FaultInfo:
    with sv.session.view():
        return fault_info(sv.session.faults.inject(spec, source=source))


def clear(sv: ServedSession, fault_id: str) -> M.FaultInfo:
    with sv.session.view():
        return fault_info(sv.session.faults.clear(fault_id))
