"""Keysight N9042B UXA (issue 336, capability #616): the SA-mode reference's flow on the example
bench.

The manual cases (tests/models/keysight-n9042b/) check command by command; these tests run the
real-instrument-style script (examples/scripts/keysight_n9042b_measure.py) unchanged on the example
bench, and check what the cases cannot: the option-526 preset above the old 7.5 GHz limit, the
X-Series RBW steps in the automatic coupling, Normal detection on noise, min hold, the virtual time
of a single sweep and the delta marker's offset.
"""

from pathlib import Path

import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-n9042b.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_n9042b_measure.py"
POINTS = 1001


script = load_script(SCRIPT)


def session():
    return open_bench(BENCH, idle="off")  # virtual time only advances with the commands (#455)


def q(s, command: str) -> str | None:
    return session_query(s, "sa", command)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["sa"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0] == "Keysight Technologies,N9042B,MY00000001,A.25.05"
    assert out[1] == "rbw: 9100 Hz, sweep: 30.190 ms"
    assert out[2] == "marker: 5800000000 Hz, -10.000 dBm"
    assert out[3] == "counter: 5800000000 Hz"
    assert out[4] == f"trace: {POINTS} points, peak -10.000 dBm"
    assert out[5] == '+0,"No error"'


def test_preset_window_reaches_26_5_ghz_and_finds_the_tone():
    s = session()
    try:
        assert q(s, ":FREQ:STOP?") == "+2.65000000E+010"
        assert q(s, ":INIT:CONT OFF") is None
        assert q(s, ":INIT:IMM") is None
        assert q(s, ":CALC:MARK:MAX") is None
        bucket = 26.49e9 / (POINTS - 1)
        assert float(q(s, ":CALC:MARK:X?")) == pytest.approx(5.8e9, abs=bucket)
        assert float(q(s, ":CALC:MARK:Y?")) == pytest.approx(-10.0, abs=0.5)
    finally:
        s.close()


@pytest.mark.parametrize(
    ("span", "rbw"), [(1.0e6, 9.1e3), (10.0e6, 91.0e3), (100.0e3, 910.0), (300.0e6, 2.7e6), (400.0e6, 3.0e6)]
)
def test_automatic_rbw_is_span_over_106_on_the_ten_percent_steps(span, rbw):
    s = session()
    try:
        assert q(s, ":FREQ:CENT 5.8 GHz") is None
        assert q(s, f":FREQ:SPAN {span}") is None
        assert float(q(s, ":BAND?")) == pytest.approx(rbw, rel=1e-9)
        assert float(q(s, ":BAND:VID?")) == pytest.approx(rbw, rel=1e-9)
    finally:
        s.close()


def test_normal_detection_alternates_on_noise_and_keeps_the_peak():
    s = session()
    try:
        assert q(s, ":FREQ:CENT 5.8 GHz;:FREQ:SPAN 100 MHz") is None
        assert q(s, ":BAND 1 kHz") is None  # many resolution cells per bucket: noise-like
        normal = [float(v) for v in q(s, ":TRAC:DATA? TRACE1").split(",")]
        assert q(s, ":DET:TRAC POS") is None
        peak = [float(v) for v in q(s, ":TRAC:DATA? TRACE1").split(",")]
    finally:
        s.close()
    noise_even = [normal[i] for i in range(0, 400, 2)]
    noise_odd = [normal[i] for i in range(1, 400, 2)]
    assert sum(noise_even) / len(noise_even) - sum(noise_odd) / len(noise_odd) > 10.0  # max vs min
    assert max(normal) == pytest.approx(max(peak), abs=0.01)  # the CW tone reads as with peak


def test_min_hold_never_exceeds_clear_write():
    s = session()
    try:
        assert q(s, ":FREQ:CENT 5.8 GHz;:FREQ:SPAN 1 MHz;:INIT:CONT OFF") is None
        assert q(s, ":DET:TRAC SAMP;:AVER:COUN 5;:TRAC:TYPE MINH") is None
        assert q(s, ":INIT:IMM") is None
        held = [float(v) for v in q(s, ":TRAC:DATA? TRACE1").split(",")]
        assert q(s, ":TRAC:TYPE WRIT;:INIT:IMM") is None
        write = [float(v) for v in q(s, ":TRAC:DATA? TRACE1").split(",")]
    finally:
        s.close()
    assert sum(held) / POINTS < sum(write) / POINTS - 3.0  # the minimum of 5 sweeps sits lower
    assert max(held) == pytest.approx(-10.0, abs=0.5)


def test_a_single_sweep_takes_the_manual_sweep_time():
    s = session()
    try:
        assert q(s, ":INIT:CONT OFF;:SWE:TIME 3000 s") is None
        t0 = s.now_ns
        assert q(s, ":INIT:IMM") is None
        assert (s.now_ns - t0) / 1e9 == pytest.approx(3000.0, abs=1e-6)
    finally:
        s.close()


def test_delta_marker_reads_and_sets_its_offset():
    s = session()
    try:
        assert q(s, ":FREQ:CENT 5.8 GHz;:FREQ:SPAN 1 MHz") is None
        assert q(s, ":CALC:MARK:MAX;:CALC:MARK:MODE DELT") is None
        assert q(s, ":CALC:MARK:X?") == "+0.00000000E+000"
        assert q(s, ":CALC:MARK:X -200 kHz") is None
        assert q(s, ":CALC:MARK:X?") == "-2.00000000E+005"
        assert float(q(s, ":CALC:MARK:Y?")) < -60.0  # noise 200 kHz below the carrier, in dB
        assert q(s, ":CALC:MARK:MODE POS") is None
        assert q(s, ":CALC:MARK:X?") == "+5.79980000E+009"
    finally:
        s.close()
