"""Keysight N6705C (issue 339): the modular DC power analyzer against the engine.

The manual cases (tests/models/keysight-n6705c/cases.yaml) check command by command; these tests run
the real-instrument script examples/scripts/keysight_n6705c_check.py on the example bench and check
what a transcript cannot see: each slot's module limits, the 100 W power envelope (#533), remote sensing
past lead resistance and the protection latches.
"""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.engine.session import Session
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight-n6705c.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_n6705c_check.py"


def _script():
    spec = importlib.util.spec_from_file_location("keysight_n6705c_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session(parts: dict, wires: list):
    lib = DefinitionLibrary()
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "t",
            "instruments": {"psu": {"model": "keysight-n6705c"}},
            "parts": parts,
            "wires": wires,
        }
    )
    s = Session(bench, lambda model: lib.get(model), clock="command", seed=7, idle="off")

    def q(cmd: str) -> str:
        s.write("psu", cmd.encode() + b"\n")
        out = s.read("psu")
        return out.decode().strip() if out else ""

    return s, q


def load(ch: int, r: float) -> tuple[dict, list]:
    return (
        {f"r{ch}": {"type": "resistor", "params": {"r": r}}},
        [{"from": f"psu.OUT{ch}+", "to": f"r{ch}.A"}, {"from": f"r{ch}.B", "to": f"psu.OUT{ch}-"}],
    )


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["keysight_n6705c_check.py", "psu", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Keysight Technologies,N6705C,MY56000123,E.02.07.3231"
    assert out[1] == "modules: N6762A,N6762A,N6752A,N6752A"
    ch1 = out[2].split()
    assert float(ch1[2]) == pytest.approx(5.0, abs=2e-3) and float(ch1[4]) == pytest.approx(0.5, abs=1e-3)
    # channel 3 went into constant current on 1 ohm: the protection switched it off
    assert out[3] == "channel 3: 0.0000 V, 0.0000 A"
    assert out[4] == "output states: 1,0"
    assert out[-1] == '+0,"No error"'


@pytest.mark.parametrize(("ch", "max_a"), [(1, 3.06), (2, 3.06), (3, 10.2), (4, 10.2)])
def test_each_slot_takes_its_module_current_range(ch, max_a):
    s, q = session({}, [])
    try:
        assert float(q(f"CURR? MAX,(@{ch})")) == pytest.approx(max_a)
        q(f"CURR {max_a},(@{ch})")
        assert q("SYST:ERR?") == '+0,"No error"'
        q(f"CURR {max_a + 0.1},(@{ch})")
        assert q("SYST:ERR?") == '-222,"Data out of range"'
    finally:
        s.close()


def test_the_100_watt_envelope_limits_the_output():
    """#533: 50 V into 5 ohm asks for 10 A (500 W): the N6752A's 100 W rating holds the output
    to constant power (I = sqrt(100 / 5)); the terminals see V = 5 ohm x I."""
    s, q = session(*load(3, 5.0))
    try:
        q("VOLT 50,(@3);:CURR 10,(@3);:OUTP ON,(@3)")
        q("*OPC?")
        v, i = float(q("MEAS:VOLT? (@3)")), float(q("MEAS:CURR? (@3)"))
        assert i == pytest.approx(20.0**0.5, rel=1e-3)
        assert v * i == pytest.approx(100.0, rel=1e-3)
        assert q("SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_remote_sense_regulates_past_the_leads():
    """A 0.5 ohm lead in front of 10 ohm: local sensing holds 5 V at the terminals (the DUT gets
    5 x 10/10.5); remote sensing holds 5 V at the DUT (the current says which)."""
    parts = {
        "lead": {"type": "resistor", "params": {"r": 0.5}},
        "dut": {"type": "resistor", "params": {"r": 10.0}},
    }
    wires = [
        {"from": "psu.OUT1+", "to": "lead.A"},
        {"from": "lead.B", "to": "dut.A"},
        {"from": "dut.B", "to": "psu.OUT1-"},
        {"from": "psu.SENSE1+", "to": "dut.A"},
        {"from": "dut.B", "to": "psu.SENSE1-"},
    ]
    s, q = session(parts, wires)
    try:
        q("VOLT 5,(@1);:CURR 1,(@1);:OUTP ON,(@1)")
        q("*OPC?")
        assert float(q("MEAS:CURR? (@1)")) == pytest.approx(5.0 / 10.5, rel=1e-3)
        q("VOLT:SENS:SOUR EXT,(@1)")
        q("*OPC?")
        assert float(q("MEAS:VOLT? (@1)")) == pytest.approx(5.0, rel=1e-4)
        assert float(q("MEAS:CURR? (@1)")) == pytest.approx(0.5, rel=1e-3)
    finally:
        s.close()


def test_rise_and_fall_delays_sequence_the_outputs():
    """#540: channel 1 turns on at once, channel 3 0.5 s later; turning both off, channel 3 holds
    for its 0.2 s fall delay. The relay's 10 ms comes on top of the rise delay."""
    parts, wires = load(1, 10.0)
    p3, w3 = load(3, 10.0)
    s, q = session({**parts, **p3}, wires + w3)
    try:
        q("VOLT 5,(@1,3);:CURR 1,(@1,3);:OUTP:DEL:RISE 0.5,(@3);:OUTP:DEL:FALL 0.2,(@3)")
        q("OUTP ON,(@1,3)")
        s.advance(0.011)
        assert [float(x) > 4.9 for x in q("MEAS:VOLT? (@1,3)").split(",")] == [True, False]
        s.advance(0.5)
        assert [float(x) > 4.9 for x in q("MEAS:VOLT? (@1,3)").split(",")] == [True, True]
        q("OUTP OFF,(@1,3)")
        assert [float(x) > 4.9 for x in q("MEAS:VOLT? (@1,3)").split(",")] == [False, True]
        s.advance(0.21)
        assert [float(x) > 4.9 for x in q("MEAS:VOLT? (@1,3)").split(",")] == [False, False]
    finally:
        s.close()


def test_status_registers_follow_the_channel(tmp_path):
    """#539: a script that polls the status registers sees CV/CC and an OCP trip per channel; the
    enabled QUES event raises status byte bit 3 until it is read."""
    s, q = session(*load(3, 1.0))
    try:
        q("VOLT 5,(@3);:CURR 2,(@3);:OUTP ON,(@3)")
        q("*OPC?")
        assert q("STAT:OPER:COND? (@3)") == "2"  # constant current on 1 ohm
        q("STAT:QUES:ENAB 2,(@3);:CURR:PROT:DEL 0,(@3);:CURR:PROT:STAT ON,(@3)")
        q("*OPC?")
        assert q("STAT:QUES:COND? (@3)") == "2"  # OC: the protection holds the output off
        assert q("STAT:OPER:COND? (@3)") == "4"  # OFF
        assert int(q("*STB?")) & 8
        assert q("STAT:QUES? (@3)") == "2"
        assert not int(q("*STB?")) & 8  # the event was read: the summary clears
        q("CURR:PROT:STAT OFF,(@3);:OUTP:PROT:CLE (@3)")
        q("*OPC?")
        assert q("STAT:QUES:COND? (@3)") == "0"
        assert q("STAT:OPER:COND? (@3)") == "2"
    finally:
        s.close()


def test_transition_filters_choose_which_edges_latch():
    """#549: with NTR on the CV bit and PTR cleared, a CV -> CC change latches only the falling CV
    edge; the default filters (PTR all ones, NTR 0) latch only rising edges; STATus:PRESet restores
    them."""
    s, q = session(*load(3, 1.0))
    try:
        assert q("STAT:OPER:PTR? (@3)") == "32767" and q("STAT:OPER:NTR? (@3)") == "0"
        q("VOLT 1,(@3);:CURR 2,(@3);:OUTP ON,(@3)")
        q("*OPC?")
        assert q("STAT:OPER:COND? (@3)") == "1"  # CV: 1 A into 1 ohm
        q("STAT:OPER:NTR 1,(@3);:STAT:OPER:PTR 0,(@3)")
        q("STAT:OPER? (@3)")  # read: clears the event register
        q("CURR 0.5,(@3)")
        q("*OPC?")
        assert q("STAT:OPER:COND? (@3)") == "2"  # CC now
        assert q("STAT:OPER? (@3)") == "1"  # the CV bit fell; the CC rise is filtered out
        q("CURR 2,(@3)")
        q("*OPC?")
        assert q("STAT:OPER? (@3)") == "0"  # CC fell (NTR has no CC bit), CV rose (PTR 0)
        q("STAT:QUES:PTR 5,(@3);:STAT:QUES:NTR 3,(@3);:STAT:OPER:ENAB 1,(@3)")
        q("STAT:PRES")
        assert q("STAT:OPER:PTR? (@3)") == "32767" and q("STAT:OPER:NTR? (@3)") == "0"
        assert q("STAT:QUES:PTR? (@3)") == "32767" and q("STAT:QUES:NTR? (@3)") == "0"
        assert q("STAT:OPER:ENAB? (@3)") == "0"
        q("CURR 0.5,(@3)")
        q("*OPC?")
        assert q("STAT:OPER? (@3)") == "2"  # default filters again: only the CC rise latches
        assert q("SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_a_trigger_right_after_init_is_ignored_until_the_system_is_ready():
    """#548 (N6700C-90901 Programming Output Transients): the transient system takes a few
    milliseconds (5 ms here) after INIT:TRAN to accept a trigger; one before that is ignored without
    an error, and WTG-tran (bit 4) appears once it is ready. ABORt drops a pending ready."""
    s, q = session(*load(1, 10.0))
    try:
        q("VOLT 2,(@1);:CURR 1,(@1);:VOLT:TRIG 6,(@1);:VOLT:MODE STEP,(@1);:OUTP ON,(@1)")
        q("*OPC?")
        q("INIT:TRAN (@1);*TRG")  # too early: ignored
        assert q("VOLT? (@1)") == "+2.000000E+00"
        assert q("STAT:OPER:COND? (@1)") == "65"  # TRAN-active, not yet WTG-tran
        s.advance(0.005)
        assert q("STAT:OPER:COND? (@1)") == "81"  # ready: WTG-tran
        q("*TRG")
        assert q("VOLT? (@1)") == "+6.000000E+00"
        q("VOLT 2,(@1);:INIT:TRAN (@1);:ABOR:TRAN (@1)")
        s.advance(0.01)
        q("*TRG")  # aborted before it was ready: stays idle
        assert q("VOLT? (@1)") == "+2.000000E+00"
        assert q("STAT:OPER:COND? (@1)") == "1"
        assert q("SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_a_bus_trigger_steps_only_the_armed_channels():
    """#541: channels 1 and 3 in STEP mode, only channel 1 initiated: *TRG steps channel 1's
    output to its triggered level (seen on the load) and leaves channel 3 alone."""
    parts, wires = load(1, 10.0)
    p3, w3 = load(3, 10.0)
    s, q = session({**parts, **p3}, wires + w3)
    try:
        q("VOLT 2,(@1,3);:CURR 1,(@1,3);:VOLT:TRIG 6,(@1,3);:VOLT:MODE STEP,(@1,3);:OUTP ON,(@1,3)")
        q("*OPC?")
        q("INIT:TRAN (@1)")
        q("*OPC?")  # ready for a trigger a few milliseconds after INIT (#548)
        q("*TRG")
        volts = [float(x) for x in q("MEAS:VOLT? (@1,3)").split(",")]
        assert volts == pytest.approx([6.0, 2.0], abs=2e-3)
        q("INIT:TRAN (@3);:ABOR:TRAN (@3);*OPC?")
        q("*TRG")  # nothing armed: no change, no error (the manual lists none)
        assert float(q("MEAS:VOLT? (@3)")) == pytest.approx(2.0, abs=2e-3)
        assert q("SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_switching_on_during_the_fall_delay_keeps_the_output_on():
    """#545: OUTP OFF starts the 1 s fall delay; OUTP ON before it ends keeps the output on (it
    never went off) and drops the pending turn-off; a second OFF does not restart the countdown."""
    s, q = session(*load(1, 10.0))
    try:
        q("VOLT 5,(@1);:CURR 1,(@1);:OUTP:DEL:RISE 0.5,(@1);:OUTP:DEL:FALL 1,(@1)")
        q("OUTP ON,(@1)")
        s.advance(0.6)
        assert float(q("MEAS:VOLT? (@1)")) > 4.9
        q("OUTP OFF,(@1)")
        s.advance(0.3)
        q("OUTP ON,(@1)")
        assert float(q("MEAS:VOLT? (@1)")) > 4.9
        s.advance(1.0)
        assert float(q("MEAS:VOLT? (@1)")) > 4.9  # the old turn-off did not fire
        q("OUTP OFF,(@1)")
        s.advance(0.6)
        q("OUTP OFF,(@1)")  # repeated: the countdown keeps running
        s.advance(0.5)
        assert float(q("MEAS:VOLT? (@1)")) == 0.0
    finally:
        s.close()


def test_a_bench_that_starts_with_the_output_on_waits_for_the_rise_delay():
    """#545: sync() schedules the relay after rise_delay + relay time, as OUTP ON does."""
    parts, wires = load(1, 10.0)
    lib = DefinitionLibrary()
    bench = BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "t",
            "instruments": {
                "psu": {
                    "model": "keysight-n6705c",
                    "state": {
                        "ch1.voltage": 5.0,
                        "ch1.current": 1.0,
                        "ch1.rise_delay": 0.5,
                        "ch1.output": True,
                    },
                }
            },
            "parts": parts,
            "wires": wires,
        }
    )
    s = Session(bench, lambda model: lib.get(model), clock="command", seed=7, idle="off")

    def q(cmd: str) -> str:
        s.write("psu", cmd.encode() + b"\n")
        out = s.read("psu")
        return out.decode().strip() if out else ""

    try:
        assert float(q("MEAS:VOLT? (@1)")) == 0.0  # the relay waits rise_delay + relay time
        s.advance(0.3)
        assert float(q("MEAS:VOLT? (@1)")) == 0.0
        s.advance(0.3)
        assert float(q("MEAS:VOLT? (@1)")) > 4.9
    finally:
        s.close()


def test_ovp_latches_until_cleared():
    s, q = session(*load(1, 10.0))
    try:
        q("VOLT 10,(@1);:CURR 3,(@1);:OUTP ON,(@1)")
        q("*OPC?")
        q("VOLT:PROT 8,(@1)")
        q("*OPC?")
        assert float(q("MEAS:VOLT? (@1)")) == 0.0
        q("VOLT 5,(@1)")  # the cause removed: the latch still holds the output off
        q("*OPC?")
        assert float(q("MEAS:VOLT? (@1)")) == 0.0
        q("OUTP:PROT:CLE (@1)")
        q("*OPC?")
        assert float(q("MEAS:VOLT? (@1)")) == pytest.approx(5.0, abs=2e-3)
    finally:
        s.close()
