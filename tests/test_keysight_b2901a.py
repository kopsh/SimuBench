"""Keysight B2901A (issue 193): the manual's source/measure/sweep flow against the engine.

The manual cases (tests/models/keysight-b2901a/) check command by command; these tests run the
real-instrument-style sweep script (examples/scripts/keysight_b2901a_sweep.py) on the example
bench, the compliance limit switch against a hand calculation, and the virtual time of a reading
and a staircase sweep. The script reads the measurement array with FETCh:ARRay? (not the trace
buffer, which needs TRACe:FEED:CONTrol NEXT on a real instrument).
"""

import math
import re
import statistics
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


def noise_sigma(range_: float, integration_s: float = READING_TIME) -> float:
    """The reading noise rms on ``range_`` (specs.noise_ppm_of_range: 7.5 ppm referred to a
    0.02 s one-PLC integration; readings scale with the square root of the integration time)."""
    return 7.5e-6 * range_ * math.sqrt(0.02 / integration_s)


def session():
    return open_bench(BENCH)


def q(s, command: str) -> str | None:
    return session_query(s, "smu", command)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["smu"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0].startswith("Agilent Technologies,B2901A,")
    # The spot reading rides the reading noise (specs.noise_ppm_of_range): 0 V on the auto-ranged
    # 0.2 V range, ~0 A on the 100 nA range.
    spot = re.fullmatch(r"spot: (\S+) V, (\S+) A", out[1])
    assert spot
    assert float(spot[1]) == pytest.approx(0.0, abs=4 * noise_sigma(0.2))
    assert float(spot[2]) == pytest.approx(0.0, abs=4 * noise_sigma(1.0e-7))
    assert out[2] == "5 points"
    points = [[float(x) for x in line.split(",")] for line in out[3:8]]
    # every point sits on the 2 V measure range; currents up to 2 mA on the 10 mA one
    assert [v for v, _ in points] == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0], abs=4 * noise_sigma(2.0))
    assert [i for _, i in points] == pytest.approx(
        [v / RESISTANCE for v in [0.0, 0.5, 1.0, 1.5, 2.0]], abs=4 * noise_sigma(1.0e-2)
    )
    assert out[-1] == '+0,"No error"'


def test_compliance_turns_a_5_v_source_into_1_ma_into_the_1_kohm_dut():
    s = session()
    try:
        assert q(s, ":SOUR:FUNC:MODE VOLT;:SOUR:VOLT 5") is None
        assert q(s, ":SENS:CURR:PROT 0.001") is None
        assert q(s, ":OUTP ON") is None
        # 1 mA through 1 kΩ: the terminal sits at 1 V and the current limit is in force.
        assert float(q(s, ":MEAS:VOLT?")) == pytest.approx(0.001 * RESISTANCE, abs=4 * noise_sigma(2.0))
        assert float(q(s, ":MEAS:CURR?")) == pytest.approx(0.001, abs=4 * noise_sigma(1.0e-3))
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
        assert float(q(s, ":MEAS:CURR?")) == pytest.approx(0.001, abs=4 * noise_sigma(1.0e-3))
        assert float(q(s, ":MEAS:VOLT?")) == pytest.approx(0.001 * RESISTANCE, abs=4 * noise_sigma(2.0))
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


NPLC10_TIME = 50e-6 + 3 * (10.0 / 50.0 + 185e-6) + 1.8e-3  # reading_time(10, 50, True)


def test_the_definition_noise_distributes_and_falls_with_nplc():
    """The built-in reading noise (specs.noise_ppm_of_range, #744): MEAS readings are independent
    draws around the solved value with σ = 7.5 ppm of the measure range at the NPLC's reading
    time; the same seed replays bit for bit, and 10 NPLC reads √10 tighter than 0.1 NPLC."""

    def setup(s, nplc: str) -> None:
        assert q(s, f":SENS:VOLT:NPLC {nplc}") is None
        assert q(s, ":SOUR:FUNC:MODE CURR;:SOUR:CURR 0.001;:SENS:VOLT:PROT 10;:OUTP ON") is None

    s = session()
    try:
        setup(s, "0.1")
        ones = [float(q(s, ":MEAS:VOLT?")) for _ in range(200)]  # 1 V on the auto 2 V range
        sigma = noise_sigma(2.0)
        assert len(set(ones)) > 4
        assert statistics.mean(ones) == pytest.approx(1.0, abs=4 * sigma / math.sqrt(200))
        assert statistics.stdev(ones) == pytest.approx(sigma, rel=0.2)
        setup(s, "10")
        tens = [float(q(s, ":MEAS:VOLT?")) for _ in range(200)]
        assert statistics.stdev(tens) == pytest.approx(noise_sigma(2.0, NPLC10_TIME), rel=0.3)
    finally:
        s.close()
    s = session()  # the bench seed is fixed: the same start replays the same draws
    try:
        setup(s, "0.1")
        again = [float(q(s, ":MEAS:VOLT?")) for _ in range(20)]
    finally:
        s.close()
    assert again == ones[:20]
