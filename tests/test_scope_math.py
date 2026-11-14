"""The math functions of an oscilloscope (#1074): the point-wise and windowed operators over the
records the acquisition keeps, the FFT and its measurements, and the markers — exercised on the
sb-dso-4 validation scope against a known single tone, as instrument-authoring.md 8.4 asks.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from simubench.api import open_bench
from simubench.library.components.scope_math import (
    WINDOWS,
    as_density,
    lowpass,
    magnitude,
    one_sided_fft,
    running_mean,
    window,
)
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]

#: The tone the tests measure: 10 dBm into 50 Ω is exactly 1 V peak, and 9765.625 Hz lands on
#: bin 5 of a 512-point transform of a 1 µs-spaced record — an integral number of cycles, so every
#: window reads the tone's own amplitude at its bin (the F1-3 criterion's condition).
TONE_HZ = 9765.625
TONE_DBM = 10.0
TONE_PEAK_V = math.sqrt(2.0 * 50.0 * 10e-3)

BENCH = BenchDefinition.model_validate(
    {
        "schema": "simubench.bench/1",
        "name": "scope-math",
        "instruments": {"sg": {"model": "sb-sg-1"}, "scope": {"model": "sb-dso-4"}},
        "wires": [{"from": "sg.RF_OUT", "to": "scope.CH1"}],
    }
)


def bench() -> tuple[Any, Any]:
    """The scope at 100 µs/div looking at the tone; returns the session and a query function."""
    s = open_bench(BENCH, clock="command", seed=7, idle="off")
    for cmd in (
        ":FREQuency:CW 9765.625",
        ":POWer:LEVel 10",
        ":OUTPut ON",
    ):
        s.write("sg", cmd.encode() + b"\n")
    for cmd in (
        ":CHANnel1:IMPedance FIFTy",
        ":CHANnel1:SCALe 0.2",
        ":CHANnel1:DISPlay ON",
        ":TIMebase:SCALe 1.0E-4",
        ":WAVeform:POINts 1000",
    ):
        s.write("scope", cmd.encode() + b"\n")

    def query(cmd: str) -> str:
        s.write("scope", cmd.encode() + b"\n")
        out = s.read("scope") if "?" in cmd else None
        return out.decode().strip() if out is not None else ""

    return s, query


def floats(reply: str) -> list[float]:
    """A comma-separated ASCII reply as numbers."""
    return [float(x) for x in reply.split(",")]


# ---- the pure operators ---------------------------------------------------------------------------


def test_running_mean_is_centred_and_clamped():
    import numpy as np

    v = np.arange(6, dtype=float)
    out = running_mean(v, 3)
    assert [float(x) for x in out] == [0.5, 1.0, 2.0, 3.0, 4.0, 4.5]


def test_every_window_reads_a_coherent_tones_own_amplitude():
    import numpy as np

    n = 512
    v = TONE_PEAK_V * np.cos(2.0 * math.pi * 5.0 * np.arange(n) / n)
    for kind in WINDOWS:
        x = one_sided_fft(v, window(kind, n))
        i = int(np.argmax(np.abs(x)))
        dbm = magnitude(x, "DB", 50.0)
        assert i == 5 and abs(abs(x[i]) - TONE_PEAK_V) < 5e-4
        assert abs(float(dbm[i]) - TONE_DBM) < 0.01


def test_lowpass_dc_gain_and_bandwidth():
    import numpy as np

    dt, n = 1e-6, 40000
    assert abs(float(lowpass(np.ones(n), dt, 1000.0)[-1])) == pytest.approx(1.0, abs=1e-6)
    v = np.sin(2.0 * math.pi * 1000.0 * np.arange(n) * dt)
    out = lowpass(v, dt, 1000.0)
    gain = float(np.sqrt(np.mean(out[n // 2 :] ** 2) / np.mean(v[n // 2 :] ** 2)))
    assert gain == pytest.approx(0.7071, abs=0.002)


def test_a_density_spreads_the_bin_over_the_bandwidth():
    import numpy as np

    values = np.array([0.0, 10.0])  # dBm
    out = as_density(values, "DBMHZ", 100.0)
    assert [float(x) for x in out] == [-20.0, -10.0]


# ---- the math functions over the scope's records ---------------------------------------------------


def test_the_point_wise_operators_match_the_hand_calculation():
    s, query = bench()
    try:
        query(":DIGitize")
        query(":WAVeform:FORMat ASCii")
        query(":WAVeform:SOURce CHANnel1")
        ch1 = floats(query(":WAVeform:DATA?"))
        assert len(ch1) == 1000
        peak = max(abs(v) for v in ch1)
        assert peak == pytest.approx(TONE_PEAK_V, rel=2e-3)
        # F1 = ADD(CHAN1, CHAN1): every point twice the channel's
        query(":FUNCtion1:ADD CHANnel1,CHANnel1")
        query(":WAVeform:SOURce FUNCtion1")
        f1 = floats(query(":WAVeform:DATA?"))
        assert len(f1) == len(ch1)
        for i in (0, 137, 500, 999):
            assert f1[i] == pytest.approx(2.0 * ch1[i], abs=1e-6)
        # F2 = INVert(F1): the chain of functions
        query(":FUNCtion2:INVert FUNCtion1")
        query(":WAVeform:SOURce FUNCtion2")
        f2 = floats(query(":WAVeform:DATA?"))
        assert f2[314] == pytest.approx(-f1[314], abs=1e-6)
    finally:
        s.close()


def test_a_function_of_two_constants_and_a_divide_hole():
    s, query = bench()
    try:
        query(":DIGITIZE")
        query(":FUNCtion1:DIVide CHANnel1,0.5")
        query(":WAVeform:FORMat ASCii")
        query(":WAVeform:SOURce FUNCtion1")
        f1 = floats(query(":WAVeform:DATA?"))
        assert f1[100] == pytest.approx(2.0 * f1[100] / 2.0, abs=1e-6)  # parse round-trip
        assert max(f1) > 1.0  # the 1 V tone halved is nothing: DIVide by 0.5 doubles it
    finally:
        s.close()


def test_smoothing_and_the_derivative_read_like_the_hand_filter():
    s, query = bench()
    try:
        query(":DIGitize")
        query(":WAVeform:FORMat ASCii")
        query(":FUNCtion1:SMOoth CHANnel1,5")
        query(":WAVeform:SOURce FUNCtion1")
        f1 = floats(query(":WAVeform:DATA?"))
        query(":WAVeform:SOURce CHANnel1")
        ch1 = floats(query(":WAVeform:DATA?"))
        hand = running_mean.__module__  # the operator under test is the pure function
        assert hand == "simubench.library.components.scope_math"
        for i in (0, 2, 500):
            want = sum(ch1[max(0, i - 2) : i + 3]) / len(ch1[max(0, i - 2) : i + 3])
            assert f1[i] == pytest.approx(want, abs=1e-6)
    finally:
        s.close()


def test_the_fft_trace_reads_the_tones_bin_amplitude_and_frequency():
    s, query = bench()
    try:
        query(":DIGITIZE")
        query(":FUNCtion1:FFTMagnitude CHANnel1")
        query(":FUNCtion1:FFT:WINDow HANNing")
        assert query(":FUNCtion1?") == '"CHANnel1"'
        assert query(":FUNCtion1:FFT:RESolution?") == query(":FUNCtion1:FFT:RESolution?")  # stable
        query(":WAVeform:FORMat ASCii")
        query(":WAVeform:SOURce FUNCtion1")
        trace = floats(query(":WAVeform:DATA?"))
        df = 1.0e6 / 512  # the record: 1 µs apart; the transform: 512 points
        best = max(range(len(trace)), key=lambda i: trace[i])
        assert best * df == pytest.approx(TONE_HZ, abs=1e-6)
        assert trace[best] == pytest.approx(TONE_DBM, abs=0.05)
        assert best == 5
    finally:
        s.close()


def test_the_fft_units_and_the_power_spectral_density():
    s, query = bench()
    try:
        query(":DIGITIZE")
        query(":FUNCtion1:FFTMagnitude CHANnel1")
        query(":WAVeform:FORMat ASCii")
        for units, at in (
            ("VRMS", TONE_PEAK_V / math.sqrt(2.0)),
            ("DBMV", 20 * math.log10(TONE_PEAK_V / math.sqrt(2) * 1e3)),
        ):
            query(f":FUNCtion1:FFT:VUNits {units}")
            query(":WAVeform:SOURce FUNCtion1")
            trace = floats(query(":WAVeform:DATA?"))
            assert max(trace) == pytest.approx(at, rel=1e-3)
        # PSD: the tone's dBm spread over the Hann window's equivalent noise bandwidth
        query(":FUNCtion1:PSDensity CHANnel1")
        query(":FUNCtion1:FFT:VUNits DBMHZ")
        query(":WAVeform:SOURce FUNCtion1")
        trace = floats(query(":WAVeform:DATA?"))
        enbw = 1.5 / (1.0e-6 * 512) / 512 * 512  # Hann: 1.5 bins' worth
        assert max(trace) == pytest.approx(TONE_DBM - 10 * math.log10(enbw), abs=0.05)
    finally:
        s.close()


# ---- the FFT measurements --------------------------------------------------------------------------


def test_the_fft_measurements_of_a_pure_tone():
    s, query = bench()
    try:
        query(":DIGITIZE")
        query(":FUNCtion1:FFTMagnitude CHANnel1")
        assert floats(query(":MEASure:FFT:FREQuency? FUNCtion1,1,-60")) == [pytest.approx(TONE_HZ, abs=1e-3)]
        assert floats(query(":MEASure:FFT:MAGNitude? FUNCtion1,1,-60")) == [pytest.approx(TONE_DBM, abs=0.05)]
        # a second peak above the level: none in a pure tone, so peak 2 answers the sentinel
        assert query(":MEASure:FFT:FREQuency? FUNCtion1,2,-60") == "9.99E+37"
        # the channel power of a band holding only the tone's bin is the tone itself (a ±1-bin
        # band: the Hann skirt at ±1 bin is −6 dB and a wider band catches it, as the real
        # instrument would); the PSD divides by the band's width
        cpw = floats(query(":MEASure:FFT:CPOWer? FUNCtion1,9765.625,1000"))
        assert cpw == [pytest.approx(TONE_DBM, abs=0.05)]
        psd = floats(query(":MEASure:FFT:PSD? FUNCtion1,9765.625,1000"))
        assert psd == [pytest.approx(TONE_DBM - 10 * math.log10(1000.0), abs=0.05)]
        # a pure sine has no harmonics: THD ~ 0
        thd = floats(query(":MEASure:FFT:THDistortion? FUNCtion1,MANual,9765.625"))
        assert 0.0 <= thd[0] < 0.1
        # the occupied bandwidth at 99 % is the tone's own bin width or less
        obw = floats(query(":MEASure:FFT:OBW? FUNCtion1,99"))
        assert 0.0 <= obw[0] <= 3.0e6
    finally:
        s.close()


def test_the_annotated_peaks_answer_their_frequencies_and_magnitudes():
    s, query = bench()
    try:
        query(":DIGITIZE")
        query(":FUNCtion1:FFTMagnitude CHANnel1")
        query(":FUNCtion1:FFT:PEAK:LEVel -40")
        freqs = floats(query(":FUNCtion1:FFT:PEAK:FREQuency?"))
        mags = floats(query(":FUNCtion1:FFT:PEAK:MAGNitude?"))
        assert TONE_HZ in [pytest.approx(f, abs=1e-3) for f in freqs]
        assert max(mags) == pytest.approx(TONE_DBM, abs=0.05)
    finally:
        s.close()


# ---- the markers ------------------------------------------------------------------------------------


def test_manual_markers_answer_their_deltas():
    s, query = bench()
    try:
        query(":MARKer:MODE MANual")
        query(":MARKer:X1Position 1.0E-3")
        query(":MARKer:X2Position 4.0E-3")
        query(":MARKer:Y1Position 0.1")
        query(":MARKer:Y2Position -0.2")
        assert floats(query(":MARKer:XDELta?")) == [pytest.approx(3.0e-3)]
        assert floats(query(":MARKer:YDELta?")) == [pytest.approx(-0.3)]
        # the compatibility spellings move the same markers
        query(":MARKer:TSTArt 0")
        assert floats(query(":MARKer:XDELta?")) == [pytest.approx(4.0e-3)]
    finally:
        s.close()


def test_tracking_markers_read_the_waveform_and_the_fft_trace():
    s, query = bench()
    try:
        query(":DIGITIZE")
        query(":MARKer:MODE WAVeform")
        query(":MARKer:X1Y1source CHANnel1")
        query(":MARKer:X1Position 0.0")
        x, y = floats(query(":MARKer:CURSor? STARt"))
        # at the trigger the tone crosses zero going up: y ~ 0
        assert x == pytest.approx(0.0, abs=1e-6)
        assert abs(y) < 0.05 * TONE_PEAK_V
        # on the FFT trace the cursor reads the tone's bin
        query(":FUNCtion1:FFTMagnitude CHANnel1")
        query(":MARKer:X1Y1source FUNCtion1")
        query(":MARKer:X1Position 9765.625")
        x, y = floats(query(":MARKer:CURSor? STARt"))
        assert x == pytest.approx(TONE_HZ, abs=1e-6)
        assert y == pytest.approx(TONE_DBM, abs=0.05)
        assert query(":MARKer:MODE?") == "WAV"  # the manual's Returned Format is the short form
    finally:
        s.close()


def test_the_numbered_markers_hold_their_fields():
    s, query = bench()
    try:
        query(":MARKer3:TYPE TRACk")
        assert query(":MARKer3:TYPE?") == "TRAC"
        query(":MARKer3:SOURce CHANnel1")
        assert query(":MARKer3:SOURce?") == "CHAN1"
        query(":MARKer3:ENABle ON")
        assert query(":MARKer3:ENABle?") == "1"
        query(":MARKer3:X:POSition 2.0E-4")
        assert floats(query(":MARKer3:X:POSition?")) == [pytest.approx(2.0e-4)]
        # a type change while on is a settings conflict
        query(":MARKer3:TYPE YMANual")
        assert query(":SYSTem:ERRor?").split(",")[0] == "-221"
        query(":MARKer20:X:POSition 1.0E-3")  # past the model's 12: -114
        assert query(":SYSTem:ERRor?").split(",")[0] == "-114"
    finally:
        s.close()
