"""The shape of a scope channel's bandwidth filter (#607): first order (an analog front end) and
the DSP shapes of the Infiniium UXR: brick wall (flat magnitude), Gaussian and 4th-order Bessel."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from simubench.engine.signal import Signal, bessel4, brickwall, butterworth4, gaussian_lp, lowpass1
from simubench.library.components.scope import ScopeVertical
from simubench.scpi.errors import ScpiError

FC = 1.0e9
SHAPES = {
    "first_order": lowpass1,
    "brickwall": brickwall,
    "gaussian": gaussian_lp,
    "bessel4": bessel4,
    "butterworth4": butterworth4,
}


@pytest.mark.unit
@pytest.mark.parametrize("name", ["first_order", "gaussian", "bessel4", "butterworth4"])
def test_each_shape_is_3_db_down_at_the_cutoff_and_unity_at_dc(name):
    h = SHAPES[name](FC)
    assert abs(h(np.array([FC]))[0]) == pytest.approx(1 / math.sqrt(2), rel=1e-9)
    assert h(np.zeros(1))[0] == pytest.approx(1.0)


@pytest.mark.unit
def test_the_brick_wall_passes_up_to_the_cutoff_and_nothing_above():
    h = brickwall(FC)
    assert h(np.array([0.0, 0.5 * FC, FC, 1.001 * FC, 5 * FC])).tolist() == [1, 1, 1, 0, 0]


@pytest.mark.unit
@pytest.mark.parametrize("name", ["gaussian", "bessel4", "butterworth4"])
def test_the_smooth_shapes_fall_monotonically(name):
    mag = np.abs(SHAPES[name](FC)(np.linspace(0, 5 * FC, 501)))
    assert np.all(np.diff(mag) <= 1e-15)  # non-increasing (Butterworth is maximally flat near DC)
    assert mag[-1] < 0.02  # far steeper than first order above the cutoff
    assert abs(lowpass1(FC)(np.array([5 * FC]))[0]) > 0.19


@pytest.mark.unit
def test_butterworth_rolls_off_between_the_brick_wall_and_the_bessel():
    """The guide (:CHANnel<N>:ISIM:BWLimit:TYPE): a roll-off between the Brick Wall and the Bessel."""
    f = np.array([1.5 * FC, 2 * FC, 3 * FC])
    butter, bess = np.abs(butterworth4(FC)(f)), np.abs(bessel4(FC)(f))
    assert np.all(butter < bess)
    assert np.all(butter > np.abs(brickwall(FC)(f)))
    assert butter[1] == pytest.approx(1 / math.sqrt(1 + 2**8), rel=1e-9)  # |H|^2 = 1 / (1 + x^8)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["bessel4", "butterworth4"])
def test_the_filter_delay_is_removed(name):
    """Group delay ~0 at low frequency: the filter does not shift the record against other channels."""
    h = SHAPES[name](FC)
    f = np.array([1e6, 2e6])
    phase = np.unwrap(np.angle(h(f)))
    delay = -(phase[1] - phase[0]) / (2 * math.pi * (f[1] - f[0]))
    assert abs(delay) < 1e-15


def _vertical(response: str) -> ScopeVertical:
    return ScopeVertical(
        "front",
        {
            "bandwidth": 33e9,
            "bw_limit": True,
            "bw_limit_hz": FC,
            "scale": 0.1,
            "offset": 0.0,
            "response": response,
        },
        None,
        SimpleNamespace(),  # type: ignore[arg-type]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("response", "expected"), [("brickwall", 0.0), ("gaussian", 2**-0.72), ("bessel4", None)]
)
def test_the_channel_applies_its_shape_at_the_limit(response, expected, monkeypatch):
    v = _vertical(response)
    sine = Signal.lines(np.array([1.2 * FC]), np.array([1.0 + 0j]))
    monkeypatch.setattr(v, "input", lambda port: sine)
    amp = abs(v.output("out").amp[0])
    if expected is None:
        assert amp == pytest.approx(abs(bessel4(FC)(np.array([1.2 * FC]))[0]))
    else:
        assert amp == pytest.approx(expected, rel=1e-9)  # gaussian: 2^-(1.2^2 / 2)


@pytest.mark.unit
def test_first_order_is_the_default_and_unchanged(monkeypatch):
    v = ScopeVertical("front", {"bandwidth": FC}, None, SimpleNamespace())  # type: ignore[arg-type]
    sine = Signal.lines(np.array([FC]), np.array([1.0 + 0j]))
    monkeypatch.setattr(v, "input", lambda port: sine)
    assert v.output("out").amp[0] == pytest.approx(lowpass1(FC)(np.array([FC]))[0])


@pytest.mark.unit
def test_an_unknown_shape_is_refused():
    v = _vertical("butterworth")
    with pytest.raises(ScpiError):
        v.choice("response")
