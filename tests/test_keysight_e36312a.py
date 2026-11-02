"""Keysight E36312A (issue 198): the programming guide's channel-list and measurement flow.

The manual cases (tests/models/keysight-e36312a/cases.yaml) check command by command; these tests
run the real-instrument script examples/scripts/keysight_e36312a_check.py on the example bench,
check the parts a SCPI transcript cannot see (the relay's 10 ms virtual time, remote sense at the
load, the CV/CC mode the readback block reports to the panel), and the three outputs' independent
settings.
"""

import importlib.util
import math
import re
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_e36312a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_e36312a_check.py"
RELAY_S = 0.01
#: CH1 sees 10 ohm with the pass stage's 1 mohm on each side: 5 V * 10 / 10.002.
CH1_INT = 5.0 * 10.0 / 10.002
CH1_CURRENT = 5.0 / 10.002


def _script():
    spec = importlib.util.spec_from_file_location("keysight_e36312a_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session():
    return open_bench(BENCH, idle="off")


def q(s, command: str) -> str | None:
    s.write("psu", (command + "\n").encode())
    out = s.read("psu")
    return out.decode().strip() if out else None


def close_enough(value: float, expected: float, rel: float = 1e-6) -> bool:
    return math.isclose(value, expected, rel_tol=rel)


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["keysight_e36312a_check.py", "psu", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
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
    s = session()
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
