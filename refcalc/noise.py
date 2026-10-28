"""Independent noise statistics for sampled, quantized records (issue 24)."""

from __future__ import annotations

import math


def quantized_sigma(sigma: float, lsb: float, averages: int = 1) -> float:
    """RMS of white noise ``sigma`` averaged over ``averages`` acquisitions, then rounded to an
    ``lsb`` grid: the averaged noise and the uniform rounding error (lsb^2 / 12) add in power.
    Valid when the noise spans several codes (sigma / sqrt(averages) >~ lsb / 2)."""
    return math.sqrt(sigma**2 / averages + lsb**2 / 12)


def sigma_interval(sigma: float, samples: int, k: float = 4.0) -> tuple[float, float]:
    """Range a sample standard deviation of ``samples`` Gaussian values falls in with k-sigma
    confidence: its relative standard error is 1 / sqrt(2 (n - 1))."""
    rel = k / math.sqrt(2 * (samples - 1))
    return sigma * (1 - rel), sigma * (1 + rel)
