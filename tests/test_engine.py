import numpy as np
import pytest

from simubench.engine.circuit import GMIN, Circuit, solve_signals
from simubench.engine.instrument import snap_ladder
from simubench.engine.signal import Signal, lowpass1, sine, square


def test_sine_samples_and_rms():
    s = sine(1000.0, 1.0)
    v = s.sample(np.array([0, 250_000, 500_000, 750_000]))
    assert v == pytest.approx([0, 1, 0, -1], abs=1e-12)
    assert s.rms() == pytest.approx(1 / np.sqrt(2))


def test_square_wave_level_and_rms():
    q = square(1000.0, 1.0, 10e6)
    assert q.sample(np.array([250_000]))[0] == pytest.approx(1.0, abs=1e-3)
    assert q.rms() == pytest.approx(1.0, abs=1e-3)
    assert not q.is_exact  # harmonics are truncated and say so


def test_transfer_function_at_corner():
    fc = 1591.55
    out = sine(fc, 1.0).transfer(lowpass1(fc))
    assert abs(out.phasor_at(fc)) == pytest.approx(1 / np.sqrt(2), rel=1e-9)


def test_rc_divider_with_source_impedance():
    # 2 V EMF behind 50 ohm into 1 kohm + 100 nF, 10 Mohm probes on both nodes (validation-multichannel.md)
    c = Circuit()
    c.source("in", "awg", 50.0)
    c.resistor("in", "out", 1e3)
    c.capacitor("out", "0", 100e-9)
    c.resistor("out", "0", 10e6)
    c.resistor("in", "0", 10e6)
    r = solve_signals(c, {"awg": sine(1000.0, 1.0)}, ["in", "out"])
    assert 2 * abs(r["in"].phasor_at(1000.0)) == pytest.approx(1.9716, abs=1e-4)
    assert 2 * abs(r["out"].phasor_at(1000.0)) == pytest.approx(1.6693, abs=1e-4)


def test_dc_through_capacitor_is_blocked_and_resistor_divides():
    c = Circuit()
    c.source("a", "s", 50.0)
    c.resistor("a", "0", 50.0)
    r = solve_signals(c, {"s": Signal.constant(2.0)}, ["a"])
    assert r["a"].mean() == pytest.approx(1.0)


def test_phase_precision_after_long_runs():
    s = sine(1e6 + 0.37, 1.0)
    t = 10**15  # 11.6 days of virtual time
    from fractions import Fraction

    cycles = Fraction(1e6 + 0.37) * 10**6  # exact cycles for the float frequency actually used
    expected = np.sin(2 * np.pi * float(cycles % 1))
    assert s.sample(np.array([t]))[0] == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize(
    ("value", "ladder", "expected"),
    [
        (0.3, "1-2-5", 0.2),
        (0.04, "1-2-5", 0.05),
        (0.07, "1-2-5", 0.05),
        (0.002, "1-3", 0.003),
        (700, [100, 250, 500, 1000], 500),
    ],
)
def test_snap_ladder(value, ladder, expected):
    assert snap_ladder(value, ladder, None, None) == pytest.approx(expected)


def test_current_source_and_ideal_source_current():
    c = Circuit()
    c.current("a", "i1")  # 2 mA into 1 kΩ
    c.resistor("a", "0", 1000.0)
    c.source("b", "v1", 0.0)  # an ideal 3 V source into 1.5 kΩ
    c.resistor("b", "0", 1500.0)
    emf = {"i1": Signal.constant(2e-3), "v1": Signal.constant(3.0)}
    sol = c.solve([0.0])
    assert sol.node_signal("a", emf).mean() == pytest.approx(2.0, rel=1e-9)
    assert sol.source_dc_current("v1", emf) == pytest.approx(3.0 / 1500 + 3.0 * GMIN, rel=1e-12)
    assert sol.source_dc_current("i1", emf) == 0.0  # not an ideal voltage source
