"""The shared modulation math (issue 634): one Carson/Bessel expansion behind the function
generator's modulator (``sources.AwgModulator``) and the RF generator's (``rfgen.RfModulator``).

Angle modulation turns every carrier line into ``2k + 1`` lines at the sideband orders Carson's
rule keeps (``k = floor(|β|) + 1`` per side, |β| because a bandwidth is never negative); the power
the rule omits is bounded by :func:`omitted_power_fraction` (architecture.md 2.3's fidelity note).
AM multiplies every line by ``(1 + m·sin)``: two sidebands of ``m/2`` each, carrying the modulating
tone's phase.

Lines assemble per the cosine convention (``engine.signal``): a line through a negative frequency
folds onto its positive frequency as the conjugate (folding is linear, so sidebands of folded
lines fold consistently), a line at zero accumulates the DC term. The same builder serves a single
modulation (the AWG picks one type) and stacked ones (the RF generator's angle plus AM).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.special import jv

from ...engine.signal import FidelityNote, Signal


def carson_orders(beta: float) -> int:
    """Sideband orders kept on each side: floor(|β|) + 1 (Carson's rule), 0 for no modulation."""
    return math.floor(abs(beta)) + 1 if beta else 0


def omitted_power_fraction(beta: float) -> float:
    """The share of a line's power outside the kept sideband orders (the truncation error bound
    the fidelity note reports)."""
    k = carson_orders(beta)
    n = np.arange(-k, k + 1)
    return float(1.0 - np.sum(jv(n, beta) ** 2))


class LineBuilder:
    """Phasor lines being assembled (cosine convention); negative frequencies fold over."""

    def __init__(self) -> None:
        self.freq: list[float] = []
        self.amp: list[complex] = []
        self.dc = 0.0

    def add(self, f: float, a: complex) -> None:
        if abs(a) < 1e-15:
            return
        if f < 0:
            f, a = -f, a.conjugate()
        if f == 0:
            self.dc += a.real
            return
        self.freq.append(f)
        self.amp.append(a)

    def signal(self, notes: tuple[FidelityNote, ...] = ()) -> Signal:
        return Signal.lines(self.freq, self.amp, dc=self.dc, notes=notes).merged()


def angle_sidebands(
    out: LineBuilder, freq: np.ndarray, amp: np.ndarray, beta: float | np.ndarray, rate: float, phi: float
) -> None:
    """Expand every carrier line into its Bessel sidebands at ``±n·rate`` (``n`` to Carson's rule),
    each ``J_n(β)`` of the line's amplitude, phase-stepped by ``n·φ``. ``beta`` is per line (an
    array matching ``freq``) or one value for all."""
    betas = np.broadcast_to(np.asarray(beta, dtype=float), np.shape(freq))
    for f, a, b in zip(freq, amp, betas, strict=True):
        k = carson_orders(float(b))
        for n in range(-k, k + 1):
            out.add(f + n * rate, a * jv(n, b) * complex(math.cos(n * phi), math.sin(n * phi)))


def am_sidebands(out: LineBuilder, freq: np.ndarray, amp: np.ndarray, rate: float, side: complex) -> None:
    """Add the AM sidebands of ``m/2·e^{jφ}/j`` (``side``, the tone's depth and phase already
    scaled in) around every line at ``±rate``."""
    for f, a in zip(freq, amp, strict=True):
        out.add(f + rate, a * side)
        out.add(f - rate, a * side.conjugate())
