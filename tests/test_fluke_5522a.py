"""Fluke 5522A (issue 8): the manual's behaviors against the engine.

The manual cases (tests/models/fluke-5522a/) check command by command; these tests run the
bench-level behaviors: the validation bench (validation/benches/fluke-5522a-dmm.bench.yaml)
proving that OPER puts the setting on the terminals within the drawn deviations and STBY reads
0 V, the example bench (examples/benches/fluke-5522a.bench.yaml) where DC and 1 kHz AC are
visible on the scope, the 33 V confirmation, the overload trip to standby (error 1503), and the
settling time on the virtual clock.

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original test's
body verbatim; acts that are provably state-compatible share one bench, with the bridging
commands noted.
"""

import math
import statistics
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
#: The readback noise the definition carries (specs.readback_noise_rms, #744/#809): the tightest
#: 1-year floor's 1/20 (DC 330 mV range, floor 1 uV), absolute rms on the panel's voltage readback.
READBACK_SIGMA = 5.0e-8


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


# ---- the validation bench: operate/standby and the functions: one flow ------------------------------


def test_the_operate_standby_and_function_flow():
    """Standby reads 0 V, the setting alone drives nothing, OPER settles to the value; the AC
    line sits at its programmed frequency with no DC, and 0 Hz is DC again."""
    s = open_bench(DMM_BENCH)
    try:
        # -- operate puts the setting on the terminals, standby reads zero (was
        # test_operate_puts_the_setting_on_the_terminals_and_standby_reads_zero)
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
        # -- an ACV line at the programmed frequency (was test_acv_is_a_line_at_the_programmed_frequency;
        # the act's own OUT line changes the function, and below 33 V the switch keeps operating)
        q(s, "cal", "OUT 1 V, 1 kHz")
        q(s, "cal", "OPER;*WAI")
        assert reading(s, "AC") == pytest.approx(1.0, abs=GAIN * 1.0 + FLOOR + QUANTUM)
        assert reading(s, "DC") == 0.0  # one line of 1 V RMS has no DC component
        # 0 Hz is DC: below 33 V the switch keeps operating (ch. 4: only >= 33 V goes to standby, #566)
        q(s, "cal", "OUT 2 V, 0 HZ;*WAI")
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


def test_the_definition_noise_distributes_and_replays():
    """The readback noise (specs.readback_noise_rms, #744/#809): the calibrator's own monitor
    reads the settled output with an independent draw of σ = 50 nV per panel frame, the same seed
    replays bit for bit — and the terminal itself stays exactly 0 in standby and carries none of
    it in operate, so a meter reading the output is untroubled."""
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 1 V")
        out = s.instrument("cal").components["out"]
        assert s.instrument("cal").session.port_signal(out, "out").mean() == 0.0  # standby: exact
        q(s, "cal", "OPER;*WAI")
        true_v = s.instrument("cal").session.port_signal(out, "out").mean()  # the driven output
        frames = [out.readings()["values"]["voltage"] for _ in range(200)]
        assert len(set(frames)) > 100  # every frame draws
        assert statistics.mean(frames) == pytest.approx(true_v, abs=4 * READBACK_SIGMA / math.sqrt(200))
        assert statistics.stdev(frames) == pytest.approx(READBACK_SIGMA, rel=0.2)
        first20 = frames[:20]
    finally:
        s.close()
    s = open_bench(DMM_BENCH)
    try:
        q(s, "cal", "OUT 1 V")
        q(s, "cal", "OPER;*WAI")
        out = s.instrument("cal").components["out"]
        again = [out.readings()["values"]["voltage"] for _ in range(20)]
        assert again == first20  # the same seed replays bit for bit
    finally:
        s.close()


# ---- the 33 V confirmation: one flow ----------------------------------------------------------------


def test_the_33v_confirmation_flow():
    """Operators Manual ch. 3/4, Operate and Standby Modes: “A voltage >= 33 V is selected when
    the previous output voltage was less than 33 V” — only a crossing upward trips (#558), a
    function change at high voltage trips too, 33.0 V itself is on the standby side, and within
    the high range or downward the output keeps running."""
    s = open_bench(DMM_BENCH)
    try:
        # -- a change across 33 V returns to standby (was test_a_change_across_33v_returns_to_standby)
        # Operators Manual ch. 3/4, Operate and Standby Modes: “A voltage >= 33 V is selected when
        # the previous output voltage was less than 33 V” — only a crossing upward trips (#558).
        q(s, "cal", "OUT 10 V")
        q(s, "cal", "OPER;*WAI")
        assert reading(s) == pytest.approx(10.0, abs=2e-4)
        q(s, "cal", "OUT 40 V")  # the new setting is >= 33 V, the old one below: back to standby
        assert reading(s) == 0.0
        assert q(s, "cal", "ERR?") == '0,"No Error"'  # the confirmation is not an error
        q(s, "cal", "OPER;*WAI")  # the explicit confirmation
        assert reading(s) == pytest.approx(40.0, abs=GAIN * 40.0 + FLOOR + QUANTUM)
        q(s, "cal", "OUT 20 V;*WAI")  # downward from 40 V: no crossing, the output keeps running
        assert reading(s) == pytest.approx(20.0, abs=GAIN * 20.0 + FLOOR + QUANTUM)
        # -- a function change at high voltage returns to standby (was
        # test_a_function_change_at_high_voltage_returns_to_standby; the act's own OUT 40 V
        # crosses upward from the 20 V the previous act left, so its OPER re-confirms)
        q(s, "cal", "OUT 40 V")
        q(s, "cal", "OPER;*WAI")
        assert reading(s) == pytest.approx(40.0, abs=GAIN * 40.0 + FLOOR + QUANTUM)
        q(s, "cal", "OUT 40 V, 1 kHz")  # the function changes while the output is >= 33 V
        assert reading(s) == 0.0
        q(s, "cal", "OPER;*WAI")
        assert reading(s, "AC") == pytest.approx(40.0, abs=GAIN * 40.0 + FLOOR + QUANTUM)
        # -- the trip at the threshold boundary (was test_the_output_trips_at_the_threshold_boundary;
        # the act's OUT 32.9 V is a function change below 33 V: the switch keeps operating)
        # Operators Manual ch. 3/4: “A voltage >= 33 V is selected when the previous output voltage
        # was less than 33 V” -- 33.0 V itself is on the standby side; within the high range and
        # downward there is no crossing and the output keeps running (#558).
        q(s, "cal", "OUT 32.9 V")
        q(s, "cal", "OPER;*WAI")
        q(s, "cal", "OUT 33 V")  # exactly 33 V: >= the threshold, crossing upward
        assert reading(s) == 0.0
        q(s, "cal", "OPER;*WAI")
        q(s, "cal", "OUT 33.1 V;*WAI")  # 33 V -> 33.1 V: both sides of 33 V, no crossing
        assert reading(s) == pytest.approx(33.1, abs=GAIN * 33.1 + FLOOR + QUANTUM)
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


# ---- settling: one flow -----------------------------------------------------------------------------


def test_the_settling_flow():
    """OPER itself returns at once and *OPC? waits the manual's 5 s; *OPC sets ESR bit 0 once
    the output has settled."""
    s = open_bench(DMM_BENCH)
    try:
        # -- settling takes five seconds on the virtual clock (was
        # test_settling_takes_five_seconds_on_the_virtual_clock)
        q(s, "cal", "OUT 10 V")
        t0 = s.now_ns
        q(s, "cal", "OPER")
        assert s.now_ns == t0  # OPER itself returns at once
        assert q(s, "cal", "*OPC?") == "1"
        assert s.now_ns - t0 == round(5.0 * 1e9)  # the manual: settling < 5 s, modeled as 5 s
        # -- bridge: the ESR act's scenario starts in standby with OUT 10 V stored
        q(s, "cal", "STBY")
        # -- *OPC sets ESR bit 0 once settled (was test_opc_sets_esr_bit_zero_once_settled)
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
    # Idle time off: the bench's default lets wall time between commands move virtual time, so each run
    # would acquire at another instant (another noise realisation), and a frequency measured to three
    # digits from a noisy 10-cycle record can round either way.
    s = open_bench(SCOPE_BENCH, idle="off")
    try:
        q(s, "dut", ":MEASure ON;:MEASure:MODE SIMPle;:MEASure:SIMPle:SOURce C1")
        q(s, "cal", "OUT 10 V")
        q(s, "cal", "OPER;*OPC?")
        # 8-bit ADC, 30 codes/div at 2.5 V/div: 10 V is 120 codes, on scale. The mean of the noisy
        # record is the 10 V itself; its extremes carry the division's noise (37.5 mV rms, D46):
        # MAX reads a few codes above the calibrator's value.
        sigma = 0.015 * 2.5
        assert q(s, "dut", "MEAS:SIMP:VAL? MEAN") == "1.00E+01"
        assert 10.0 <= float(q(s, "dut", "MEAS:SIMP:VAL? MAX")) <= 10.0 + 8 * sigma
        q(s, "cal", "STBY")
        q(s, "cal", "OUT 1 V, 1 kHz")
        q(s, "cal", "OPER;*OPC?")
        # 1 V RMS: 2.83 Vpp, a few codes of noise on top; FREQ and RMS are untroubled by it.
        assert 2.83 <= float(q(s, "dut", "MEAS:SIMP:VAL? PKPK")) <= 2.83 + 8 * sigma
        assert q(s, "dut", "MEAS:SIMP:VAL? FREQ") == "1.00E+03"
        assert q(s, "dut", "MEAS:SIMP:VAL? RMS") == "1.00E+00"
        assert float(q(s, "dut", "MEAS:SIMP:VAL? MEAN")) == pytest.approx(0.0, abs=5e-3)
    finally:
        s.close()


# ---- identity, the error queue and the host port: one flow -------------------------------------------


def test_the_identity_and_host_port_flow():
    """*IDN?/*OPT? identify the unit and ERR? classifies the unmodeled headers (there are no
    :SYSTem: headers); OPER? reads the operate setting and LOCAL/REMOTE/LOCKOUT save the
    interface state."""
    s = open_bench(DMM_BENCH)
    try:
        # -- identity and restricted builtins (was test_identity_and_restricted_builtins)
        assert q(s, "cal", "*IDN?") == "FLUKE,5522A,5248101,1.0+1.3+1.8"
        assert q(s, "cal", "*OPT?") == "0"  # no options installed on this unit
        # The 5522A has no SCPI :SYSTem: headers: the error queue is ERR?, the clock is CLOCK.
        q(s, "cal", "OUT 10")
        assert q(s, "cal", "ERR?") == '519,"Must specify an output unit"'
        assert q(s, "cal", ":SYST:ERR?") is None
        assert q(s, "cal", "ERR?") == '-113,"Undefined header;not implemented in this definition"'
        assert q(s, "cal", "*TRG") is None  # TC measurement on the real instrument; not modeled
        assert q(s, "cal", "ERR?") == '-113,"Undefined header;not implemented in this definition"'
        # -- OPER? and the host port commands (was test_operate_query_and_the_host_port_commands;
        # the act's own *RST returns the unit to the power-up state and the queue is empty again)
        # #119: OPER? reads the operate setting, and LOCAL / REMOTE / LOCKOUT save the interface state.
        q(s, "cal", "*RST")
        assert q(s, "cal", "OPER?") == "0"
        q(s, "cal", "OUT 10 V;OPER")
        assert q(s, "cal", "OPER?") == "1"
        q(s, "cal", "STBY")
        assert q(s, "cal", "OPER?") == "0"
        cal = s.instrument("cal")
        for command in ("REMOTE", "LOCKOUT", "LOCAL"):
            q(s, "cal", command)
            assert cal.state["interface"] == command
        assert q(s, "cal", "ERR?") == '0,"No Error"'
    finally:
        s.close()


# ---- current and resistance outputs (#569) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "ohms", "volts", "fault"),
    [
        ("OUT 10 MA", 600.0, 6.0, None),  # 6 V: within the 33 mA DC range's 7 V
        ("OUT 10 MA", 800.0, 0.0, "1500"),  # 8 V: past it
        ("OUT 10 MA, 1 KHZ", 600.0, 6.0, "1500"),  # 6 V rms: past the 33 mA AC range's 5 V rms
        ("OUT 2 A", 2.5, 5.0, None),  # 5 V: within the 3 A DC range's 6 V
    ],
)
def test_a_current_output_drives_the_load_within_its_compliance(command, ohms, volts, fault):
    """Chapter 1 DC/AC Current, Max Compliance Voltage by range: beyond it the output trips to
    standby with error 1500 (Compliance voltage exceeded)."""
    s = open_bench(loaded_bench(ohms))
    try:
        q(s, "dut", command + ";OPER")
        q(s, "dut", "*OPC?")
        inst = s.instrument("dut")
        signal = inst.session.port_signal(inst.components["out"], "out")
        if fault is None:
            assert signal.rms() == pytest.approx(volts, rel=1e-3)
            assert q(s, "dut", "ERR?") == '0,"No Error"'
        else:
            assert signal.rms() == 0.0
            assert q(s, "dut", "ERR?") == f'{fault},"Compliance voltage exceeded"'
            assert q(s, "dut", "OPER?") == "0"
    finally:
        s.close()


def test_the_resistance_output_is_continuously_variable_in_the_circuit():
    """Resistance: continuously variable from 0 to 1.1 Gohm (not a set of nominal values): 1234 ohm
    across the terminals divides a 1 V source through 1 kohm."""
    data = loaded_bench(1.0e9).model_dump(by_alias=True)
    data["parts"] = {"src": {"type": "dc_supply", "params": {"v": 1.0, "r": 1000.0}}}
    data["wires"] = [{"from": "src.OUT", "to": "dut.OUT"}]
    s = open_bench(BenchDefinition.model_validate(data))
    try:
        q(s, "dut", "OUT 1234 OHM;OPER")
        q(s, "dut", "*OPC?")
        assert q(s, "dut", "OUT?") == "1.234000E+03,OHM,0E+00,0,0.000000E+00"
        inst = s.instrument("dut")
        signal = inst.session.port_signal(inst.components["out"], "out")
        assert signal.mean() == pytest.approx(1234.0 / 2234.0, rel=1e-6)
    finally:
        s.close()
