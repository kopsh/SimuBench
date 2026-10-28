"""Why a port or net reads 0 V: trace the circuit upstream and name the cause.

A flat 0 V on a scope channel can mean very different things: nothing is wired to it, every source
upstream is switched off or set to zero, there is no source at all, or a fault is acting on the
path. The topology answers which, so a screen can say so instead of guessing.

Upstream means: any node joined by a two-terminal element (resistor, capacitor, ...; ground is not
a path), and the control node of a controlled source (a buffer passes the signal one way only).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .circuit import GROUND, Circuit
from .component import Component

if TYPE_CHECKING:
    from .session import Session

Kind = Literal["unconnected", "source_off", "zero_setting", "no_source", "fault"]


@dataclass
class Silence:
    kind: Kind
    #: Components from the silent node to the source (or to where the trace ends), by reference:
    #: a DUT or accessory name ('rc', 'wire1.passive_probe0') or an instrument port ('awg.OUT1').
    path: list[str] = field(default_factory=list)
    #: The source the reason is about, as an instrument port ('awg.OUT1') or component uid.
    source: str | None = None
    fault_id: str | None = None
    detail: str = ""


def is_source(comp: Component) -> bool:
    """Components that can drive the circuit: those that declare independent sources."""
    return type(comp).emf is not Component.emf


def _ref(session: Session, comp: Component) -> str:
    """How a component is named to the user: bench components by name, instruments by port."""
    if comp.owner is None:
        return comp.name
    for port, spec in comp.ports.items():
        if spec.medium == "electrical":
            ref = session.port_ref(comp, port)
            if ref is not None:
                return ref
    return comp.owner.name


def _edges(session: Session) -> dict[str, list[tuple[str, Component]]]:
    """For each node, the upstream nodes and the component that joins them."""
    up: dict[str, list[tuple[str, Component]]] = {}
    for comp in session._all_components():
        c = Circuit()
        comp.stamp(c, lambda p, comp=comp: session.node_of(comp, p))  # type: ignore[misc]
        for b in c.branches:
            if GROUND in (b.a, b.b) or b.a == b.b:
                continue
            up.setdefault(str(b.a), []).append((str(b.b), comp))
            up.setdefault(str(b.b), []).append((str(b.a), comp))
        for v in c.vcvs:
            up.setdefault(str(v.out), []).append((str(v.ctrl), comp))
    return up


def why_silent(session: Session, node: str, *, port_ref: str | None = None) -> list[Silence]:
    """Reasons ``node`` carries no signal, or [] when it does. ``port_ref`` names the node's port."""
    if port_ref is not None and port_ref not in session.port_net:
        return [Silence("unconnected", path=[port_ref], detail=f"{port_ref} is not wired")]
    sig = session.node_signal(node)
    if sig.peak() > 0 or abs(sig.mean()) > 0:
        return []

    # Breadth-first upstream, remembering how each node was reached.
    edges = _edges(session)
    parent: dict[str, tuple[str, Component] | None] = {node: None}
    seen: list[Component] = []  # every component upstream, nearest first
    queue = deque([node])
    while queue:
        n = queue.popleft()
        for m, comp in edges.get(n, []):
            if comp not in seen:
                seen.append(comp)
            if m not in parent:
                parent[m] = (n, comp)
                queue.append(m)

    def path_to(n: str, last: Component | None = None) -> list[str]:
        hops: list[str] = []
        while parent.get(n) is not None:
            n, comp = parent[n]  # type: ignore[misc]
            hops.append(_ref(session, comp))
        hops.reverse()
        if port_ref is not None:
            hops.insert(0, port_ref)
        if last is not None:
            hops.append(_ref(session, last))
        return list(dict.fromkeys(hops))  # one entry per component, in order

    reasons: list[Silence] = []
    reached: set[str] = set()
    for comp in session._all_components():
        if not is_source(comp):
            continue
        pins = [
            session.node_of(comp, p)
            for p, spec in comp.ports.items()
            if spec.medium == "electrical" and spec.direction in ("out", "inout")
        ]
        hit = next((p for p in pins if p in parent), None)
        if hit is None:
            continue
        reached.add(comp.uid)
        emf = comp.emf()
        ref = _ref(session, comp)
        if not emf:
            reasons.append(Silence("source_off", path_to(hit, comp), ref, detail=f"{ref}: output is off"))
        elif all(s.peak() == 0 and s.mean() == 0 for s in emf.values()):
            reasons.append(Silence("zero_setting", path_to(hit, comp), ref, detail=f"{ref}: set to 0"))

    for f in session.faults.snapshot():
        target = f._component
        if (
            f.status == "active"
            and target is not None
            and (target.uid in reached or any(n in parent for n in _nodes_of(session, target)))
        ):
            reasons.append(
                Silence("fault", [_ref(session, target)], _ref(session, target), f.id, detail=f.describe())
            )

    if not reached:
        upstream = [_ref(session, c) for c in seen]
        path = list(dict.fromkeys([*([port_ref] if port_ref else []), *upstream]))
        reasons.append(Silence("no_source", path, detail="no source upstream"))
    return reasons


def _nodes_of(session: Session, comp: Component) -> list[str]:
    return [session.node_of(comp, p) for p, s in comp.ports.items() if s.medium == "electrical"]
