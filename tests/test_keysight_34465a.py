"""Keysight 34465A (issue 7): the manual's measurement flow against the engine.

The manual cases (tests/models/keysight-34465a/ and -impedance/) check command by command; these
tests run the manual's triggered-measurement example (examples/scripts/keysight_34465a_measure.py),
the calibration adjustment with a known hidden error, and the input impedance that loads the bench
circuit, against hand calculations (the solver's 1e-12 S leak to ground included).
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
import pyvisa

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-34465a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_34465a_measure.py"
GMIN = 1e-12  # engine/circuit.py stamps 1e-12 S from every node to ground


def _script():
    spec = importlib.util.spec_from_file_location("keysight_34465a_measure", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def solve(high_z: bool, emf: float = 1.0) -> float:
    """The divider's output with the meter loading it: the SDG (DC 1 V behind 50 ohm) feeds a
    900k/100k divider; the meter puts 10 Mohm (or >10 Gohm) and GMIN on the output node."""
    z_in = 10.0e9 if high_z else 10.0e6
    g = np.array(
        [
            [1 / 50 + 1 / 900e3 + GMIN, -1 / 900e3],
            [-1 / 900e3, 1 / 900e3 + 1 / 100e3 + 1 / z_in + GMIN],
        ]
    )
    return float(np.linalg.solve(g, np.array([emf / 50, 0.0]))[1])


STEP = {"0.1": 3e-8, "1": 3e-7, "10": 3e-6}  # NPLC 1: resolution fraction x range


def quantized(value: float, range_key: str = "0.1") -> float:
    return round(value / STEP[range_key]) * STEP[range_key]


# ---- the manual's triggered measurement, as a script written for a real 34465A --------------------


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["keysight_34465a_measure.py", "dut", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Keysight Technologies,34465A,MY57440001,")
    assert out[-1] == '+0,"No error"'
    # NPLC 10 (the *RST default): the resolution is 0.1 ppm of the auto-chosen 0.1 V range.
    want = round(solve(False) / 1e-8) * 1e-8
    readings = [float(x) for x in out[-2].split(": ")[1].split(" ... ")]
    assert len(readings) == 2 and readings[0] == readings[-1]
    assert readings[0] == pytest.approx(want, abs=1e-12)


# ---- the input impedance loads the bench circuit (hand calculation, GMIN included) -----------------


def test_high_z_changes_the_divided_reading():
    s = open_bench(BENCH)
    try:

        def q(cmd):
            s.write("dut", (cmd + "\n").encode())
            out = s.read("dut")
            return out.decode().strip() if out else None

        assert q("VOLT:DC:IMP:AUTO OFF") is None
        low = float(q("MEAS:VOLT:DC?").lstrip("+"))
        assert q("VOLT:DC:IMP:AUTO ON") is None
        high = float(q("MEAS:VOLT:DC?").lstrip("+"))
        assert low == pytest.approx(quantized(solve(False)), abs=1e-12)
        assert high == pytest.approx(quantized(solve(True)), abs=1e-12)
        assert high - low == pytest.approx(quantized(solve(True)) - quantized(solve(False)), abs=1e-12)
        assert high - low > 8.0e-4  # a ~0.9 % loading error the auto impedance removes
    finally:
        s.close()


def test_acv_of_a_sine_through_the_divider():
    s = open_bench(BENCH)
    try:

        def q(inst, cmd):
            s.write(inst, (cmd + "\n").encode())
            out = s.read(inst)
            return out.decode().strip() if out else None

        q("awg", "C1:BSWV WVTP,SINE,FRQ,1000,AMP,2,OFST,0")
        q("dut", 'FUNC "VOLT:AC"')
        q("dut", "VOLT:DC:RANG:AUTO ON")
        # 1 kHz is far above the 20 Hz filter; a resistive divider scales the peak like DC.
        want = quantized(solve(False) / np.sqrt(2))
        assert float(q("dut", "MEAS:VOLT:AC?").lstrip("+")) == pytest.approx(want, abs=1e-12)
        assert float(q("dut", "MEAS:VOLT:DC?").lstrip("+")) == pytest.approx(0.0, abs=1e-12)
    finally:
        s.close()


# ---- calibration: as-found with a known hidden error, as-left below 1 ppm ---------------------------


def cal_bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "cal34465a",
            "instruments": {
                "src": {"model": "sb-smu-1", "state": {"source.voltage.level": 0.0, "output.enabled": True}},
                "dmm": {
                    "model": "keysight-34465a",
                    "hidden": {"meter": {"gain_error_ppm": 150.0, "offset": 200e-6}},
                },
            },
            "wires": [{"from": "src.HI", "to": "dmm.IN"}],
            "run": {"clock": "command", "start": "2026-05-04T09:00:00Z"},
        }
    )


def test_calibration_flow_as_found_as_left():
    s = open_bench(cal_bench())
    try:

        def q(inst, cmd):
            s.write(inst, (cmd + "\n").encode())
            out = s.read(inst)
            return out.decode().strip() if out else None

        def level(v):
            q("src", f":SOUR:VOLT {v}")
            q("dmm", "MEAS:VOLT?")  # the input settles before the adjustment reads it

        assert q("dmm", "CAL:SEC:STAT?") == "1"
        for v in (0.0, 10.0):
            level(v)
            as_found = float(q("dmm", "MEAS:VOLT?").lstrip("+"))
            assert as_found == pytest.approx(v * (1 + 150e-6) + 200e-6, abs=1e-9)
        # The manual's DC voltage gain calibration: short on the input, then the gain value.
        q("dmm", 'CAL:SEC:STAT OFF,"AT3446XA"')
        level(0.0)
        q("dmm", "CAL:VAL 0")
        assert q("dmm", "CAL?") == "+0"
        level(10.0)
        q("dmm", "CAL:VAL 10")
        assert q("dmm", "CAL?") == "+0"
        q("dmm", "CAL:STOR")
        q("dmm", "CAL:SEC:STAT ON")
        assert q("dmm", "CAL:COUN?") == "1"
        assert q("dmm", "CAL:DATE?") == '"2026-05-04"'
        assert q("dmm", "CAL:TIME?") == '"09:00:00"'
        for v in (0.0, 10.0):
            level(v)
            as_left = float(q("dmm", "MEAS:VOLT?").lstrip("+"))
            assert as_left == pytest.approx(v, abs=1e-8)  # < 1 ppm of 10 V
    finally:
        s.close()


def test_adjustment_is_per_range_and_stays_secured():
    s = open_bench(cal_bench())
    try:

        def q(inst, cmd):
            s.write(inst, (cmd + "\n").encode())
            out = s.read(inst)
            return out.decode().strip() if out else None

        def level(v):
            q("src", f":SOUR:VOLT {v}")
            q("dmm", "MEAS:VOLT?")

        q("dmm", ":VOLT:DC:RANG 10;:VOLT:DC:RANG:AUTO OFF")
        level(10.0)
        assert float(q("dmm", "MEAS:VOLT?").lstrip("+")) == pytest.approx(
            10 * (1 + 150e-6) + 200e-6, abs=1e-9
        )
        q("dmm", "CAL:VAL 10")
        q("dmm", "CAL?")  # secured: refused without a reply
        assert q("dmm", ":SYST:ERR?").startswith("+701")
        q("dmm", 'CAL:SEC:STAT OFF,"AT3446XA"')
        level(10.0)
        q("dmm", "CAL:VAL 10")
        assert q("dmm", "CAL?") == "+0"
        q("dmm", "CAL:STOR")
        # The 1 V range has its own key: untouched until adjusted on it.
        q("dmm", ":VOLT:DC:RANG 1")
        level(1.0)
        assert float(q("dmm", "MEAS:VOLT?").lstrip("+")) == pytest.approx(1 * (1 + 150e-6) + 200e-6, abs=1e-9)
        q("dmm", "CAL:VAL 1")
        assert q("dmm", "CAL?") == "+0"
        assert float(q("dmm", "MEAS:VOLT?").lstrip("+")) == pytest.approx(1.0, abs=1e-9)
        # A wrong reference is caught, not applied.
        level(1.0)
        q("dmm", "CAL:VAL 2")
        assert q("dmm", "CAL?") == "+1"
        assert q("dmm", ":SYST:ERR?").startswith("+702")
    finally:
        s.close()


# ---- virtual time and individual deviations ---------------------------------------------------------


def test_reading_time_follows_nplc():
    b = BenchDefinition.model_validate({"name": "t", "instruments": {"dut": {"model": "keysight-34465a"}}})
    s = open_bench(b)
    try:

        def q(cmd):
            s.write("dut", (cmd + "\n").encode())
            out = s.read("dut")
            return out.decode().strip() if out else None

        for nplc, seconds in (("1", 0.04013), ("10", 0.40016), ("100", 4.00016)):
            q(f"VOLT:DC:NPLC {nplc}")
            t0 = s.now_ns
            q(":INIT;*WAI")
            assert s.now_ns - t0 == round(seconds * 1e9)
    finally:
        s.close()


def test_deviation_shifts_the_reading_within_the_data_sheet_limit():
    b = BenchDefinition.model_validate(
        {
            "name": "dev",
            "instruments": {
                "src": {"model": "sb-smu-1", "state": {"source.voltage.level": 10.0, "output.enabled": True}},
                "dut": {"model": "keysight-34465a", "deviation": "from_specs"},
            },
            "wires": [{"from": "src.HI", "to": "dut.IN"}],
            "run": {"seed": 11},
        }
    )
    s = open_bench(b)
    try:

        def q(cmd):
            s.write("dut", (cmd + "\n").encode())
            out = s.read("dut")
            return out.decode().strip() if out else None

        q(":VOLT:DC:RANG 10;:VOLT:DC:RANG:AUTO OFF")
        q("VOLT:DC:NPLC 10")
        reading = float(q("MEAS:VOLT:DC?").lstrip("+"))
        # 10 V range: gain within +-30 ppm of reading, offset within +-4 uV; 1 uV quantization.
        assert reading != pytest.approx(10.0, abs=1e-9)
        assert reading == pytest.approx(10.0, abs=30e-6 * 10 + 4e-6 + 1e-6)
    finally:
        s.close()
