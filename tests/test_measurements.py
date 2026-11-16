"""The measurement algorithms as a pure module: no component, no session, importable alone."""

from __future__ import annotations

import math

import numpy as np
import pytest

from simubench.library.components import measurements as M
from simubench.scpi.errors import ScpiError

pytestmark = pytest.mark.unit

DT = 1e-6


def sine(cycles: float = 5.0, n: int = 5000, noise: float = 0.0) -> np.ndarray:
    t = np.arange(n) * DT
    v = np.sin(2 * math.pi * (cycles / (n * DT)) * t)  # ``cycles`` over the 5 ms record
    return v + (noise * np.sin(n * t * 1e6) if noise else 0.0)


def test_the_module_imports_without_the_component_library():
    import simubench.library.components.measurements  # noqa: F401

    assert set(M.MEASUREMENTS) == {
        "VPP",
        "VMAX",
        "VMIN",
        "VAMPlitude",
        "VRMS",
        "VAVerage",
        "FREQuency",
        "PERiod",
    }


def test_top_base_reads_the_resting_levels_or_none():
    t = np.arange(10_000) * DT
    square = np.convolve(np.where((t * 1e3) % 1 < 0.5, 1.0, 0.0), np.ones(11) / 11, mode="same")
    assert M.top_base(square) == pytest.approx((1.0, 0.0))
    ramp = np.linspace(0.0, 2.0, 5000)  # fills the histogram evenly: no levels to tell
    assert M.top_base(ramp) is None
    assert M.top_base(np.array([1.0, 1.0])) is None  # no span, no levels


def test_period_counts_on_the_hysteresis_band_and_least_squares():
    v = sine(noise=0.05)
    assert M.period(v, DT) == pytest.approx(1e-3, rel=5e-3)
    assert M.period(sine(cycles=2.5), DT, min_cycles=3) is None  # fewer cycles in the record
    assert M.period(np.array([0.0, 0.0]), DT) is None


def test_the_band_floor_keeps_noise_alone_unmeasured():
    """The band never drops below ``min_band`` (#745): noise alone — whose own span would other-
    wise size the band — counts no crossings and the period stays unmeasurable, while a real
    signal above the floor still does."""
    noise = sine() * 0.01  # noise alone: its span is the noise
    assert M.period(noise, DT) is not None  # without the floor the noise "measures"
    assert M.period(noise, DT, min_band=0.2) is None  # the floor: no crossings to count
    assert M.period(sine(noise=0.05), DT, min_band=0.2) == pytest.approx(1e-3, rel=5e-3)
    # mid_crossings carries the same floor for the edge and pulse measurements
    assert M.mid_crossings(noise, DT, 0.1, min_band=0.2) == []
    # 5 cycles: the record opens at the middle going up, so the first countable swing is missing
    assert len(M.mid_crossings(sine(), DT, 0.1, min_band=0.2)) == 9


def test_crossings_interpolate_between_samples():
    v = np.array([0.0, 0.5, 1.0, 0.5, 0.0, -0.5])
    out = M.crossings(v, DT, 0.4, 0.05)
    assert [d for _t, d in out] == [1, -1]
    assert out[0][0] == pytest.approx(0.8 * DT)  # 0.4 between 0.0 and 0.5


def test_measure_answers_every_engine_word_and_nan_when_unmeasurable():
    v = sine()
    assert M.measure("VPP", v, DT) == pytest.approx(2.0, rel=1e-3)
    assert M.measure("VAMPlitude", v, DT) == pytest.approx(2.0, rel=1e-2)
    assert M.measure("FREQuency", v, DT) == pytest.approx(1000.0, rel=1e-2)
    assert M.measure("PERiod", v, DT) == pytest.approx(1e-3, rel=1e-3)
    assert math.isnan(M.measure("FREQuency", np.array([0.0, 0.0]), DT))
    with pytest.raises(ScpiError) as err:
        M.measure("NOPE", v, DT)
    assert err.value.args[0] == -113  # ScpiError: not a measurement


def test_mid_crossings_and_edge_spec():
    # a two-level square: the histogram forms no peak, so the middle falls back to the extremes
    square = np.where((np.arange(10_000) * DT * 1e3) % 1 < 0.5, 1.0, 0.0)
    rising = [d for _t, d in M.mid_crossings(square, DT, 0.1) if d == 1]
    assert len(rising) == 9
    assert M.edge_spec("+2") == (1, 2) and M.edge_spec("-3") == (-1, 3) and M.edge_spec(None) == (1, 1)
    with pytest.raises(ScpiError):
        M.edge_spec("0")


def _square(n: int = 4000, period: int = 1000) -> np.ndarray:
    """+-1 V square wave starting high: falls at 500, rises at 1000, ... (samples)."""
    return np.where((np.arange(n) % period) < period // 2, 1.0, -1.0)


def test_threshold_sets_give_the_levels_by_method_and_top_base_rule():
    v = _square()
    pct = M.threshold_levels(v, {"upper": 80.0, "middle": 50.0, "lower": 20.0})
    assert (pct.top, pct.base, pct.upper, pct.lower) == pytest.approx((1.0, -1.0, 0.6, -0.6))
    absolute = M.threshold_levels(
        v, {"method": "ABSolute", "abs_upper": 0.5, "abs_middle": 0.1, "abs_lower": -0.3}
    )
    assert (absolute.upper, absolute.middle, absolute.lower) == pytest.approx((0.5, 0.1, -0.3))
    hyst = M.threshold_levels(v, {"method": "HYSTeresis", "hyst_level": 0.2, "hyst_range": 0.1})
    assert (hyst.upper, hyst.middle, hyst.lower, hyst.band) == pytest.approx((0.3, 0.2, 0.1, 0.1))
    fixed = M.threshold_levels(v, {"tb_method": "ABSolute", "tb_top": 0.5, "tb_base": -0.5})
    assert fixed.middle == pytest.approx(0.0) and fixed.upper == pytest.approx(0.4)
    ramp = np.linspace(-1.0, 1.0, 1000)  # no histogram levels: HISTONLY gives none, STANdard the extremes
    assert M.threshold_levels(ramp, {"tb_method": "HISTONLY"}) is None
    assert M.threshold_levels(ramp, {}).top == pytest.approx(1.0)
    assert M.Levels(1, -1, 0.8, 0, -0.8, 0.1).at("UPPer") == 0.8
    with pytest.raises(ScpiError):
        M.Levels(1, -1, 0.8, 0, -0.8, 0.1).at("TOP")


def test_level_measurements_read_the_levels_the_shape_and_the_area():
    v = _square()
    v[1000:1010] = 1.3  # overshoot after the rise at 1000
    v[1100:1120] = 0.7  # a sag inside the positive pulse
    v[990:1000] = -1.2  # preshoot before it
    lv = M.threshold_levels(v, {"tb_method": "ABSolute", "tb_top": 1.0, "tb_base": -1.0})
    assert M.level_measure("VUPPer", v, 1.0, lv) == pytest.approx(0.8)
    assert M.level_measure("VOVershoot", v, 1.0, lv) == pytest.approx(1.3)
    assert M.level_measure("VPReshoot", v, 1.0, lv) == pytest.approx(-1.2)
    assert M.level_measure("VUNDershoot", v, 1.0, lv) == pytest.approx(0.7)
    assert M.level_measure("UNDershoot", v, 1.0, lv) == pytest.approx(15.0)  # 0.3 of 2 V
    assert M.level_measure("AREA:CYCLe", _square(), 1e-9, M.threshold_levels(_square(), {})) == pytest.approx(
        0.0, abs=1e-8
    )
    assert M.level_measure("AREA", np.ones(100), 0.01, lv) == pytest.approx(1.0)


def test_pulse_windows_bursts_and_crossings():
    v = _square()
    lv = M.threshold_levels(v, {})
    w = v.copy()
    w[1000:1050] = 0.5  # the positive pulse's first tenth sits at 0.5 V
    assert M.pulse_window_mean(w, 1.0, M.threshold_levels(w, {}), 50.0, True) == pytest.approx(1.0)
    assert M.pulse_window_mean(w, 1.0, M.threshold_levels(w, {}), 100.0, True) == pytest.approx(
        0.95, abs=0.01
    )
    assert M.pulse_window_mean(v, 1.0, lv, 50.0, False) == pytest.approx(-1.0)
    # bursts of two 10-sample pulses every 400 samples, idle 100
    b = -np.ones(2000)
    for start in (200, 600, 1000, 1400):
        b[start : start + 10] = 1.0
        b[start + 30 : start + 40] = 1.0
    lb = M.threshold_levels(b, {"tb_method": "MINmax"})
    assert M.burst_measure("BWIDth", b, 1.0, lb, 100.0) == pytest.approx(40.0, abs=1.0)
    assert M.burst_measure("BPERiod", b, 1.0, lb, 100.0) == pytest.approx(400.0, abs=1.0)
    assert M.burst_measure("BINTerval", b, 1.0, lb, 100.0) == pytest.approx(360.0, abs=1.0)
    assert math.isnan(M.burst_measure("BWIDth", b, 1.0, lb, 1000.0))  # no idle that long between them
    t = np.linspace(0.0, 1.0, 101)
    assert M.crossing_voltage(t, 0.5 * np.ones(101)) == pytest.approx(0.5)
    assert math.isnan(M.crossing_voltage(t, t + 1.0))
