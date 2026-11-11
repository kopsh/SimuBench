"""The function generator's modulation block (#327): AM/FM/PM/FSK/PWM line by line, checked
against the closed forms — time samples of the modulated wave, rebuilt from the lines."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from simubench.engine.deviation import set_override
from simubench.engine.signal import pulse_train, sine
from simubench.library.components.sources import AwgModulator
from simubench.scpi.errors import ScpiError

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
    set_override(stub, "enabled", False)
    assert stub.output("out") is carrier
    out = _Stub(carrier, ext=sine(250.0, 2.5), type="AM", source="EXTernal", am_depth=100.0).output("out")
    m = out.merged()
    side = abs(m.amp[np.argmin(np.abs(m.freq - 1250))])
    assert side == pytest.approx(0.5 * 0.5 / 2, rel=1e-9)  # 2.5 V of 5 V full scale: m = 0.5


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        # the issue's reproduction: sb-awg-2 allows rate 1 mHz with deviation 10 MHz, beta = 1e10
        ("FM", {"fm_dev": 1e7, "int_rate": 1e-3}),
        ("PM", {"pm_dev": 1e9, "int_rate": 100.0}),
    ],
)
def test_an_angle_modulation_needing_too_many_lines_is_refused_before_the_expansion(kind, params):
    # Before #357 the Bessel loop enumerated 2*floor(beta)+1 lines first and hung the session.
    stub = _Stub(sine(1000.0, 0.5), type=kind, **params)
    start = time.perf_counter()
    with pytest.raises(ScpiError) as err:
        stub.output("out")
    assert err.value.code == -222
    assert time.perf_counter() - start < 1.0


def test_pwm_over_the_line_budget_cuts_harmonics_instead_of_refusing():
    """Issue #388: a pulse has bandwidth/f harmonics, so over the budget the expansion keeps the
    low harmonics and notes the cut; the count stops at the budget, so it stays fast (#357)."""

    class Synth:
        def function(self):
            return "SQUare"

        def frequency(self):
            return 1000.0

        def duty_ratio(self):
            return 0.5

        def param(self, key):
            return {"bandwidth": 300e6}[key]  # sb-awg-2: 300 000 harmonics of 1 kHz

    stub = _Stub(sine(1000.0, 0.5), type="PWM", pwm_dev=100.0, int_rate=10.0)
    start = time.perf_counter()
    out = AwgModulator._pwm_from(stub, Synth())
    assert time.perf_counter() - start < 1.0
    m = out.merged()
    assert 0 < len(m.freq) <= 2000
    assert abs(m.amp[np.argmin(np.abs(m.freq - 1000))]) > 0.1  # the fundamental is kept
    assert m.freq.max() < 300e6  # the high harmonics are cut
    (note,) = [n for n in out.notes if n.kind == "truncated"]
    kept = note.detail.removeprefix("PWM pulse: ").partition(" ")[0]
    assert kept.isdigit() and 0 < int(kept) <= 2000, note.detail
    assert note.detail == f"PWM pulse: {kept} of 300000 harmonics kept within the line budget"


def test_pwm_within_the_line_budget_carries_no_truncated_note():
    """Issue #440: when every harmonic the bandwidth implies fits the budget, nothing is cut and
    the signal carries no truncated note — the note is only for a real cut (50 kHz = 50 harmonics
    of 1 kHz, the default 10 % swing needs ~1750 of the 2000 lines)."""

    class Synth:
        def function(self):
            return "SQUare"

        def frequency(self):
            return 1000.0

        def duty_ratio(self):
            return 0.5

        def param(self, key):
            return {"bandwidth": 50e3}[key]

    out = AwgModulator._pwm_from(_Stub(sine(1000.0, 0.5), type="PWM", pwm_dev=10.0, int_rate=100.0), Synth())
    assert out.notes == ()
    m = out.merged()
    # The 50th, last implied harmonic is kept; its Carson sidebands reach ~53 kHz, so the top
    # line is not the harmonic itself.
    assert np.isclose(m.freq, 50e3).any()


def test_fm_readings_report_the_index_and_carsons_truncation_bound():
    """The fidelity bound the RF generator reports, now on the AWG's angle modulation too
    (issue 634): the worst carrier line's β (its harmonics index higher than the fundamental)."""
    from simubench.library.components.modulation import omitted_power_fraction

    m = _Stub(sine(1000.0, 0.5), type="FM", fm_dev=200.0, int_rate=100.0)
    r = m.readings()
    assert r["values"]["beta"] == pytest.approx(2.0)  # 200 Hz dev on the 100 Hz tone
    assert r["values"]["omitted_power"] == pytest.approx(omitted_power_fraction(2.0))
    assert _Stub(sine(1000.0, 0.5), type="AM", am_depth=50.0, int_rate=100.0).readings() is None
    assert _Stub(sine(1000.0, 0.5), type="FM", enabled=False, int_rate=100.0).readings() is None
