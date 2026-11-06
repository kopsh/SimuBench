"""The scope's shape measurements (#248) and the generic status register block, below a model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from simubench.library.components.measurements import measure

pytestmark = pytest.mark.unit

DT = 1e-6


def pulse(duty: float = 0.3, ringing: float = 0.0) -> np.ndarray:
    """1 kHz pulses, 0 to 1 V, edges smoothed over 11 samples; ``ringing`` overshoots each edge."""
    t = np.arange(10_000) * DT
    v = np.where((t * 1e3) % 1 < duty, 1.0, 0.0)
    v = np.convolve(v, np.ones(11) / 11, mode="same")
    if ringing:
        edges = np.flatnonzero(np.diff(np.where((t * 1e3) % 1 < duty, 1, 0)) > 0)
        for e in edges:
            v[e + 6 : e + 9] += ringing  # just after the rising edge settles
    return v


def test_levels_and_overshoot():
    v = pulse(ringing=0.1)
    assert measure("VTOP", v, DT) == pytest.approx(1.0)
    assert measure("VBASe", v, DT) == pytest.approx(0.0)
    assert measure("OVERshoot", v, DT) == pytest.approx(10.0, abs=0.5)
    assert measure("PREShoot", v, DT) == pytest.approx(0.0)


def test_a_noisy_records_overshoot_reads_the_noise_excursion_and_the_ringing_shows():
    """A clean sine on a noisy record reads the noise excursion's order — the extremum rides a
    few sigma above the histogram's level cluster (the real instruments' own method, the
    InfiniiVision Programmer's Guide's (Vmax - Vtop)/(Vtop - Vbase), #782) — and a genuine
    ringing shows on top of it instead of being masked."""
    rng = np.random.default_rng(7)
    t = np.arange(100_000) * DT
    sine = np.sin(2 * math.pi * 1e3 * t) + rng.normal(0.0, 0.005, 100_000)  # sigma: 0.5% of 1 V
    over = measure("OVERshoot", sine, DT)
    amp = measure("VAMPlitude", sine, DT)
    # the excursion in sigmas of the noise: the extremum's ~4 sigma plus the mode's inward step
    assert 3.0 <= over / 100.0 * amp / 0.005 <= 6.0, over
    noisy_pulse = pulse(ringing=0.1) + rng.normal(0.0, 0.005, 10_000)
    assert 7.0 <= measure("OVERshoot", noisy_pulse, DT) <= 14.0


def test_edge_times_widths_and_duty():
    v = pulse()
    # A linear 11-sample ramp: 10 % to 90 % is 0.8 x 11 samples.
    assert measure("RISetime", v, DT) == pytest.approx(8.8e-6, rel=0.02)
    assert measure("FALLtime", v, DT) == pytest.approx(8.8e-6, rel=0.02)
    assert measure("PWIDth", v, DT) == pytest.approx(300e-6, rel=1e-3)
    assert measure("NWIDth", v, DT) == pytest.approx(700e-6, rel=1e-3)
    assert measure("DUTYcycle", v, DT) == pytest.approx(30.0, rel=1e-3)
    assert measure("NDUTy", v, DT) == pytest.approx(70.0, rel=1e-3)


def test_edge_and_pulse_counts():
    v = pulse()
    assert measure("PEDGes", v, DT) == 9.0  # the record starts high: 9 rising, 10 falling
    assert measure("NEDGes", v, DT) == 10.0
    assert measure("PPULses", v, DT) == 9.0
    assert measure("NPULses", v, DT) == 9.0


def test_a_flat_record_has_no_edges_to_time():
    v = np.zeros(1000)
    assert math.isnan(measure("RISetime", v, DT))
    assert math.isnan(measure("DUTYcycle", v, DT))
