"""The shared modulation math (issue 634): one Carson/Bessel expansion, the folding line builder,
and the fidelity bound — behind the AWG's and the RF generator's modulators alike."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.special import jv

from simubench.library.components.modulation import (
    LineBuilder,
    am_sidebands,
    angle_sidebands,
    carson_orders,
    omitted_power_fraction,
)

pytestmark = pytest.mark.unit


def test_carson_orders_counts_sides_of_the_absolute_index():
    """floor(|β|) + 1 per side; a bandwidth is never negative, so β = -2.4 keeps 3 like +2.4."""
    assert carson_orders(0) == 0
    assert carson_orders(2.4) == 3
    assert carson_orders(-2.4) == 3


def test_the_omitted_power_matches_the_kept_orders():
    beta = 2.0
    k = carson_orders(beta)
    n = np.arange(-k, k + 1)
    assert omitted_power_fraction(beta) == pytest.approx(1.0 - float(np.sum(jv(n, beta) ** 2)))
    assert omitted_power_fraction(0.0) == pytest.approx(0.0)


def test_lines_fold_per_the_cosine_convention():
    out = LineBuilder()
    out.add(-1e3, 2 + 3j)  # a negative-frequency line lands conjugated on the positive one
    out.add(1e3, 1 + 0j)
    out.add(0.0, 5 + 7j)  # zero frequency takes the DC term (the real part)
    out.add(0.0, 4 - 1j)
    assert sorted(out.freq) == [1e3, 1e3]  # duplicates merge in .signal()'s merged()
    assert sorted(out.amp, key=abs) == sorted([(2 - 3j), (1 + 0j)], key=abs)
    assert out.dc == 9.0  # 5 + Re(4 - 1j): both zero-frequency adds accumulate
    sig = out.signal()
    assert sig.dc == 9.0
    assert list(sig.freq) == [1e3]
    assert sig.amp[0] == pytest.approx((2 - 3j) + (1 + 0j))  # the folded conjugate plus the direct line


def test_angle_sidebands_expand_each_line_to_carsons_orders():
    out = LineBuilder()
    freq, amp = np.array([10e6, 20e6]), np.array([1.0 + 0j, 0.5 + 0j])
    beta = np.array([2.0, 1.5])
    angle_sidebands(out, freq, amp, beta, 1e3, 0.0)
    for f, a, b in zip(freq, amp, beta, strict=True):
        k = carson_orders(float(b))
        assert (
            sum(1 for g in out.freq if any(abs(g - (f + n * 1e3)) < 1e-9 for n in range(-k, k + 1)))
            >= 2 * k + 1
        )
        # the carrier line itself is the n = 0 sideband, J_0 shrinks it
        center = next(a2 for g, a2 in zip(out.freq, out.amp, strict=True) if abs(g - f) < 1e-9)
        assert center == pytest.approx(a * jv(0, b))
        # an edge sideband carries its Bessel factor and the phase step
        edge = next(a2 for g, a2 in zip(out.freq, out.amp, strict=True) if abs(g - (f + k * 1e3)) < 1e-9)
        assert edge == pytest.approx(a * jv(k, b) * complex(math.cos(k * 0.0), math.sin(k * 0.0)))


def test_a_scalar_beta_expands_like_a_per_line_one():
    one = LineBuilder()
    scalar = LineBuilder()
    freq, amp = np.array([5e6]), np.array([1.0 + 0j])
    angle_sidebands(one, freq, amp, np.array([1.4]), 2e3, math.pi / 4)
    angle_sidebands(scalar, freq, amp, 1.4, 2e3, math.pi / 4)
    assert sorted(zip(one.freq, one.amp, strict=True)) == sorted(zip(scalar.freq, scalar.amp, strict=True))


def test_am_sidebands_carry_the_tones_phase():
    out = LineBuilder()
    out.add(10e6, 1.0 + 0j)
    side = 0.8 / 2 / 1j * complex(math.cos(math.pi / 3), math.sin(math.pi / 3))
    am_sidebands(out, np.array([10e6]), np.array([1.0 + 0j]), 1e3, side)
    pairs = sorted(zip(out.freq, out.amp, strict=True))
    assert [f for f, _ in pairs] == pytest.approx([10e6 - 1e3, 10e6, 10e6 + 1e3])
    _, upper = pairs[2]
    assert upper == pytest.approx(side)  # J of m/2 from the carrier's 1.0
    assert pairs[0][1] == pytest.approx(side.conjugate())
