"""Keysight E36312A (issue 198): the programming guide's channel-list and measurement flow.

The manual cases (tests/models/keysight-e36312a/cases.yaml) check command by command; these tests
run the real-instrument script examples/scripts/keysight_e36312a_check.py on the example bench,
check the parts a SCPI transcript cannot see (the relay's 10 ms virtual time, remote sense at the
load, the CV/CC mode the readback block reports to the panel), and the three outputs' independent
settings.
"""

import math
import re
from pathlib import Path

import pytest
import yaml

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_e36312a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_e36312a_check.py"
RELAY_S = 0.01
#: CH1 sees 10 ohm with the pass stage's 1 mohm on each side: 5 V * 10 / 10.002.
CH1_INT = 5.0 * 10.0 / 10.002
CH1_CURRENT = 5.0 / 10.002

script = load_script(SCRIPT)


def session():
    return open_bench(BENCH, idle="off")


def sense_wired_session():
    """The example bench plus the SENSE1 wires: the sense pair joins the force wires on the
    load's two terminals (a star join, no diagnostic since #316)."""
    from simubench.models.bench import BenchDefinition

    data = yaml.safe_load(BENCH.read_text(encoding="utf-8"))
    data["wires"] += [
        {"from": "psu.SENSE1+", "to": "r1.A"},
        {"from": "r1.B", "to": "psu.SENSE1-"},
    ]
    bench = BenchDefinition.model_validate(data)
    return open_bench(bench, idle="off")


def q(s, command: str) -> str | None:
    return session_query(s, "psu", command)


def close_enough(value: float, expected: float, rel: float = 1e-6) -> bool:
    return math.isclose(value, expected, rel_tol=rel)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["psu"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0].startswith("Keysight Technologies,E36312A,")
    assert out[1].startswith("channel 1: ")
    assert out[2].startswith("channel 2: ")
    assert out[3].startswith("channel 3: ")
    assert out[4] == "list: 1.5,1.5"
    assert out[5] == "ovp: trip=1 output=0"
    assert out[6] == 'ovp error: +729,"Not allow to enable output"'
    assert out[7] == "recovered output=1"
    assert out[8] == '+0,"No error"'

    values = [re.findall(r"-?\d+\.\d+", line) for line in out[1:4]]
    voltages = [float(v[0]) for v in values]
    currents = [float(v[1]) for v in values]
    powers = [float(v[2]) for v in values]
    assert voltages == pytest.approx([CH1_INT, 3.3 * 100 / 100.002, 12.0 * 100 / 100.002], rel=1e-6)
    assert currents == pytest.approx([CH1_CURRENT, 3.3 / 100.002, 12.0 / 100.002], rel=1e-6)
    assert powers == pytest.approx([v * i for v, i in zip(voltages, currents, strict=True)], rel=1e-6)


def test_the_relay_closes_in_virtual_time():
    s = session()
    try:
        assert q(s, "VOLT 5,(@1);:CURR 1,(@1)") is None
        t0 = s.now_ns
        assert q(s, "OUTP ON,(@1)") is None
        assert q(s, "MEAS:VOLT? (@1)") == "+0.0000000E+00"
        # Waiting for the output relay advances virtual time by its 10 ms action.
        assert q(s, "*WAI") is None
        assert (s.now_ns - t0) / 1e9 == pytest.approx(RELAY_S, abs=1e-3)
        assert float(q(s, "MEAS:VOLT? (@1)")) == pytest.approx(CH1_INT, rel=1e-6)
    finally:
        s.close()


def test_remote_sense_regulates_the_voltage_at_the_load():
    s = sense_wired_session()
    try:
        assert q(s, "VOLT 5,(@1);:CURR 1,(@1);:OUTP ON,(@1);*WAI") is None
        assert float(q(s, "MEAS:VOLT? (@1)")) == pytest.approx(CH1_INT, rel=1e-6)
        assert q(s, "VOLT:SENS EXT,(@1)") is None
        assert q(s, "VOLT:SENS? (@1)") == "EXT"
        assert float(q(s, "MEAS:VOLT? (@1)")) == pytest.approx(5.0, rel=1e-9)
        assert float(q(s, "MEAS:CURR? (@1)")) == pytest.approx(0.5, rel=1e-7)
        assert q(s, "VOLT:SENS INT,(@1)") is None
        assert float(q(s, "MEAS:VOLT? (@1)")) == pytest.approx(CH1_INT, rel=1e-6)
    finally:
        s.close()


def test_the_current_limit_turns_the_output_into_cc():
    s = session()
    try:
        assert q(s, "VOLT 5,(@1);:CURR 0.1,(@1);:OUTP ON,(@1);*WAI") is None
        assert float(q(s, "MEAS:VOLT? (@1)")) == pytest.approx(1.0, rel=1e-6)
        assert float(q(s, "MEAS:CURR? (@1)")) == pytest.approx(0.1, rel=1e-9)
        values = s.instrument("psu").components["ch1.read"].readings()["values"]
        assert values["regulation"] == "CC"
        assert values["power"] == pytest.approx(0.1, rel=1e-6)
    finally:
        s.close()


def test_the_three_outputs_are_independent_and_the_channel_list_expands():
    s = session()
    try:
        assert q(s, "VOLT 5,(@1);:VOLT 3.3,(@2);:VOLT 12,(@3)") is None
        assert q(s, "CURR 1,(@1:3)") is None
        assert q(s, "OUTP ON,(@1:3);*WAI") is None
        assert q(s, "MEAS:VOLT? (@3,1,2)") == ("+1.1999760E+01,+4.9990002E+00,+3.2999340E+00")
        assert q(s, "OUTP? (@1:3)") == "1,1,1"
        # A channel list query answers in the list's order, not in channel order.
        assert q(s, "VOLT? (@2,1)") == "+3.30000000E+00,+5.00000000E+00"
    finally:
        s.close()


def test_the_example_bench_validates_without_diagnostics():
    """#296 acceptance: `simubench validate` on the example bench reports nothing."""
    from simubench.engine.validate import validate_data
    from simubench.library.loader import DefinitionLibrary

    _, report = validate_data(
        yaml.safe_load(BENCH.read_text(encoding="utf-8")),
        DefinitionLibrary([BENCH.parent / "instruments"]),
    )
    assert report.diagnostics == []


def test_out_of_range_values_name_the_channel_limits():
    s = session()
    try:
        assert q(s, "VOLT 7,(@1)") is None
        assert q(s, "SYST:ERR?") == '-222,"Data out of range"'
        assert q(s, "CURR 6,(@1)") is None
        assert q(s, "SYST:ERR?") == '-222,"Data out of range"'
        assert q(s, "VOLT 26,(@2)") is None
        assert q(s, "SYST:ERR?") == '-222,"Data out of range"'
        # CH3 is a +25 V output on the E36312A (not the E36311A's -25 V).
        assert q(s, "VOLT 25.75,(@3)") is None
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_ovp_trip_clear_and_the_729_output_refusal():
    s = session()
    try:
        assert q(s, "VOLT 5,(@1);:CURR 1,(@1);:VOLT:PROT 4,(@1);:OUTP ON,(@1);*WAI") is None
        assert q(s, "VOLT:PROT:TRIP? (@1)") == "1"
        assert q(s, "OUTP? (@1)") == "0"
        assert float(q(s, "MEAS:VOLT? (@1)")) == 0.0
        assert q(s, "OUTP ON,(@1)") is None
        assert q(s, "SYST:ERR?") == '+729,"Not allow to enable output"'
        assert q(s, "VOLT:PROT 6,(@1);:VOLT:PROT:CLE (@1)") is None
        assert q(s, "OUTP? (@1)") == "1"
        assert q(s, "*WAI") is None
        assert float(q(s, "MEAS:VOLT? (@1)")) == pytest.approx(CH1_INT, rel=1e-6)
        assert close_enough(float(q(s, "VOLT? (@1)")), 5.0, rel=1e-9)
    finally:
        s.close()


# ---- OCP by time in constant current and the CV/CC condition (#271) ----------------------------------


def cc_session():
    """CH1 at 5 V with a 10 mA limit into 10 Ω: constant current once the relay has closed."""
    s = session()
    q(s, "VOLT 5,(@1);:CURR 0.01,(@1);:OUTP ON,(@1)")
    s.advance(0.02)  # the relay closes after 10 ms
    return s


def test_the_condition_register_reads_cc_and_cv():
    s = cc_session()
    try:
        assert q(s, "STAT:QUES:INST:ISUM1:COND?") == "1"  # constant current
        assert q(s, "STAT:QUES:INST:ISUM2:COND?") == "0"  # output 2 off
        q(s, "CURR 1,(@1)")
        assert q(s, "STAT:QUES:INST:ISUM1:COND?") == "2"  # constant voltage
    finally:
        s.close()


def test_ocp_trips_after_the_delay_in_cc_and_only_its_own_clear_removes_it():
    s = cc_session()
    try:
        assert q(s, "CURR:PROT:STAT?") == "0"  # *RST: off — CC alone never trips
        s.advance(1.0)
        assert q(s, "CURR:PROT:TRIP? (@1)") == "0" and q(s, "OUTP? (@1)") == "1"
        q(s, "CURR:PROT:DEL 0.2,(@1);:CURR:PROT:STAT ON,(@1)")
        s.advance(0.1)
        assert q(s, "CURR:PROT:TRIP? (@1)") == "0"  # 100 ms of the 200 ms delay
        s.advance(0.2)
        assert q(s, "CURR:PROT:TRIP? (@1)") == "1"
        assert q(s, "VOLT:PROT:TRIP? (@1)") == "0"  # the OVP query answers OVP only
        assert q(s, "OUTP? (@1)") == "0"
        q(s, "VOLT:PROT:CLE (@1)")  # an OVP clear leaves the OCP latch
        assert q(s, "CURR:PROT:TRIP? (@1)") == "1"
        q(s, "CURR 1,(@1);:CURR:PROT:CLE (@1)")
        assert q(s, "CURR:PROT:TRIP? (@1)") == "0" and q(s, "OUTP? (@1)") == "1"  # restored
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


@pytest.mark.parametrize(("start", "tripped"), [("SCH", "0"), ("CCTR", "1")])
def test_the_delay_start_decides_whether_a_settings_change_restarts_it(start, tripped):
    """SCHange: a settings change while in CC starts the delay again; CCTRans: only entering CC."""
    s = cc_session()
    try:
        q(s, f"CURR:PROT:DEL:STAR {start},(@1);:CURR:PROT:DEL 0.2,(@1);:CURR:PROT:STAT ON,(@1)")
        s.advance(0.15)
        q(s, "CURR 0.02,(@1)")  # still CC: a settings change
        s.advance(0.1)
        assert q(s, "CURR:PROT:TRIP? (@1)") == tripped
    finally:
        s.close()


# ---- the questionable status register group (#348) -------------------------------------------------


def _cc_on_channel_1(s) -> None:
    """5 V with a 0.1 A limit into CH1's 10 Ω: the output goes into constant current."""
    assert q(s, "VOLT 5,(@1);:CURR 0.1,(@1);:OUTP ON,(@1)") is None
    s.advance(0.5)


def test_a_cv_to_cc_transition_latches_the_isummary_event_and_a_read_clears_it():
    s = session()
    try:
        assert q(s, "STAT:QUES:INST:ISUM1:EVEN?") == "0"
        _cc_on_channel_1(s)
        assert q(s, "STAT:QUES:INST:ISUM1:COND?") == "1"  # bit 0: CC
        assert q(s, "STAT:QUES:INST:ISUM1?") == "1"  # the event latched ...
        assert q(s, "STAT:QUES:INST:ISUM1?") == "0"  # ... and the read cleared it
        assert q(s, "STAT:QUES:INST:ISUM1:COND?") == "1"  # the condition stays
        assert q(s, "STAT:QUES:INST:ISUM2?") == "0"  # another output: its own register
        # Back to CV (a limit above 0.5 A): bit 1 rises.
        assert q(s, "CURR 1,(@1)") is None
        s.advance(0.5)
        assert q(s, "STAT:QUES:INST:ISUM1:COND?") == "2"
        assert q(s, "STAT:QUES:INST:ISUM1?") == "2"
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_the_enable_masks_carry_the_summary_up_to_the_status_byte():
    s = session()
    try:
        _cc_on_channel_1(s)
        # Nothing enabled: the event is latched but no summary reaches QUES or the status byte.
        assert q(s, "STAT:QUES:INST?") == "0"
        assert int(q(s, "*STB?")) & 8 == 0
        # ISUM1 CC -> QUES:INST bit 1 -> QUES bit 13 -> status byte bit 3 (and RQS with *SRE 8).
        assert q(s, "STAT:QUES:INST:ISUM1:ENAB 1;:STAT:QUES:INST:ENAB 2;:STAT:QUES:ENAB 8192;*SRE 8") is None
        assert q(s, "STAT:QUES:INST:ISUM1:ENAB?") == "1"
        assert q(s, "STAT:QUES:INST:ENAB?") == "2"
        assert q(s, "STAT:QUES:ENAB?") == "8192"
        assert int(q(s, "*STB?")) & (8 | 64) == 8 | 64
        assert q(s, "STAT:QUES?") == "8192"
        # A mask that leaves CC out (CV only) stops the chain.
        assert q(s, "STAT:QUES:INST:ISUM1:ENAB 2") is None
        assert int(q(s, "*STB?")) & 8 == 0
        # STATus:PRESet zeroes every enable mask.
        assert q(s, "STAT:PRES") is None
        assert [
            q(s, c) for c in ("STAT:QUES:ENAB?", "STAT:QUES:INST:ENAB?", "STAT:QUES:INST:ISUM1:ENAB?")
        ] == ["0"] * 3
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_a_bus_trigger_steps_after_the_trigger_delay():
    """#548 (E36311-90008 INITiate / TRIGger subsystems): INIT arms the output trigger system of
    the listed channels; *TRG steps a STEP-mode channel to its triggered level once the trigger
    delay has run (*WAI waits for it); without INIT the *TRG is simply ignored (no error)."""
    s = session()
    try:
        q(s, "VOLT 2,(@1,2);:VOLT:TRIG 4,(@1,2);:VOLT:MODE STEP,(@1,2);:OUTP ON,(@1,2);*WAI")
        q(s, "*TRG")  # nothing initiated: ignored
        assert q(s, "VOLT? (@1,2)") == "+2.00000000E+00,+2.00000000E+00"
        q(s, "TRIG:DEL 0.5,(@1);:INIT (@1)")
        q(s, "*TRG")
        assert q(s, "VOLT? (@1)") == "+2.00000000E+00"  # the delay runs
        s.advance(0.5)
        assert q(s, "VOLT? (@1,2)") == "+4.00000000E+00,+2.00000000E+00"  # only CH1 was initiated
        q(s, "VOLT 2,(@1);:INIT (@1);*TRG;:ABOR (@1)")  # aborted while delayed: no step
        s.advance(1.0)
        assert q(s, "VOLT? (@1)") == "+2.00000000E+00"
        q(s, "TRIG:DEL 0,(@2);:TRIG:SOUR IMM,(@2);:INIT (@2)")  # IMMediate: steps at once
        assert float(q(s, "MEAS:VOLT? (@2)")) == pytest.approx(4.0, rel=1e-3)
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()
