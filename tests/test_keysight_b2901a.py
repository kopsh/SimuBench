"""Keysight B2901A (issue 193): the manual's source/measure/sweep flow against the engine.

The manual cases (tests/models/keysight-b2901a/) check command by command; these tests run the
real-instrument-style sweep script (examples/scripts/keysight_b2901a_sweep.py) on the example
bench, the compliance limit switch against a hand calculation, and the virtual time of a reading
and a staircase sweep. The script reads the measurement array with FETCh:ARRay? (not the trace
buffer, which needs TRACe:FEED:CONTrol NEXT on a real instrument).
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_b2901a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_b2901a_sweep.py"
RESISTANCE = 1000.0
READING_TIME = 0.008405  # NPLC 0.1 at 50 Hz with autozero: reading_time(0.1, 50, True)


def _script():
    spec = importlib.util.spec_from_file_location("keysight_b2901a_sweep", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session():
    return open_bench(BENCH)


def q(s, command: str) -> str | None:
    s.write("smu", (command + "\n").encode())
    out = s.read("smu")
    return out.decode().strip() if out else None


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["keysight_b2901a_sweep.py", "smu", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
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
