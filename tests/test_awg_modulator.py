"""The function generator's modulation block (#327): AM/FM/PM/FSK/PWM line by line, checked
against the closed forms — time samples of the modulated wave, rebuilt from the lines."""

from __future__ import annotations

import math

import numpy as np
import pytest

from simubench.engine.signal import pulse_train, sine
from simubench.library.components.sources import AwgModulator

pytestmark = pytest.mark.unit


class _Stub(AwgModulator):
    """The block with its input, ext input and synth given directly."""

    def __init__(self, carrier, ext=None, synth=None, **params):
        self._carrier, self._ext, self._synth = carrier, ext, synth
        self.overrides = {"enabled": True, **params}
        self._params = {}
        self.owner = None

    def param(self, key):
        return self.overrides.get(key, self.defaults[key])

    def input(self, port):
        from simubench.engine.signal import Signal

        return self._carrier if port == "in" else (self._ext or Signal.zero())


def samples(sig, t):
    m = sig.merged()
    return m.dc + np.real(np.sum(m.amp[:, None] * np.exp(2j * np.pi * m.freq[:, None] * t[None, :]), axis=0))


T = np.linspace(0, 0.02, 4001)


def test_am_multiplies_the_wave_by_one_plus_m_sin():
    out = _Stub(sine(1000.0, 0.5), type="AM", am_depth=50.0, int_rate=100.0).output("out")
    want = (1 + 0.5 * np.sin(2 * np.pi * 100 * T)) * 0.5 * np.sin(2 * np.pi * 1000 * T)
    assert np.max(np.abs(samples(out, T) - want)) < 1e-12


def test_fm_and_pm_are_bessel_lines_to_carsons_rule():
    out = _Stub(sine(1000.0, 0.5), type="FM", fm_dev=300.0, int_rate=100.0).output("out")
    beta = 3.0
    want = 0.5 * np.sin(2 * np.pi * 1000 * T + beta * np.sin(2 * np.pi * 100 * T))
    assert np.max(np.abs(samples(out, T) - want)) < 0.05  # Carson keeps >98 % of the power
    pm = _Stub(sine(1000.0, 0.5), type="PM", pm_dev=math.degrees(0.5), int_rate=100.0).output("out")
    want = 0.5 * np.sin(2 * np.pi * 1000 * T + 0.5 * np.sin(2 * np.pi * 100 * T))
    assert np.max(np.abs(samples(pm, T) - want)) < 0.04  # Carson drops J2(0.5) ≈ 0.03


def test_fsk_alternates_between_the_carrier_and_the_hop():
    out = _Stub(sine(1000.0, 0.5), type="FSK", fsk_hop=2000.0, fsk_rate=50.0).output("out")
    m = out.merged()
    assert m.amp[np.argmin(np.abs(m.freq - 1000))] == pytest.approx(0.5 * -1j / 2, abs=1e-12)
    assert m.amp[np.argmin(np.abs(m.freq - 2000))] == pytest.approx(0.5 * -1j / 2, abs=1e-12)
    # in the first half period (gate high) the wave is mostly the carrier: correlate with it
    t = np.linspace(0.001, 0.009, 2001)
    v = samples(out, t)
    assert np.mean(v * np.sin(2 * np.pi * 1000 * t)) > 3 * abs(np.mean(v * np.sin(2 * np.pi * 2000 * t)))


def test_pwm_swings_the_mean_with_the_duty():
    class Synth:
        def function(self):
            return "SQUare"

        def frequency(self):
            return 1000.0

        def duty_ratio(self):
            return 0.5

        def param(self, key):
            return {"bandwidth": 50e3}[key]

    stub = _Stub(pulse_train(1000.0, 0.5, 0.5, 50e3), type="PWM", pwm_dev=20.0, int_rate=10.0)
    stub._pwm_synth = Synth()
    stub._pwm = lambda: AwgModulator._pwm_from(stub, Synth())
    out = stub.output("out")
    m = out.merged()
    # the duty d(t) = 0.5 + 0.2 sin: the mean of the ±0.5 pulse is d - 0.5, a 10 Hz line of 0.2
    assert abs(m.amp[np.argmin(np.abs(m.freq - 10))]) == pytest.approx(0.2, rel=1e-9)


def test_off_passes_the_wave_and_ext_sets_the_rate():
    carrier = sine(1000.0, 0.5)
    stub = _Stub(carrier, type="AM")
    stub.overrides["enabled"] = False
    assert stub.output("out") is carrier
    out = _Stub(carrier, ext=sine(250.0, 2.5), type="AM", source="EXTernal", am_depth=100.0).output("out")
    m = out.merged()
    side = abs(m.amp[np.argmin(np.abs(m.freq - 1250))])
    assert side == pytest.approx(0.5 * 0.5 / 2, rel=1e-9)  # 2.5 V of 5 V full scale: m = 0.5
