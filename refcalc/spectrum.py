"""Independent spectrum-analyzer reference values."""

from __future__ import annotations

import math

import numpy as np

KT0_DBM_HZ = -174.0


def line_dbm(peak_v: float, r_ohm: float = 50.0) -> float:
    """A sine of ``peak_v`` into ``r_ohm``: P = V²/2R, in dBm."""
    return 10 * math.log10(peak_v**2 / (2 * r_ohm) / 1e-3)


def square_harmonic_peak(amplitude: float, n: int) -> float:
    return 4 * amplitude / (math.pi * n)


def gaussian_rbw_loss_db(offset_hz: float, rbw_hz: float) -> float:
    """Power response of a Gaussian filter whose −3 dB width is the RBW."""
    return 10 * math.log10(math.exp(-4 * math.log(2) * (offset_hz / rbw_hz) ** 2))


def noise_floor_dbm(rbw_hz: float, atten_db: float, nf_db: float = 24.0) -> float:
    nbw = rbw_hz * math.sqrt(math.pi / (4 * math.log(2)))
    return KT0_DBM_HZ + nf_db + atten_db + 10 * math.log10(nbw)


def detector_offsets(cells: int, trials: int = 400_000, seed: int = 1) -> dict[str, float]:
    """Mean of the displayed (log) noise relative to its power, per detector, by Monte Carlo."""
    e = np.random.default_rng(seed).exponential(size=(trials, cells))
    return {
        "SAMPle": float(np.mean(10 * np.log10(e[:, 0]))),
        "POSitive": float(np.mean(10 * np.log10(e.max(axis=1)))),
        "AVERage": float(np.mean(10 * np.log10(np.mean(np.sqrt(e), axis=1) ** 2))),
        "RMS": float(np.mean(10 * np.log10(e.mean(axis=1)))),
    }


def prbs_line_peak(level_on_net: float, order: int, k: int) -> float:
    """Line k of an m-sequence of ±A: 2A·√(N+1)/N·|sinc(k/N)|."""
    n = 2**order - 1
    return 2 * level_on_net * math.sqrt(n + 1) / n * abs(np.sinc(k / n))
