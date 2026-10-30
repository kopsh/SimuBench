"""Independent noise statistics for sampled, quantized records (issue 24)."""

from __future__ import annotations

import math


def quantized_sigma(sigma: float, lsb: float, level: float = 0.0) -> float:
    """RMS of white noise ``sigma`` rounded to an ``lsb`` grid, exactly: the spread of the codes
    the rounded Gaussian falls on. ``level`` is the noise-free value in codes (0: on a code, 0.5:
    between two).

    When the noise spans several codes this is the familiar sigma^2 + lsb^2 / 12 in power; nearer
    one code it is not (0.6 % lower on a code at sigma = lsb / 2), which a 100 000-point check
    resolves. A record that averages acquisitions processes the codes after the quantization
    (G4/D46), so its noise is :func:`averaged_codes_sigma`."""
    s = sigma
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


def averaged_codes_sigma(sigma: float, lsb: float, acquisitions: int, level: float = 0.0) -> float:
    """RMS of the mean of ``acquisitions`` records, each white noise ``sigma`` rounded to an
    ``lsb`` grid before the averaging (G4/D46): the noise and the quantization are independent
    sample by sample, so both average down - (sigma^2 + lsb^2 / 12) / acquisitions in power.

    Averaging before the quantization (an ideal instrument's analog average) would leave the full
    lsb^2 / 12 in every mean; a real scope's average of codes leaves a share of it, which is why
    averaging buys resolution at all."""
    if sigma <= 0:
        return 0.0
    return math.sqrt((sigma**2 + lsb**2 / 12) / acquisitions)


def sigma_interval(sigma: float, samples: int, k: float = 4.0) -> tuple[float, float]:
    """Range a sample standard deviation of ``samples`` Gaussian values falls in with k-sigma
    confidence: its relative standard error is 1 / sqrt(2 (n - 1))."""
    rel = k / math.sqrt(2 * (samples - 1))
    return sigma * (1 - rel), sigma * (1 + rel)
