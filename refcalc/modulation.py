"""Independent closed forms for modulated carriers."""

from __future__ import annotations

import math

import numpy as np


def bessel_j(n: int, beta: float) -> float:
    """J_n(β) by its integral (1/π)∫₀^π cos(nt − β sin t) dt, independent of scipy."""
    t = np.linspace(0, math.pi, 200_001)
    return float(np.trapezoid(np.cos(n * t - beta * np.sin(t)), t) / math.pi)


def dbc(amplitude_ratio: float) -> float:
    return 20 * math.log10(max(abs(amplitude_ratio), 1e-30))


def carson_omitted_power(beta: float) -> float:
    k = math.floor(beta) + 1
    return 1 - sum(bessel_j(n, beta) ** 2 for n in range(-k, k + 1))
