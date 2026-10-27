"""Independent lock-in reference calculations (closed forms from the SRS application note)."""

from __future__ import annotations

import math

import numpy as np


def enbw(poles: int, tc: float) -> float:
    """ENBW of n RC stages, by integrating |H(f)|² numerically (independent of the closed form)."""
    f = np.linspace(0, 2000 / tc, 2_000_001)
    h2 = 1 / (1 + (2 * math.pi * f * tc) ** 2) ** poles
    return float(np.trapezoid(h2, f))


def unsettled(poles: int, x: float) -> float:
    """Fraction of a step still unsettled at t = x·T for n identical stages."""
    return math.exp(-x) * sum(x**k / math.factorial(k) for k in range(poles))


def settling_multiple(poles: int, remaining: float = 0.01) -> float:
    x = 0.0
    while unsettled(poles, x) > remaining:
        x += 1e-4
    return x


def staircase_fundamental(table: list[float], scale: float) -> float:
    """Peak of the fundamental of a linearly interpolated periodic table, by dense sampling + FFT."""
    n = len(table)
    over = 4096
    t = np.arange(n * over) / over  # in samples
    y = np.interp(t, np.arange(n + 1), [*list(table), table[0]]) * scale
    spec = np.fft.rfft(y) / y.size
    return 2 * abs(spec[1])
