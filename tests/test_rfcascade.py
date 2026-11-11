"""Cascaded RF figures (#949, signal-chain.md 5.2): Friis, the intercept cascade, distortion
products and compression against hand calculations."""

from __future__ import annotations

import math

import numpy as np
import pytest

from simubench.engine.component import RfStageSpec
from simubench.engine.rfcascade import (
    MAX_PRODUCT_LINES,
    cascade,
    compress,
    noise_floor_dbm,
    products,
)

pytestmark = pytest.mark.unit

# The textbook receiver: a low-noise amplifier, a mixer and an IF amplifier.
LNA = RfStageSpec(gain_db=10.0, noise_figure_db=2.0, iip3_dbm=10.0)
MIXER = RfStageSpec(gain_db=-8.0, noise_figure_db=8.0, iip3_dbm=15.0)
IF_AMP = RfStageSpec(gain_db=20.0, noise_figure_db=5.0, iip3_dbm=5.0)


def test_three_stages_follow_friis_and_the_intercept_cascade():
    total = cascade([LNA, MIXER, IF_AMP])
    # F = F1 + (F2-1)/G1 + (F3-1)/(G1 G2), in linear units: 1.5849 + 5.3096/10 + 2.1623/1.5849
    f = 10**0.2 + (10**0.8 - 1) / 10 + (10**0.5 - 1) / 10**0.2
    # 1/IIP3 = 1/IIP3_1 + G1/IIP3_2 + G1 G2/IIP3_3, in mW: 1/10 + 10/31.62 + 1.585/3.162
    ip3 = 1 / (1 / 10 + 10 / 10**1.5 + 10**0.2 / 10**0.5)
    assert total.gain_db == pytest.approx(22.0)
    assert total.noise_figure_db == pytest.approx(10 * math.log10(f), abs=0.01)
    assert total.noise_figure_db == pytest.approx(5.416, abs=0.01)  # F = 3.480
    assert total.iip3_dbm == pytest.approx(10 * math.log10(ip3), abs=0.01)
    assert total.iip3_dbm == pytest.approx(0.37, abs=0.01)


def test_one_stage_is_itself():
    stage = RfStageSpec(gain_db=-7.3, noise_figure_db=24.3, iip2_dbm=41.7, iip3_dbm=13.1, p1db_dbm=-2.9)
    assert cascade([stage]) == stage


def test_one_declared_noise_figure_behind_an_attenuator_adds_the_attenuation_exactly():
    """The spectrum analyzer's earlier way: only a whole-receiver noise figure is declared."""
    receiver = RfStageSpec(noise_figure_db=24.3)
    undeclared = RfStageSpec()
    assert cascade([undeclared, receiver, undeclared]).noise_figure_db == 24.3
    assert cascade([RfStageSpec(gain_db=-10.0), receiver, undeclared]).noise_figure_db == 24.3 + 10.0


def test_a_matched_loss_counts_as_its_own_noise_figure():
    # 3 dB pad (F = 2) ahead of a 6 dB amplifier with a 3 dB pad behind: F = L1 F2 + (L3 - 1) L1 / G2
    total = cascade([RfStageSpec(gain_db=-3.0), RfStageSpec(10.0, 6.0), RfStageSpec(gain_db=-3.0)])
    f = 10**0.3 * 10**0.6 + (10**0.3 - 1) * 10**0.3 / 10
    assert total.noise_figure_db == pytest.approx(10 * math.log10(f), abs=1e-9)


def test_nothing_declared_gives_no_figures():
    total = cascade([RfStageSpec(), RfStageSpec(gain_db=4.0)])
    assert (total.noise_figure_db, total.iip2_dbm, total.iip3_dbm, total.p1db_dbm) == (
        None,
        math.inf,
        math.inf,
        math.inf,
    )


def test_noise_floor_is_kt0_plus_figure_plus_bandwidth():
    assert noise_floor_dbm(24.0, 1e3) == pytest.approx(-174.0 + 24.0 + 30.0)


def _at(found, f_hz: float) -> float:
    (k,) = np.flatnonzero(np.isclose(found.freq_hz, f_hz))
    return float(found.power_dbm[k])


def test_two_tones_make_third_order_products_from_the_intercept():
    f1, f2 = 100e6, 101e6
    found = products(np.array([f1, f2]), np.array([-10.0, -10.0]), RfStageSpec(iip3_dbm=10.0))
    assert _at(found, 2 * f1 - f2) == pytest.approx(-50.0)
    assert _at(found, 2 * f2 - f1) == pytest.approx(-50.0)
    assert _at(found, 3 * f1) == pytest.approx(-50.0 - 9.54, abs=0.01)
    assert not found.dropped


def test_two_tones_make_second_order_products_from_the_intercept():
    f1, f2 = 100e6, 130e6
    found = products(np.array([f1, f2]), np.array([-10.0, -10.0]), RfStageSpec(iip2_dbm=30.0))
    assert _at(found, f1 + f2) == pytest.approx(-50.0)
    assert _at(found, f2 - f1) == pytest.approx(-50.0)
    assert _at(found, 2 * f1) == pytest.approx(-50.0 - 6.02, abs=0.01)


def test_no_intercepts_no_products():
    found = products(np.array([1e6, 2e6]), np.array([0.0, 0.0]), RfStageSpec())
    assert found.freq_hz.size == 0


def test_lines_beyond_the_strongest_are_left_out_and_reported():
    n = MAX_PRODUCT_LINES + 1
    freq = 1e6 * (1 + np.arange(n))
    power = np.full(n, -20.0)
    freq[0], power[0] = 0.123e6, -90.0  # the weakest: the one left out
    found = products(freq, power, RfStageSpec(iip2_dbm=30.0))
    assert found.dropped
    assert not np.isclose(found.freq_hz, 2 * freq[0]).any()
    assert np.isclose(found.freq_hz, 2 * freq[1]).any()
    assert not products(freq[:-1], power[:-1], RfStageSpec(iip2_dbm=30.0)).dropped


def test_gain_is_down_one_db_at_the_compression_point():
    spec = RfStageSpec(gain_db=12.0, p1db_dbm=-5.0)
    assert compress(-5.0, spec) == pytest.approx(11.0)
    assert compress(-60.0, spec) == pytest.approx(12.0, abs=1e-4)
    assert compress(20.0, spec) < compress(10.0, spec) < 11.0
    assert compress(0.0, RfStageSpec(gain_db=12.0)) == 12.0
