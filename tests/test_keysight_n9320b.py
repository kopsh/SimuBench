"""Keysight N9320B (issue 196): the Programmer's Guide flow on the example bench.

The manual cases (tests/models/keysight-n9320b/) check command by command; these tests run the
real-instrument-style script (examples/scripts/keysight_n9320b_measure.py) unchanged on the
example bench, the automatic RBW/VBW/sweep-time couplings, the trace (1001 points, deterministic
at one virtual instant) and the virtual time of a single sweep.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_n9320b.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_n9320b_measure.py"
POINTS = 1001
# 2 Vpp with the HiZ load setting into the 50 ohm input: 0.5 V peak -> +3.979 dBm.
LEVEL_DBM = 3.9793921


def _script():
    spec = importlib.util.spec_from_file_location("keysight_n9320b_measure", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session():
    # idle='off' (D47): the bench's default wall idle lets real gaps between commands advance the
    # virtual time, so the sweep a read lands in — and with it the noise a point carries —
    # depends on machine speed; two sessions then disagree on a slow runner (#455).
    return open_bench(BENCH, idle="off")


def q(s, command: str) -> str | None:
    s.write("sa", (command + "\n").encode())
    out = s.read("sa")
    return out.decode().strip() if out else None


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["keysight_n9320b_measure.py", "sa", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Keysight Technologies,N9320B,A.01.01.CIDKMNEW,0B.03.03"
    assert out[1] == "marker: 10000000 Hz, 3.979 dBm"
    assert out[2] == "counter: 10000000 Hz"
    assert out[3] == f"trace: {POINTS} points, peak 3.979 dBm"
    assert out[4] == '+0,"No error"'


def test_automatic_rbw_vbw_and_sweep_time_couple_to_the_span():
    s = session()
    try:
        # Bench state: 10 MHz center, 2 MHz span. Auto RBW = span/100 snapped to the 1-3-10
        # ladder (30 kHz), VBW follows RBW, sweep time = 2.5 span / (RBW min(RBW, VBW)).
        assert float(q(s, ":BAND?")) == pytest.approx(3.0e4, rel=1e-9)
        assert float(q(s, ":BAND:VID?")) == pytest.approx(3.0e4, rel=1e-9)
        assert float(q(s, ":SWE:TIME?")) == pytest.approx(2.5 * 2.0e6 / (3.0e4**2), rel=1e-9)
        assert q(s, ":BAND:AUTO?") == "1"
        assert q(s, ":BAND:VID:AUTO?") == "1"
        assert q(s, ":SWE:TIME:AUTO?") == "1"

        assert q(s, ":BAND 100 kHz") is None
        assert q(s, ":BAND?") == "+1.0000000000000000E+005"  # the guide's one format (#366)
        assert q(s, ":BAND:AUTO?") == "0"
        assert float(q(s, ":BAND:VID?")) == pytest.approx(1.0e5, rel=1e-9)
        assert q(s, ":BAND:VID:AUTO?") == "1"
        assert float(q(s, ":SWE:TIME?")) == pytest.approx(max(1.0e-3, 2.5 * 2.0e6 / (1.0e5**2)), rel=1e-9)

        # Automatic attenuation holds the reference level at the -20 dBm mixer limit in 2 dB steps:
        # 20 dB at the 0 dBm reference level, the manual's *RST value (#292).
        assert float(q(s, ":POW:ATT?")) == pytest.approx(20.0, abs=1e-9)
        assert q(s, ":POW:ATT:AUTO?") == "1"
    finally:
        s.close()


def test_marker_peak_search_reads_the_10_mhz_tone():
    s = session()
    try:
        assert q(s, ":CALC:MARK:PEAK:THR -30") is None
        assert q(s, ":CALC:MARK:MAX") is None
        assert float(q(s, ":CALC:MARK:X?")) == pytest.approx(10.0e6, abs=2.0e3)
        assert float(q(s, ":CALC:MARK:Y?")) == pytest.approx(LEVEL_DBM, abs=0.01)
        assert float(q(s, ":CALC:MARK:FCO:X?")) == 0.0  # the counter is off after *RST (#274)
        assert q(s, ":CALC:MARK:FCO ON") is None
        assert float(q(s, ":CALC:MARK:FCO:X?")) == pytest.approx(10.0e6, abs=1.0)
        assert q(s, ":SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_trace_has_1001_points_and_two_reads_at_one_instant_agree():
    s = session()
    try:
        first = q(s, ":TRAC:DATA? TRACE1")
        second = q(s, ":TRAC:DATA? TRACE1")
    finally:
        s.close()
    assert first == second
    values = [float(x) for x in first.split(",")]
    assert len(values) == POINTS
    assert max(values) == pytest.approx(LEVEL_DBM, abs=0.01)
    assert values.index(max(values)) == (POINTS - 1) // 2  # the 10 MHz tone is at the center


def test_a_single_sweep_takes_the_manual_sweep_time():
    s = session()
    try:
        assert q(s, ":INIT:CONT OFF") is None
        assert q(s, ":SWE:TIME 50 ms") is None
        assert q(s, ":SWE:TIME:AUTO?") == "0"
        t0 = s.now_ns
        assert q(s, ":INIT:IMM") is None
        assert (s.now_ns - t0) / 1e9 == pytest.approx(0.05, abs=1e-9)
        assert q(s, ":INIT:CONT?") == "0"
    finally:
        s.close()


def test_delta_marker_starts_at_zero_when_the_marker_was_off():
    """Entering DELTa with the marker off turns the marker on first (screen center) and fixes
    the reference there: Y? and X:DELT? start at 0, and the same sequence reads identically in
    two sessions (issue #349: the reference used to be fixed at the stale off-marker position)."""
    readings = []
    for _ in range(2):
        s = session()
        try:
            readings.append(
                [
                    q(s, ":CALC:MARK:MODE DELT"),
                    q(s, ":CALC:MARK:Y?"),
                    q(s, ":CALC:MARK:X:DELT?"),
                    q(s, ":CALC:MARK:X 11 MHz"),
                    q(s, ":CALC:MARK:Y?"),
                ]
            )
        finally:
            s.close()
    first, second = readings
    assert first == second  # deterministic across sessions
    assert first[0] is None and first[3] is None  # the writes answer nothing
    assert first[1] == first[2] == "+0.0000000000000000E+000"
    assert float(first[4]) < -100  # off the 10 MHz reference, at the 11 MHz noise floor
