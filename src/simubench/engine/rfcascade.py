"""Cascaded RF figures of a receiver's front end (signal-chain.md 5.2): gain, noise figure,
intercept points and compression of a chain of stages, and the distortion products the chain's
intercepts make of a set of spectral lines.

Pure functions of numbers and :class:`~simubench.engine.component.RfStageSpec` — no session, no
component. The spectrum analyzer's trace and (later) its ADC both compute from the list of stages
found by walking the wiring upstream; nothing else carries a second copy of these formulas.

Levels are dBm into the chain's input unless a name says otherwise; intercept and compression
points are input-referred.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from .component import RfStageSpec

#: Thermal noise density at T0 = 290 K, dBm/Hz.
KT0_DBM_HZ = -174.0
#: Input lines that take part in :func:`products` (the strongest); the rest are left out and the
#: result says so.
MAX_PRODUCT_LINES = 64
#: 1 − 10^(−1/20): how far the third-order model's gain has dropped (as a voltage ratio) at P1dB.
_C1DB = 1.0 - 10.0 ** (-1.0 / 20.0)


def _lin(db: float) -> float:
    return 10.0 ** (db / 10.0)


def _db(ratio: float) -> float:
    return 10.0 * math.log10(ratio)


def _noise_figure(stages: list[RfStageSpec]) -> float | None:
    """Friis over the stages. A stage without a noise figure and without gain is a matched loss at
    T0: it adds its loss to the next stage's noise figure, in dB — so a chain with one noisy stage
    behind an attenuator is exactly that stage's figure plus the attenuation, with no rounding from
    a trip through linear units. A stage without a noise figure that has gain is noiseless."""
    noisy: list[tuple[float, float]] = []  # (noise figure, gain ahead of it), dB
    ahead = 0.0  # gain of everything already folded into `noisy`
    loss = 0.0  # matched loss waiting for the next noisy stage
    for s in stages:
        if s.noise_figure_db is None:
            if s.gain_db <= 0.0:
                loss -= s.gain_db
                continue
            if loss > 0.0:
                noisy.append((loss, ahead))
            ahead += s.gain_db - loss
            loss = 0.0
            continue
        noisy.append((s.noise_figure_db + loss, ahead))
        ahead += s.gain_db - loss
        loss = 0.0
    if loss > 0.0:
        noisy.append((loss, ahead))
    if not noisy:
        return None
    if len(noisy) == 1 and noisy[0][1] == 0.0:
        return noisy[0][0]
    return _db(1.0 + sum((_lin(nf) - 1.0) / _lin(g) for nf, g in noisy))


def _input_referred(points: list[tuple[float, float]]) -> float:
    """Stage points (dBm at the stage's input, gain ahead of it) combined at the chain's input:
    each referred to the input, their reciprocals (in mW) added."""
    finite = [(p, g) for p, g in points if math.isfinite(p)]
    if not finite:
        return math.inf
    if len(finite) == 1 and finite[0][1] == 0.0:
        return finite[0][0]
    return -_db(sum(_lin(g - p) for p, g in finite))


def _input_referred_amplitude(points: list[tuple[float, float]]) -> float:
    """Second-order intercepts combined in amplitude form (Razavi, the cascade of nonlinear
    stages): a stage's second-order product referred to the chain's input carries the voltage
    gain ahead of it to the first power (the product itself scales with the square), so the
    reciprocals of the *voltages* add — 1/A = Σ αₖ/Aₖ — where a third-order product referred
    the same way carries the square of the gain, which is the power form ``_input_referred``
    implements. The gain ahead is a power gain in dB, which over a continuous impedance is the
    voltage gain in dB too (20·log₁₀); the reference impedance's constant cancels in the ratio."""
    finite = [(p, g) for p, g in points if math.isfinite(p)]
    if not finite:
        return math.inf
    if len(finite) == 1 and finite[0][1] == 0.0:
        return finite[0][0]
    return -20.0 * math.log10(sum(10.0 ** ((g - p) / 20.0) for p, g in finite))


def cascade(stages: Iterable[RfStageSpec]) -> RfStageSpec:
    """The chain's figures at its input: gains add; the noise figure follows Friis; the
    third-order intercept and the compression point are the reciprocal sum of the stages' points
    referred to the input (in mW), the second-order intercept the same sum in voltage form
    (``_input_referred_amplitude``). A chain of one stage is that stage."""
    chain = list(stages)
    gain = 0.0
    ip2: list[tuple[float, float]] = []
    ip3: list[tuple[float, float]] = []
    p1: list[tuple[float, float]] = []
    for s in chain:
        ip2.append((s.iip2_dbm, gain))
        ip3.append((s.iip3_dbm, gain))
        p1.append((s.p1db_dbm, gain))
        gain += s.gain_db
    return RfStageSpec(
        gain_db=chain[0].gain_db if len(chain) == 1 else gain,
        noise_figure_db=_noise_figure(chain),
        iip2_dbm=_input_referred_amplitude(ip2),
        iip3_dbm=_input_referred(ip3),
        p1db_dbm=_input_referred(p1),
    )


def noise_floor_dbm(nf_db: float, bandwidth_hz: float) -> float:
    """Noise power at the input of a receiver with noise figure ``nf_db``, in ``bandwidth_hz``."""
    return KT0_DBM_HZ + nf_db + 10.0 * math.log10(bandwidth_hz)


@dataclass(frozen=True)
class Products:
    """Distortion products as lines at the chain's input (what an ideal chain would have to be fed
    to show them)."""

    freq_hz: np.ndarray
    power_dbm: np.ndarray
    #: Input lines beyond the strongest ``MAX_PRODUCT_LINES`` were left out: an approximation.
    dropped: bool = False


def products(freq_hz: np.ndarray, power_dbm: np.ndarray, spec: RfStageSpec) -> Products:
    """The second- and third-order products the chain's intercepts make of the input lines: per
    line 2f and 3f; per pair f₁±f₂, 2f₁−f₂ and 2f₂−f₁. Two equal tones at P make third-order
    products at 3P − 2·IIP3 and second-order ones at 2P − IIP2; a harmonic is 6.02 dB (second) or
    9.54 dB (third) below the two-tone product of the same order."""
    f = np.asarray(freq_hz, dtype=float)
    p = np.asarray(power_dbm, dtype=float)
    dropped = f.size > MAX_PRODUCT_LINES
    if dropped:
        keep = np.sort(np.argsort(p, kind="stable")[-MAX_PRODUCT_LINES:])
        f, p = f[keep], p[keep]
    out_f: list[np.ndarray] = []
    out_p: list[np.ndarray] = []
    i, j = np.triu_indices(f.size, k=1)
    if math.isfinite(spec.iip2_dbm):
        out_f += [2 * f, f[i] + f[j], np.abs(f[i] - f[j])]
        out_p += [2 * p - spec.iip2_dbm - _db(4.0), *[p[i] + p[j] - spec.iip2_dbm] * 2]
    if math.isfinite(spec.iip3_dbm):
        out_f += [3 * f, np.abs(2 * f[i] - f[j]), np.abs(2 * f[j] - f[i])]
        out_p += [
            3 * p - 2 * spec.iip3_dbm - _db(9.0),
            2 * p[i] + p[j] - 2 * spec.iip3_dbm,
            2 * p[j] + p[i] - 2 * spec.iip3_dbm,
        ]
    if not out_f:
        return Products(np.zeros(0), np.zeros(0), dropped)
    freqs, powers = np.concatenate(out_f), np.concatenate(out_p)
    real = freqs > 0.0
    return Products(freqs[real], powers[real], dropped)


def compress(p_in_dbm: float, spec: RfStageSpec) -> float:
    """The chain's gain (dB) for a tone at ``p_in_dbm``: the third-order model y = a₁x + a₃x³ with
    a₃ set by the 1 dB compression point, so the gain is down exactly 1 dB at P1dB. Past the
    model's output maximum the output stays there (the gain falls dB for dB)."""
    if not math.isfinite(spec.p1db_dbm):
        return spec.gain_db
    r = _lin(p_in_dbm - spec.p1db_dbm)  # input power over the power at P1dB
    peak = 1.0 / (3.0 * _C1DB)
    factor = 1.0 - _C1DB * r if r <= peak else (2.0 / 3.0) * math.sqrt(peak / r)
    return spec.gain_db + 20.0 * math.log10(factor)


__all__ = [
    "KT0_DBM_HZ",
    "MAX_PRODUCT_LINES",
    "Products",
    "cascade",
    "compress",
    "noise_floor_dbm",
    "products",
]
