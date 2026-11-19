"""The bench's nets, derived once (issue 1381): the union-find over wire ends, each wire's
accessory chain and the accessories' run-time names.

The session builds the running bench's nets from this walk -- creating each accessory component
as its wire is walked (:func:`accessory_name`) -- and the bench validation groups the same wire
ends the same way. An accessory type named in ``through`` joins its two terminals into one node
(the validation's series pass, for the impedance hints); the runtime passes nothing: for the
circuit every accessory is two nodes. One walk, one naming, so a rule change lands here and the
two seams' messages can name the same accessory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.bench import BenchDefinition, Wire
    from .component import Component

#: The media a bench wire may end on: the circuit's nodes and the one-way links of a non-electrical
#: medium (a fibre, D71). A new wired medium is named here, not in the session.
WIRED_MEDIA: tuple[str, ...] = ("electrical", "optical")


class UnionFind:
    """Union-find over strings, with path halving; :meth:`groups` lists members in insertion
    order, roots in the order they first appeared (the net ids keep counting from it)."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self) -> Iterator[list[str]]:
        """Every member under its root; the order is the one the refs were added in."""
        out: dict[str, list[str]] = {}
        for ref in list(self.parent):
            out.setdefault(self.find(ref), []).append(ref)
        yield from out.values()


def accessory_name(wire_index: int, k: int, acc_type: str) -> str:
    """A wire accessory's component name as the runtime names it: ``wire<i>.<type><k>``. This is
    the user-visible name (components, net members, internals); the validation cites it too."""
    return f"wire{wire_index}.{acc_type}{k}"


def union_wire(uf: UnionFind, wire_index: int, wire: Wire, *, through: frozenset[str] = frozenset()) -> None:
    """Join one wire's ends and accessory terminals in ``uf``: the chain's hops (``from`` --
    the first accessory's IN, its OUT -- the next accessory's IN, ..., the last one's OUT --
    ``to``), plus both terminals of every accessory whose type is in ``through``. An accessory's
    terminals are named as the runtime names its component (:func:`accessory_name`)."""
    chain = [wire.from_]
    for k, acc in enumerate(wire.accessories):
        name = accessory_name(wire_index, k, acc.type)
        chain += [f"{name}.IN", f"{name}.OUT"]
        if acc.type in through:
            uf.union(f"{name}.IN", f"{name}.OUT")
    chain.append(wire.to)
    for a, b in zip(chain[0::2], chain[1::2], strict=True):
        uf.union(a, b)


def nets_of(bench: BenchDefinition, *, through: frozenset[str] = frozenset()) -> list[list[str]]:
    """The bench's nets: port references grouped by net, each wire walked by :func:`union_wire`
    (``through``: accessory types whose two terminals are one node)."""
    uf = UnionFind()
    for i, wire in enumerate(bench.wires):
        union_wire(uf, i, wire, through=through)
    return list(uf.groups())


def feed(
    members: Iterable[str],
    component_at: Callable[[str], tuple[Component, str] | None],
    comp: Component,
    medium: str,
) -> tuple[Component, str] | None:
    """The one output of ``medium`` among a net's ``members`` (port references), other than
    ``comp`` itself; None when the net has none. A net of a non-electrical medium is a pair of ends
    with one driver (a fibre, D71): what arrives at an input is what that output gives. Nothing
    here is specific to light — the next such medium passes its own name."""
    for member in members:
        found = component_at(member)
        if found is None or found[0] is comp:
            continue
        spec = found[0].ports.get(found[1])
        if spec is not None and spec.medium == medium and spec.direction == "out":
            return found
    return None
