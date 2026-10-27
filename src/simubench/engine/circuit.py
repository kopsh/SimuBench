"""Linear network solving by modified nodal analysis, one complex system per frequency.

Electrical ports and two-port components contribute elements to a :class:`Circuit`; the solver returns,
for every node, its transfer function from each independent source. Signals on nets follow by
multiplying each source's lines by those transfer functions (architecture.md 1.4).
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field

import numpy as np

from .signal import Signal

GROUND = "0"
#: Conductance from every node to ground, as in SPICE, so that floating nodes stay solvable.
GMIN = 1e-12

Admittance = Callable[[np.ndarray], np.ndarray]


def _const(y: complex) -> Admittance:
    return lambda f: np.full(np.shape(f), y, dtype=complex)


@dataclass
class _Branch:
    a: Hashable
    b: Hashable
    y: Admittance


@dataclass
class _Source:
    """Thevenin source: EMF of source ``sid`` behind impedance ``z`` into ``node``."""

    node: Hashable
    sid: Hashable
    z: float


@dataclass
class _Current:
    """Ideal current source: source ``sid``'s signal, in amperes, injected into ``node``."""

    node: Hashable
    sid: Hashable


@dataclass
class _Vcvs:
    """Voltage-controlled voltage source: V(out) = gain(f) * V(ctrl) behind impedance ``z``."""

    out: Hashable
    ctrl: Hashable
    gain: Admittance
    z: float


@dataclass
class Circuit:
    branches: list[_Branch] = field(default_factory=list)
    sources: list[_Source] = field(default_factory=list)
    vcvs: list[_Vcvs] = field(default_factory=list)
    currents: list[_Current] = field(default_factory=list)
    _nodes: dict[Hashable, int] = field(default_factory=dict)

    # ---- building --------------------------------------------------------------------------------

    def node(self, name: Hashable) -> int:
        if name == GROUND:
            return -1
        if name not in self._nodes:
            self._nodes[name] = len(self._nodes)
        return self._nodes[name]

    def admittance(self, a: Hashable, b: Hashable, y: Admittance) -> None:
        self.node(a)
        self.node(b)
        self.branches.append(_Branch(a, b, y))

    def resistor(self, a: Hashable, b: Hashable, r: float) -> None:
        if r <= 0:
            raise ValueError("resistance must be positive")
        self.admittance(a, b, _const(1.0 / r))

    def capacitor(self, a: Hashable, b: Hashable, c: float) -> None:
        self.admittance(a, b, lambda f, c=c: 1j * 2 * np.pi * np.asarray(f) * c)

    def inductor(self, a: Hashable, b: Hashable, inductance: float) -> None:
        def y(f: np.ndarray, L: float = inductance) -> np.ndarray:
            w = 2 * np.pi * np.asarray(f, dtype=float)
            with np.errstate(divide="ignore"):
                out = 1.0 / (1j * w * L)
            return np.where(w == 0, 1e12, out)

        self.admittance(a, b, y)

    def source(self, node: Hashable, sid: Hashable, z: float) -> None:
        self.node(node)
        self.sources.append(_Source(node, sid, float(z)))

    def current(self, node: Hashable, sid: Hashable) -> None:
        """An ideal current source pushing source ``sid``'s signal (in A) into ``node``."""
        self.node(node)
        self.currents.append(_Current(node, sid))

    def controlled(self, out: Hashable, ctrl: Hashable, gain: Admittance, z: float) -> None:
        self.node(out)
        self.node(ctrl)
        self.vcvs.append(_Vcvs(out, ctrl, gain, float(z)))

    @property
    def nodes(self) -> list[Hashable]:
        return list(self._nodes)

    # ---- solving ---------------------------------------------------------------------------------

    def solve(self, freqs: Iterable[float]) -> Solution:
        f = np.unique(np.asarray(list(freqs), dtype=float))
        n = len(self._nodes)
        sids = list(dict.fromkeys([*(s.sid for s in self.sources), *(c.sid for c in self.currents)]))
        # Extra unknowns: one current per ideal (z == 0) source and per controlled source.
        ideal = [s for s in self.sources if s.z == 0]
        k = n + len(ideal) + len(self.vcvs)
        a = np.zeros((f.size, k, k), dtype=complex)
        b = np.zeros((f.size, k, len(sids)), dtype=complex)
        idx = self._nodes

        for i in range(n):
            a[:, i, i] += GMIN

        def stamp(p: Hashable, q: Hashable, y: np.ndarray) -> None:
            ip = -1 if p == GROUND else idx[p]
            iq = -1 if q == GROUND else idx[q]
            if ip >= 0:
                a[:, ip, ip] += y
            if iq >= 0:
                a[:, iq, iq] += y
            if ip >= 0 and iq >= 0:
                a[:, ip, iq] -= y
                a[:, iq, ip] -= y

        for br in self.branches:
            stamp(br.a, br.b, np.asarray(br.y(f), dtype=complex))

        row = n
        for s in self.sources:
            col = sids.index(s.sid)
            i = idx[s.node]
            if s.z == 0:
                # V(node) = EMF; KCL gets the source current unknown.
                a[:, i, row] += 1
                a[:, row, i] += 1
                b[:, row, col] += 1
                row += 1
            else:
                g = 1.0 / s.z
                a[:, i, i] += g
                b[:, i, col] += g  # Norton equivalent: EMF / z injected into the node

        for cs in self.currents:
            b[:, idx[cs.node], sids.index(cs.sid)] += 1

        for v in self.vcvs:
            io, ic = idx[v.out], idx[v.ctrl]
            gain = np.asarray(v.gain(f), dtype=complex)
            if v.z == 0:
                a[:, io, row] += 1
                a[:, row, io] += 1
                a[:, row, ic] -= gain
            else:
                # Internal ideal node behind z: model as Norton with controlled current.
                g = 1.0 / v.z
                a[:, io, io] += g
                a[:, io, ic] -= g * gain
                # unused extra row stays as identity to keep the system square
                a[:, row, row] = 1
            row += 1

        x = np.linalg.solve(a, b) if k else np.zeros((f.size, 0, len(sids)), dtype=complex)
        # The extra unknown of an ideal source is the current flowing from its node into it.
        currents = {s.sid: -x[:, n + j, :] for j, s in enumerate(ideal)}
        return Solution(f, {name: x[:, i, :] for name, i in idx.items()}, sids, currents)


@dataclass
class Solution:
    freqs: np.ndarray
    node_transfer: dict[Hashable, np.ndarray]  # node -> (F, S)
    sids: list[Hashable]
    source_transfer: dict[Hashable, np.ndarray] = field(default_factory=dict)  # ideal source -> (F, S)

    def transfer(self, node: Hashable, sid: Hashable, f: np.ndarray) -> np.ndarray:
        """Transfer from source ``sid``'s EMF to ``node`` at frequencies ``f`` (must have been solved)."""
        if node == GROUND or sid not in self.sids:
            return np.zeros(np.shape(f), dtype=complex)
        col = self.sids.index(sid)
        pos = np.searchsorted(self.freqs, f)
        return self.node_transfer[node][pos, col]

    def source_dc_current(self, sid: Hashable, emf: dict[Hashable, Signal]) -> float:
        """DC current an ideal (``z == 0``) source drives out of its node, in amperes."""
        h = self.source_transfer.get(sid)
        if h is None:
            return 0.0
        total = 0.0
        for other, sig in emf.items():
            if other in self.sids:
                total += float((h[0, self.sids.index(other)] * sig.dc).real)
        return total

    def node_signal(self, node: Hashable, emf: dict[Hashable, Signal]) -> Signal:
        """Signal on ``node`` given each source's EMF signal."""
        out = Signal.zero()
        for sid, sig in emf.items():
            if sid not in self.sids:
                continue
            h = self.transfer(node, sid, sig.freq)
            h0 = self.transfer(node, sid, np.zeros(1))[0]
            amp = sig.amp * h
            keep = amp != 0  # a source with no path to this node contributes nothing, not zero lines
            dc = sig.dc * h0.real
            reaches = bool(keep.any()) or dc != 0
            # A source's fidelity notes travel only where its signal does.
            out = out + Signal(sig.freq[keep], amp[keep], sig.t0_ns[keep], dc, sig.notes if reaches else ())
        return out


def solve_signals(
    circuit: Circuit, emf: dict[Hashable, Signal], nodes: Iterable[Hashable]
) -> dict[Hashable, Signal]:
    """Solve ``circuit`` at every frequency the sources carry and return the signal on each node."""
    freqs: set[float] = {0.0}
    for sig in emf.values():
        freqs.update(float(x) for x in np.unique(sig.freq))
    sol = circuit.solve(sorted(freqs))
    return {nd: sol.node_signal(nd, emf) for nd in nodes}
