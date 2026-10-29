"""Fluke 5522A (issue 8): the manual's behaviors against the engine.

The manual cases (tests/models/fluke-5522a/) check command by command; these tests run the
bench-level behaviors: the validation bench (validation/benches/fluke-5522a-dmm.bench.yaml)
proving that OPER puts the setting on the terminals within the drawn deviations and STBY reads
0 V, the example bench (examples/benches/fluke-5522a.bench.yaml) where DC and 1 kHz AC are
visible on the scope, the 33 V confirmation, the overload trip to standby (error 1503), and the
settling time on the virtual clock.
"""

from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
DMM_BENCH = ROOT / "validation" / "benches" / "fluke-5522a-dmm.bench.yaml"
SCOPE_BENCH = ROOT / "examples" / "benches" / "fluke-5522a.bench.yaml"
#: The drawn deviations are bounded by the tightest 1-year corners of the specs (gain 20 ppm,
#: floor 1 uV); the meter's own hidden errors are zeroed in the bench.
GAIN = 20.0e-6
FLOOR = 1.0e-6
QUANTUM = 1.0e-7  # sb-dmm-1 answers with a 7-digit mantissa


def q(s, inst, cmd):
    s.write(inst, (cmd + "\n").encode())
    out = s.read(inst)
    return out.decode().strip() if out else None


def reading(s, what="DC"):
    reply = q(s, "dmm", f":MEAS:VOLT:{what}?")
    assert reply, reply
    return float(reply)


def loaded_bench(load_ohm: float) -> BenchDefinition:
    data = BenchDefinition.model_validate({"name": "t", "instruments": {"dut": {"model": "fluke-5522a"}}})
    raw = data.model_dump(by_alias=True)
    raw["parts"] = {"load": {"type": "load_resistor", "params": {"r": load_ohm}}}
    raw["wires"] = [{"from": "dut.OUT", "to": "load.A"}]
    raw["run"] = {"clock": "command"}
    return BenchDefinition.model_validate(raw)


# ---- the validation bench: OPER reads the setting, STBY reads 0 V ---------------------------------


def test_operate_puts_the_setting_on_the_terminals_and_standby_reads_zero():
    s = open_bench(DMM_BENCH)
    try:
        assert reading(s) == 0.0  # power-up: standby, the terminal is out of the circuit
        q(s, "cal", "OUT 10 V")
        assert reading(s) == 0.0  # the setting alone drives nothing
        q(s, "cal", "OPER")
        assert q(s, "cal", "*OPC?") == "1"
        settled = reading(s)
        assert settled == pytest.approx(10.0, abs=GAIN * 10.0 + FLOOR + QUANTUM)
        q(s, "cal", "STBY")
        assert reading(s) == 0.0
        q(s, "cal", "OPER")  # entering operate again settles from 0
        q(s, "cal", "*WAI")
        assert reading(s) == pytest.approx(settled, abs=QUANTUM)
    finally:
        s.close()


def test_acv_is_a_line_at_the_programmed_frequency():
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 1 V, 1 kHz")
        q(s, "cal", "OPER;*WAI")
        assert reading(s, "AC") == pytest.approx(1.0, abs=GAIN * 1.0 + FLOOR + QUANTUM)
        assert reading(s, "DC") == 0.0  # one line of 1 V RMS has no DC component
        q(s, "cal", "OUT 2 V, 0 HZ")  # 0 Hz is DC
        q(s, "cal", "*WAI")
        assert reading(s, "DC") == pytest.approx(2.0, abs=GAIN * 2.0 + FLOOR + QUANTUM)
    finally:
        s.close()


def test_the_drawn_deviations_are_the_bench_seed_s_own():
    """deviation: from_specs draws once per bench: the same seed gives the same unit."""
    first = open_bench(DMM_BENCH)
    try:
        q(first, "cal", "OUT 10 V")
        q(first, "cal", "OPER;*WAI")
        want = reading(first)
        assert want != 10.0  # the draw moved the output off the nominal
    finally:
        first.close()
    second = open_bench(DMM_BENCH)
    try:
        q(second, "cal", "OUT 10 V")
        q(second, "cal", "OPER;*WAI")
        assert reading(second) == pytest.approx(want, abs=QUANTUM)
    finally:
        second.close()


# ---- the 33 V confirmation --------------------------------------------------------------------------


def test_a_change_across_33v_returns_to_standby():
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 10 V")
        q(s, "cal", "OPER;*WAI")
        assert reading(s) == pytest.approx(10.0, abs=2e-4)
        q(s, "cal", "OUT 40 V")  # the new setting is >= 33 V: back to standby
        assert reading(s) == 0.0
        assert q(s, "cal", "ERR?") == '0,"No Error"'  # the confirmation is not an error
        q(s, "cal", "OPER;*WAI")  # the explicit confirmation
        assert reading(s) == pytest.approx(40.0, abs=GAIN * 40.0 + FLOOR + QUANTUM)
        q(s, "cal", "OUT 20 V")  # the old setting was above 33 V: standby even though 20 V is safe
        assert reading(s) == 0.0
        q(s, "cal", "OPER;*WAI")
        assert reading(s) == pytest.approx(20.0, abs=GAIN * 20.0 + FLOOR + QUANTUM)
    finally:
        s.close()


def test_a_function_change_at_high_voltage_returns_to_standby():
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 40 V")
        q(s, "cal", "OPER;*WAI")
        assert reading(s) == pytest.approx(40.0, abs=GAIN * 40.0 + FLOOR + QUANTUM)
        q(s, "cal", "OUT 40 V, 1 kHz")  # the function changes while the output is >= 33 V
        assert reading(s) == 0.0
        q(s, "cal", "OPER;*WAI")
        assert reading(s, "AC") == pytest.approx(40.0, abs=GAIN * 40.0 + FLOOR + QUANTUM)
    finally:
        s.close()


# ---- overload ---------------------------------------------------------------------------------------


def test_overload_trip_behavior():
    s = open_bench(loaded_bench(10.0))
    try:
        q(s, "dut", "OUT 1 V")
        q(s, "dut", "OPER")
        s.advance(5.0)
        out = s.instruments["dut"].components["out"]
        assert out.readings()["values"]["tripped"] is True
        assert out.readings()["values"]["output"] == "STBY"
        assert q(s, "dut", "ERR?") == '1503,"Output current lim exceeded"'
        assert q(s, "dut", "ERR?") == '0,"No Error"'  # the entry was read
        for _ in (1, 2):
            q(s, "dut", "OPER")
            s.advance(5.0)  # the trip repeats: the load is still too heavy
            assert out.readings()["values"]["tripped"] is True
            assert q(s, "dut", "ERR?") == '1503,"Output current lim exceeded"'
    finally:
        s.close()


def test_a_load_within_the_capability_does_not_trip():
    s = open_bench(loaded_bench(10000.0))  # 0.1 mA
    try:
        q(s, "dut", "OUT 1 V")
        q(s, "dut", "OPER;*WAI")
        v = s.instruments["dut"].components["out"].readings()["values"]["voltage"]
        assert v == pytest.approx(1.0, abs=1e-6)
        assert q(s, "dut", "ERR?") == '0,"No Error"'
    finally:
        s.close()


# ---- settling ---------------------------------------------------------------------------------------


def test_settling_takes_five_seconds_on_the_virtual_clock():
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 10 V")
        t0 = s.now_ns
        q(s, "cal", "OPER")
        assert s.now_ns == t0  # OPER itself returns at once
        assert q(s, "cal", "*OPC?") == "1"
        assert s.now_ns - t0 == round(5.0 * 1e9)  # the manual: settling < 5 s, modeled as 5 s
    finally:
        s.close()


def test_opc_sets_esr_bit_zero_once_settled():
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 10 V")
        q(s, "cal", "OPER")
        q(s, "cal", "*OPC")
        assert q(s, "cal", "*ESR?") == "0"  # the output has not settled yet
        s.advance(5.0)
        assert q(s, "cal", "*ESR?") == "1"
    finally:
        s.close()


# ---- the example bench: DC and 1 kHz AC visible on the scope -----------------------------------------


def test_the_example_bench_shows_dc_and_1khz_ac_on_the_scope():
    s = open_bench(SCOPE_BENCH)
    try:
        q(s, "dut", ":MEASure ON;:MEASure:MODE SIMPle;:MEASure:SIMPle:SOURce C1")
        q(s, "cal", "OUT 10 V")
        q(s, "cal", "OPER;*OPC?")
        # 8-bit ADC, 30 codes/div at 2.5 V/div: 10 V is 120 codes, on scale.
        assert q(s, "dut", "MEAS:SIMP:VAL? MEAN") == "1.00E+01"
        assert q(s, "dut", "MEAS:SIMP:VAL? MAX") == "1.00E+01"
        q(s, "cal", "STBY")
        q(s, "cal", "OUT 1 V, 1 kHz")
        q(s, "cal", "OPER;*OPC?")
        # 1 V RMS: 2.83 Vpp; the quantized peaks read 17 codes = 1.4167 V.
        assert q(s, "dut", "MEAS:SIMP:VAL? PKPK") == "2.83E+00"
        assert q(s, "dut", "MEAS:SIMP:VAL? FREQ") == "1.00E+03"
        assert q(s, "dut", "MEAS:SIMP:VAL? RMS") == "1.00E+00"
        assert float(q(s, "dut", "MEAS:SIMP:VAL? MEAN")) == pytest.approx(0.0, abs=1e-12)
    finally:
        s.close()


# ---- identity and error queue ------------------------------------------------------------------------


def test_identity_and_restricted_builtins():
    s = open_bench(DMM_BENCH)
    try:
        assert q(s, "cal", "*IDN?") == "FLUKE,5522A,5248101,1.0+1.3+1.8"
        assert q(s, "cal", "*OPT?") == "0"  # no options installed on this unit
        # The 5522A has no SCPI :SYSTem: headers: the error queue is ERR?, the clock is CLOCK.
        q(s, "cal", "OUT 10")
        assert q(s, "cal", "ERR?") == '519,"Must specify an output unit"'
        assert q(s, "cal", ":SYST:ERR?") is None
        assert q(s, "cal", "ERR?") == '-113,"Undefined header;not modeled by SimuBench"'
        assert q(s, "cal", "*TRG") is None  # TC measurement on the real instrument; not modeled
        assert q(s, "cal", "ERR?") == '-113,"Undefined header;not modeled by SimuBench"'
    finally:
        s.close()


def test_the_output_floats_above_the_threshold_boundary():
    """33.0 V itself does not return to standby (the component trips strictly above; the manual
    says a voltage >= 33 V is selected) -- a documented boundary deviation."""
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 32.9 V")
        q(s, "cal", "OPER;*WAI")
        q(s, "cal", "OUT 33 V;*WAI")  # exactly 33 V: no trip
        assert reading(s) == pytest.approx(33.0, abs=GAIN * 33.0 + FLOOR + QUANTUM)
        q(s, "cal", "OUT 33.1 V")  # above 33 V: standby
        assert reading(s) == 0.0
    finally:
        s.close()
