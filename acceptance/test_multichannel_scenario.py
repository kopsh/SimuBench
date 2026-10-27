"""Multi-channel scenario (docs/validation-multichannel.md): expectations from circuit theory."""

from __future__ import annotations

import math

import pytest
from refcalc.circuits import fit_sine, rc_chain

from .conftest import capture, open_rig

PROBE_LOAD = 10e6


@pytest.mark.parametrize(
    ("f", "gain", "phase_deg"),
    [
        (100.0, 0.99793, -3.595),
        (500.0, 0.95394, -17.439),
        (1000.0, 0.84667, -32.139),
        (1591.55, 0.70707, -44.997),
        (3000.0, 0.46864, -62.051),
        (10000.0, 0.15718, -80.956),
    ],
)
def test_gain_and_phase_ch2_over_ch1(f, gain, phase_deg):
    rm, awg, scope = open_rig("two_channel.bench.yaml")
    awg.write(":OUTPut1:LOAD INF;:OUTPut1:STATe ON")
    awg.write(f":SOURce1:FREQuency {f}")
    scope.write(":CHANnel1:SCALe 0.05;:CHANnel2:SCALe 0.05;:CHANnel2:DISPlay ON")
    scope.write(f":TIMebase:SCALe {2 / f}")
    scope.write(":DIGitize")
    c1 = capture(scope, probe=10.0, source=1, digitize=False)
    c2 = capture(scope, probe=10.0, source=2, digitize=False)
    assert list(c1.preamble[4:7]) == list(c2.preamble[4:7])  # shared timebase and trigger
    p1, ph1, _ = fit_sine(c1.t, c1.v, f)
    p2, ph2, _ = fit_sine(c2.t, c2.v, f)
    measured_phase = math.degrees((ph2 - ph1 + math.pi) % (2 * math.pi) - math.pi)
    # validation-multichannel.md table, and the same from the independent reference calculation
    v_in, v_out = rc_chain(f, 1.0, rs=50, load_in=PROBE_LOAD, load_out=PROBE_LOAD)
    assert abs(v_out / v_in) == pytest.approx(gain, abs=1e-5)
    assert p2 / p1 == pytest.approx(gain, rel=1e-3)
    assert measured_phase == pytest.approx(phase_deg, abs=0.1)
    rm.close()


def test_channel_suffix_out_of_range():
    rm, _, scope = open_rig("two_channel.bench.yaml")
    scope.write(":CHANnel5:SCALe 1")
    assert scope.query(":SYSTem:ERRor?").startswith("-114,")
    rm.close()


def test_default_channel_is_one():
    rm, awg, _ = open_rig("two_channel.bench.yaml")
    awg.write(":FREQuency 1234")
    assert float(awg.query(":SOURce1:FREQuency?")) == 1234
    assert float(awg.query(":SOURce2:FREQuency?")) == 1000
    rm.close()


def test_per_channel_capability_limits():
    rm, awg, _ = open_rig("two_channel.bench.yaml")
    awg.write(":SOURce2:FUNCtion SQUare;:SOURce2:FREQuency 30e6")  # square limited to 25 MHz
    assert awg.query(":SYSTem:ERRor?").startswith("-221,")
    awg.write(":SOURce1:FREQuency 70e6")  # beyond the 60 MHz range
    assert awg.query(":SYSTem:ERRor?").startswith("-222,")
    rm.close()
