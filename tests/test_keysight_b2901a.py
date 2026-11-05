"""Keysight B2901A (issue 193): the manual's source/measure/sweep flow against the engine.

The manual cases (tests/models/keysight-b2901a/) check command by command; these tests run the
real-instrument-style sweep script (examples/scripts/keysight_b2901a_sweep.py) on the example
bench, the compliance limit switch against a hand calculation, and the virtual time of a reading
and a staircase sweep. The script reads the measurement array with FETCh:ARRay? (not the trace
buffer, which needs TRACe:FEED:CONTrol NEXT on a real instrument).
"""

from pathlib import Path

import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_b2901a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_b2901a_sweep.py"
RESISTANCE = 1000.0
READING_TIME = 0.008405  # NPLC 0.1 at 50 Hz with autozero: reading_time(0.1, 50, True)

script = load_script(SCRIPT)


def session():
    return open_bench(BENCH)


def q(s, command: str) -> str | None:
    return session_query(s, "smu", command)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["smu"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0].startswith("Agilent Technologies,B2901A,")
    assert out[1].startswith("spot: 0 V, 0 A")
    assert out[2] == "5 points"
    points = [[float(x) for x in line.split(",")] for line in out[3:8]]
    assert [v for v, _ in points] == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0])
    assert [i for _, i in points] == pytest.approx([v / RESISTANCE for v in [0.0, 0.5, 1.0, 1.5, 2.0]])
    assert out[-1] == '+0,"No error"'


def test_compliance_turns_a_5_v_source_into_1_ma_into_the_1_kohm_dut():
    s = session()
    try:
        assert q(s, ":SOUR:FUNC:MODE VOLT;:SOUR:VOLT 5") is None
        assert q(s, ":SENS:CURR:PROT 0.001") is None
        assert q(s, ":OUTP ON") is None
        # 1 mA through 1 kΩ: the terminal sits at 1 V and the current limit is in force.
        assert float(q(s, ":MEAS:VOLT?")) == pytest.approx(0.001 * RESISTANCE, abs=1e-9)
        assert float(q(s, ":MEAS:CURR?")) == pytest.approx(0.001, abs=1e-12)
        assert q(s, ":SENS:CURR:PROT:TRIP?") == "1"
        assert q(s, ":SENS:VOLT:PROT:TRIP?") == "0"
    finally:
        s.close()


def test_current_source_into_the_dut_is_1_v_at_1_ma():
    s = session()
    try:
        assert q(s, ":SOUR:FUNC:MODE CURR;:SOUR:CURR 0.001") is None
        assert q(s, ":SENS:VOLT:PROT 10") is None
        assert q(s, ":OUTP ON") is None
        assert float(q(s, ":MEAS:CURR?")) == pytest.approx(0.001, abs=1e-12)
        assert float(q(s, ":MEAS:VOLT?")) == pytest.approx(0.001 * RESISTANCE, abs=1e-9)
        assert q(s, ":SENS:VOLT:PROT:TRIP?") == "0"
    finally:
        s.close()


def test_reading_time_and_sweep_time_follow_nplc():
    s = session()
    try:
        assert q(s, ":SENS:VOLT:NPLC 0.1") is None
        t0 = s.now_ns
        assert q(s, ":INIT;*WAI") is None
        assert (s.now_ns - t0) / 1e9 == pytest.approx(READING_TIME, abs=1e-9)

        assert q(s, ":SOUR:FUNC:MODE VOLT;:SENS:CURR:PROT 0.01;:OUTP ON") is None
        assert q(s, ":SOUR:VOLT:MODE SWE;:SOUR:VOLT:STAR 0;:SOUR:VOLT:STOP 1;:SOUR:VOLT:POIN 5") is None
        t0 = s.now_ns
        assert q(s, ":INIT;*WAI") is None
        assert (s.now_ns - t0) / 1e9 == pytest.approx(5 * READING_TIME, abs=1e-9)
    finally:
        s.close()


def test_source_ranges_and_autoranges_are_kept_per_function():
    """#253: voltage and current source ranges are independent settings (and autoranges too)."""
    s = session()
    try:
        q(s, ":SOUR:VOLT:RANG 20")
        q(s, ":SOUR:CURR:RANG 1E-3")
        source = s.instruments["smu"].components["source"]
        assert source.source_range("VOLTage", 1.0) == 20.0
        assert source.source_range("CURRent", 1.0e-3) == 1.0e-3
        assert q(s, ":SOUR:VOLT:RANG?") == "+2.000000E+01"
        assert q(s, ":SOUR:CURR:RANG?") == "+1.000000E-03"
        q(s, ":SOUR:CURR:RANG:AUTO ON")
        assert q(s, ":SOUR:CURR:RANG:AUTO?") == "1"
        assert q(s, ":SOUR:VOLT:RANG:AUTO?") == "0"  # the current autorange did not move the voltage one
    finally:
        s.close()
