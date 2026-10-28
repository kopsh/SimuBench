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


def test_fidelity_notes_follow_the_signal():
    from simubench.engine.signal import FidelityNote

    c = Circuit()
    c.source("a", "s1", 50.0)
    c.resistor("a", "0", 50.0)
    c.source("b", "s2", 50.0)
    c.resistor("b", "0", 50.0)
    note = FidelityNote("truncated", "square wave: harmonics left out")
    emf = {"s1": Signal.lines([1000.0], [1.0]).with_note(note), "s2": Signal.lines([500.0], [1.0])}
    sol = c.solve([0.0, 500.0, 1000.0])
    assert sol.node_signal("a", emf).notes == (note,)
    assert sol.node_signal("b", emf).notes == ()  # s1 does not reach b


# ---- waveform shapes and fast sampling (SDG2042X duty and symmetry, issue 10) ----------------------


@pytest.mark.parametrize("duty", [0.5, 0.2, 0.8])
def test_pulse_train_matches_the_ideal_waveform(duty):
    from simubench.engine.signal import pulse_train

    t = np.linspace(0, 1e-3, 4000, endpoint=False)
    v = pulse_train(1000.0, 1.0, duty, 1e9).sample_rel(0, t)
    ideal = np.where(t < duty * 1e-3, 1.0, -1.0)
    away = (np.abs(t - duty * 1e-3) > 2e-5) & (t > 2e-5) & (t < 1e-3 - 2e-5)  # off the edges (Gibbs)
    assert np.max(np.abs(v - ideal)[away]) < 0.01
    assert v.mean() == pytest.approx(2 * duty - 1, abs=1e-3)


@pytest.mark.parametrize("symmetry", [0.5, 0.25, 0.9])
def test_ramp_with_symmetry_matches_the_ideal_waveform(symmetry):
    from simubench.engine.signal import triangle

    t = np.linspace(0, 1e-3, 2000, endpoint=False)
    v = triangle(1000.0, 1.0, symmetry, 1e9).sample_rel(0, t)
    u = (t / 1e-3 + symmetry / 2) % 1  # rising zero crossing at t = 0
    ideal = np.where(u < symmetry, -1 + 2 * u / symmetry, 1 - 2 * (u - symmetry) / (1 - symmetry))
    assert np.max(np.abs(v - ideal)) < 1e-3


def test_uniform_sampling_takes_the_matrix_path_and_agrees_with_the_direct_sum():
    from simubench.engine import signal as S

    sig = S.pulse_train(1000.0, 1.0, 0.2, 1.2e8, 0.3, 12345)
    off = np.arange(20000) * 2.5e-7 + 3e-6
    assert S._uniform_step(off) is not None
    fast = sig.sample_rel(10**12 + 7, off)
    base = S.phase_cycles(sig.freq, 10**12 + 7 - sig.t0_ns)
    a = sig.amp * np.exp(2j * np.pi * base)
    direct = sig.dc + np.real(a[None, :] * np.exp(2j * np.pi * sig.freq[None, :] * off[:500, None])).sum(
        axis=1
    )
    assert np.max(np.abs(fast[:500] - direct)) < 1e-10
