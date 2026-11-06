"""The measurement counter's band floor (``hysteresis_floor_divs``, #745/#773/#781): the band
never drops below that fraction of the record's scale, so a trace of noise alone measures as
nothing, a signal above the floor measures normally, and 0 restores the old behaviour. Model
level, through sb-dso-4's SCPI (the shipped definition is noiseless, D42 — the noise rides in
as an override, like the reading-noise tests do)."""

from pathlib import Path

import pytest

from helpers import session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml"


def _value(s, cmd: str) -> float | None:
    """A measurement reply as a number; the model's cannot-measure values (the invalid value,
    UNDEF) come back as None."""
    text = session_query(s, "scope", cmd)
    if text is None or "UNDEF" in text:
        return None
    value = float(text)
    return None if value > 9.9e37 else value


def test_noise_alone_measures_as_nothing_and_the_floor_follows_the_scale():
    s = open_bench(BENCH, seed=5)
    try:
        session_query(s, "awg", ":OUTPut1 0")  # the generator off: CH1 carries nothing else
        s.instruments["scope"].components["ch1.front"].overrides["noise_divs"] = 0.02
        meas = s.instruments["scope"].components["meas"]
        session_query(s, "scope", ":CHANnel1:SCALe 10")
        session_query(s, "scope", ":DIGitize CHANnel1")
        rec = s.instruments["scope"].components["acq"].records[1]
        assert meas._band_floor(rec) == pytest.approx(0.16 * rec.scale)  # 1.6 V on this range
        # the noise (0.02 divisions rms) never crosses a 0.16-division band: nothing measurable
        assert _value(s, ":MEASure:FREQuency? CHANnel1") is None
        assert _value(s, ":MEASure:PERiod? CHANnel1") is None
        assert _value(s, ":MEASure:BRATe? CHANnel1") is None
        # the same noise on a 10x bigger range: the floor's volts follow the record's scale
        session_query(s, "scope", ":CHANnel1:SCALe 1")
        session_query(s, "scope", ":DIGitize CHANnel1")
        rec1 = s.instruments["scope"].components["acq"].records[1]
        assert meas._band_floor(rec1) == pytest.approx(0.16 * rec1.scale)
        assert meas._band_floor(rec1) == pytest.approx(meas._band_floor(rec) / 10)
        assert _value(s, ":MEASure:FREQuency? CHANnel1") is None
        # 0 restores the old behaviour: the noise's own span sizes the band and it "measures"
        meas.overrides["hysteresis_floor_divs"] = 0.0
        session_query(s, "scope", ":DIGitize CHANnel1")
        assert _value(s, ":MEASure:FREQuency? CHANnel1") is not None
    finally:
        s.close()


def test_a_signal_above_the_floor_measures_as_before():
    s = open_bench(BENCH, seed=7)
    try:
        # the bench's 1 kHz, 2 Vpp sine through the RC and the 10:1 probe: 0.17 Vpp — 3.4
        # divisions on a 50 mV/div screen, past the 0.16-division floor on both swings
        session_query(s, "awg", ":OUTPut1 1")
        session_query(s, "scope", ":CHANnel1:SCALe 5E-2")
        session_query(s, "scope", ":TIMebase:SCALe 5E-4")
        session_query(s, "scope", ":DIGitize CHANnel1")
        assert _value(s, ":MEASure:FREQuency? CHANnel1") == pytest.approx(1000.0, rel=0.02)
    finally:
        s.close()
