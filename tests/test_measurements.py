"""The measurement algorithms as a pure module: no component, no session, importable alone."""

from __future__ import annotations

import math

import numpy as np
import pytest

from simubench.library.components import measurements as M

pytestmark = pytest.mark.unit

DT = 1e-6


def sine(cycles: float = 5.0, n: int = 5000, noise: float = 0.0) -> np.ndarray:
    t = np.arange(n) * DT
    v = np.sin(2 * math.pi * (cycles / (n * DT)) * t)  # ``cycles`` over the 5 ms record
    return v + (noise * np.sin(n * t * 1e6) if noise else 0.0)


def test_the_module_imports_without_the_component_library():
    import simubench.library.components.measurements  # noqa: F401

    assert set(M.MEASUREMENTS) == {
        "VPP", "VMAX", "VMIN", "VAMPlitude", "VRMS", "VAVerage", "FREQuency", "PERiod"
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
    with pytest.raises(Exception) as err:
        M.measure("NOPE", v, DT)
    assert err.value.args[0] == -113  # ScpiError: not a measurement


def test_mid_crossings_and_edge_spec():
    # a two-level square: the histogram forms no peak, so the middle falls back to the extremes
    square = np.where((np.arange(10_000) * DT * 1e3) % 1 < 0.5, 1.0, 0.0)
    rising = [d for _t, d in M.mid_crossings(square, DT, 0.1) if d == 1]
    assert len(rising) == 9
    assert M.edge_spec("+2") == (1, 2) and M.edge_spec("-3") == (-1, 3) and M.edge_spec(None) == (1, 1)
    with pytest.raises(Exception):
        M.edge_spec("0")
