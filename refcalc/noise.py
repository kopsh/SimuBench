"""Independent noise statistics for sampled, quantized records (issue 24)."""

from __future__ import annotations

import math


def quantized_sigma(sigma: float, lsb: float, averages: int = 1, level: float = 0.0) -> float:
    """RMS of white noise ``sigma`` averaged over ``averages`` acquisitions, then rounded to an
    ``lsb`` grid, exactly: the spread of the codes the rounded Gaussian falls on. ``level`` is the
    noise-free value in codes (0: on a code, 0.5: between two).

    When the noise spans several codes this is the familiar sigma^2 / averages + lsb^2 / 12 in
    power; nearer one code it is not (0.6 % lower on a code at sigma / sqrt(averages) = lsb / 2),
    which a 100 000-point check resolves."""
    s = sigma / math.sqrt(averages)
    if s <= 0:
        return 0.0
    span = int(12 * s / lsb) + 2
    mean = square = 0.0
    for k in range(round(level) - span, round(level) + span + 1):
        lo = ((k - 0.5) - level) * lsb / (s * math.sqrt(2))
        hi = ((k + 0.5) - level) * lsb / (s * math.sqrt(2))
        p = 0.5 * (math.erf(hi) - math.erf(lo))
        mean += p * (k - level)
        square += p * (k - level) ** 2
    return math.sqrt(max(square - mean**2, 0.0)) * lsb


def sigma_interval(sigma: float, samples: int, k: float = 4.0) -> tuple[float, float]:
    """Range a sample standard deviation of ``samples`` Gaussian values falls in with k-sigma
    confidence: its relative standard error is 1 / sqrt(2 (n - 1))."""
    rel = k / math.sqrt(2 * (samples - 1))
    return sigma * (1 - rel), sigma * (1 + rel)
