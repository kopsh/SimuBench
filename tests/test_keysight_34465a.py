"""Keysight 34465A (issue 7): the manual's measurement flow against the engine.

The manual cases (tests/models/keysight-34465a/ and -impedance/) check command by command; these
tests run the manual's triggered-measurement example (examples/scripts/keysight_34465a_measure.py),
the calibration adjustment with a known hidden error, and the input impedance that loads the bench
circuit, against hand calculations (the solver's 1e-12 S leak to ground included).

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original test's
body verbatim; acts that are provably state-compatible share one bench, with the bridging
commands noted.
"""

from pathlib import Path

import numpy as np
import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-34465a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_34465a_measure.py"
GMIN = 1e-12  # engine/circuit.py stamps 1e-12 S from every node to ground


script = load_script(SCRIPT)


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
    out = run_manual_script(script, ["dut"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0].startswith("Keysight Technologies,34465A,MY57440001,")
    assert out[-1] == '+0,"No error"'
    # CONF:VOLT:DC 10 selects the 10 V range; the readings quantize by the NPLC table (1e-6 at
    # NPLC 10) — the CONF resolution argument is reflected in CONF? only (#286), not in the
    # quantization (the model boundary the coverage table notes).
    want = round(solve(False) / 1e-6) * 1e-6
    readings = [float(x) for x in out[-2].split(": ")[1].split(" ... ")]
    assert len(readings) == 2 and readings[0] == readings[-1]
    assert readings[0] == pytest.approx(want, abs=1e-12)


# ---- the input impedance loads the bench circuit, then the ACV of a sine: one flow -----------------
# (hand calculation, GMIN included)


def test_the_input_impedance_and_acv_flow():
    """The auto impedance removes the ~0.9 % loading error from the divided DC reading; a 1 kHz
    sine scales through the resistive divider like DC and reads no DC."""
    s = open_bench(BENCH)
    try:

        def q(inst, cmd):
            s.write(inst, (cmd + "\n").encode())
            out = s.read(inst)
            return out.decode().strip() if out else None

        # -- high-Z changes the divided reading (was test_high_z_changes_the_divided_reading)
        assert q("dut", "VOLT:DC:IMP:AUTO OFF") is None
        low = float(q("dut", "MEAS:VOLT:DC?").lstrip("+"))
        assert q("dut", "VOLT:DC:IMP:AUTO ON") is None
        high = float(q("dut", "MEAS:VOLT:DC?").lstrip("+"))
        assert low == pytest.approx(quantized(solve(False)), abs=1e-12)
        assert high == pytest.approx(quantized(solve(True)), abs=1e-12)
        assert high - low == pytest.approx(quantized(solve(True)) - quantized(solve(False)), abs=1e-12)
        assert high - low > 8.0e-4  # a ~0.9 % loading error the auto impedance removes
        # -- bridge: the ACV act's expected loading is the 10 MΩ the fresh bench starts with
        q("dut", "VOLT:DC:IMP:AUTO OFF")
        # -- the ACV of a sine through the divider (was test_acv_of_a_sine_through_the_divider)
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
                    "hidden": {"path": {"gain_error_ppm": 150.0, "offset": 200e-6}},
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


def test_the_calibration_set_form_adjusts_without_answering():
    """#9: a set operation never answers; ``CAL`` adjusts silently, ``CAL?`` reports the result."""
    s = open_bench(cal_bench())
    try:

        def q(inst, cmd):
            s.write(inst, (cmd + "\n").encode())
            out = s.read(inst)
            return out.decode().strip() if out else None

        def level(v):
            q("src", f":SOUR:VOLT {v}")

        q("dmm", "*RST")
        level(0.0)
        q("dmm", 'CAL:SEC:STAT OFF,"AT3446XA"')
        s.write("dmm", b":VOLT:DC:RANG 1;:VOLT:DC:RANG:AUTO OFF\n")
        s.write("dmm", b":CAL:VAL 0\n")
        assert s.read("dmm") is None  # an operation's set form does not answer
        s.write("dmm", b":CAL\n")
        assert s.read("dmm") is None
        level(1.0)
        s.write("dmm", b":CAL:VAL 1\n")
        s.write("dmm", b":CAL\n")
        s.write("dmm", b":CAL:STOR\n")
        reading = float(q("dmm", ":MEASure:VOLTage:DC?").lstrip("+"))
        assert reading == pytest.approx(1.0, abs=1e-9)  # adjusted on the 1 V range
        # The query form still reports the adjustment result.
        s.write("dmm", b":CAL:VAL 1\n")
        assert q("dmm", ":CAL?") == "+0"
    finally:
        s.close()


# ---- resistance and current from the circuit (#301) ---------------------------------------------------


def _bench(parts: dict, wires: list) -> "BenchDefinition":
    from simubench.models.bench import BenchDefinition

    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "dmm-301",
            "instruments": {"dmm": {"model": "keysight-34465a"}},
            "parts": parts,
            "wires": wires,
            "run": {"clock": "command", "idle": "off"},
        }
    )


def _q(s, cmd: str) -> str:
    return session_query(s, "dmm", cmd, default="")


def test_two_wire_reads_the_leads_four_wire_does_not():
    """A 1 kΩ DUT behind a 10 Ω lead: 2-wire reads 1010 Ω; 4-wire senses at the DUT, 1000 Ω."""
    from simubench.api import open_bench

    bench = _bench(
        {
            "lead": {"type": "resistor", "params": {"r": 10.0}},
            "dut": {"type": "load_resistor", "params": {"r": 1000.0}},
        },
        [
            {"from": "dmm.IN", "to": "lead.A"},
            {"from": "lead.B", "to": "dut.A"},
            {"from": "dmm.SENSE", "to": "dut.A"},
        ],
    )
    s = open_bench(bench)
    try:
        two = float(_q(s, "MEAS:RES?"))
        four = float(_q(s, "MEAS:FRES?"))
        assert _q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()
    assert four == pytest.approx(two - 10.0, abs=0.5)


# ---- ohms overload and the settled auto range: one flow (#301, #522) -------------------------------


def test_the_ohms_overload_and_autorange_flow():
    """A too-small range reads past its overrange and an empty input reads overload (on a bench
    of its own); READ? and INIT/FETCh? take the reading after the auto range has settled, as
    MEASure? does (#522: each ohms range drives its own test current, so the first guess can
    read open)."""
    s = open_bench(
        _bench({"r1": {"type": "load_resistor", "params": {"r": 4700.0}}}, [{"from": "dmm.IN", "to": "r1.A"}])
    )
    try:
        # -- a too-small range reads overload (was the first half of
        # test_an_open_input_and_a_too_small_range_read_overload)
        assert float(_q(s, "MEAS:RES?")) == pytest.approx(4700.0, rel=1e-6)
        _q(s, "CONF:RES 1000")  # 4.7 kΩ on the 1 kΩ range: past its overrange
        assert float(_q(s, "READ?")) == pytest.approx(9.9e37)
        assert _q(s, "CONF?") == '"RES +1.00000000E+03,+1.00000000E-04"'
        # -- READ? settles the ohms auto range like MEASure? (was
        # test_read_settles_the_ohms_autorange_like_measure; the fixed range above is replaced
        # by the act's own CONF:RES 100, then autorange)
        _q(s, "CONF:RES 100")
        _q(s, "CONF:RES")  # back to autorange from a fixed range
        assert float(_q(s, "READ?")) == pytest.approx(4700.0, rel=1e-6)
        _q(s, "INIT")
        assert float(_q(s, "FETC?")) == pytest.approx(4700.0, rel=1e-6)
        assert _q(s, "CONF?") == '"RES +1.00000000E+04,+1.00000000E-03"'
    finally:
        s.close()
    # -- an empty input reads overload: a bench of its own (was the second half of
    # test_an_open_input_and_a_too_small_range_read_overload)
    s = open_bench(_bench({}, []))
    try:
        assert float(_q(s, "MEAS:RES?")) == pytest.approx(9.9e37)  # nothing on the input
    finally:
        s.close()


def test_current_flows_through_the_shunt_into_the_circuit():
    """5 V behind 1 kΩ into the current input: the shunt (10 Ω on the 10 mA range) is in the loop."""
    from simubench.api import open_bench

    s = open_bench(
        _bench(
            {"src": {"type": "dc_supply", "params": {"v": 5.0, "r": 1000.0}}},
            [{"from": "src.OUT", "to": "dmm.IN"}],
        )
    )
    try:
        _q(s, "CONF:CURR 0.01")
        i = float(_q(s, "READ?"))
        assert i == pytest.approx(5.0 / (1000.0 + 10.0), rel=1e-6)
        assert _q(s, "FUNC?") == '"CURR"'
    finally:
        s.close()
