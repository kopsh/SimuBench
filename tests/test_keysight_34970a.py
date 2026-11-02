"""Keysight 34970A (issue 199): the User's Guide's scan, trigger and reading-memory flow.

The manual cases (tests/models/keysight-34970a/cases.yaml) check command by command through the
case runner; these tests run the real-instrument-style script
examples/scripts/keysight_34970a_scan.py on the example bench and check the parts a SCPI
transcript cannot see: the relay's closure loading the bench circuit (the internal DMM's 10 MΩ
divides a 1 MΩ source), the scan's virtual time (relay settling + DMM reading time), the reading
memory's fetch/remove semantics, and the 3-slot SCPI channel domain.
"""

import importlib.util
import math
import shutil
import sys
from pathlib import Path

import pytest
import pyvisa

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_34970a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_34970a_scan.py"
RELAY_SETTLE_S = 0.003
#: 1 PLC at 50 Hz plus the model's overhead.
DMM_READING_S = 0.021
#: A 1 MΩ source behind the internal DMM's 10 MΩ input reads 10/11 of its open-circuit voltage.
LOADED_101 = 10.0 / 11.0


def _script():
    spec = importlib.util.spec_from_file_location("keysight_34970a_scan", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


script = _script()


def session():
    return open_bench(BENCH, idle="off")


def q(s, command: str) -> str | None:
    s.write("daq", (command + "\n").encode())
    out = s.read("daq")
    return out.decode().strip() if out else None


def numbers(reply: str) -> list[float]:
    return [float(v) for v in reply.split(",") if v.strip()]


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    bench = tmp_path / "bench.yaml"
    shutil.copy(BENCH, bench)
    rm = pyvisa.ResourceManager(f"{bench}@simubench")
    try:
        monkeypatch.setattr(sys, "argv", ["keysight_34970a_scan.py", "daq", "--bench", str(bench)])
        script.main()
    finally:
        rm.close()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Keysight Technologies,34970A,0,1.0-1.0-1.0"
    assert out[1] == "scan: 0.90909,1.81818,3.63636"
    assert out[2] == "opc: 1"
    assert out[3] == "memory: stored=6 points=6 removed=3 left=3"
    assert out[4] == "relays: closed=1,0 opened=0"
    assert out[5] == "high-z: 0.9999,1.9998,3.9996,0.9999,1.9998,3.9996"
    assert out[6] == '+0,"No error"'


def test_the_scan_runs_in_ascending_order_and_repeats_the_list():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@103,101);:TRIG:COUN 2") is None
        assert q(s, "TRIG:COUN?") == "2"
        # The 34970A scans the list in ascending channel order, not in the order it was written.
        assert numbers(q(s, "READ?")) == pytest.approx(
            [LOADED_101, 4 * LOADED_101, LOADED_101, 4 * LOADED_101], rel=1e-4
        )
        # The stored list keeps the written order.
        assert q(s, "ROUT:SCAN?") == "(@103,101)"
    finally:
        s.close()


def test_read_does_not_store_readings_but_init_does():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@101:103)") is None
        assert q(s, "READ?") is not None
        assert q(s, "DATA:POIN?") == "0"
        assert q(s, "FETC?") is None
        assert q(s, "SYST:ERR?").startswith('-230,"Data stale"')

        assert q(s, "INIT") is None
        assert q(s, "SYST:ERR?") == '+0,"No error"'
        assert q(s, "*OPC?") == "1"
        assert q(s, "DATA:POIN?") == "3"
        stored = numbers(q(s, "FETC?"))
        assert len(stored) == 3 and stored[0] == pytest.approx(LOADED_101, rel=1e-4)
        # FETCh? does not clear the memory; DATA:REMove? takes the oldest readings out.
        assert q(s, "DATA:POIN?") == "3"
        assert len(numbers(q(s, "DATA:REM? 2"))) == 2
        assert q(s, "DATA:POIN?") == "1"
    finally:
        s.close()


def test_the_scan_reading_time_is_the_relay_settling_plus_the_dmm_reading():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@101:103)") is None
        t0 = s.now_ns
        assert q(s, "READ?") is not None
        # Three readings at 1 PLC: 3 × (3 ms relay + 21 ms reading).
        expected = 3 * (RELAY_SETTLE_S + DMM_READING_S)
        assert (s.now_ns - t0) / 1e9 == pytest.approx(expected, abs=1e-3)
    finally:
        s.close()


def test_the_relay_closure_loads_the_source_and_high_z_removes_the_load():
    s = session()
    try:
        assert q(s, "SENS:VOLT:DC:RANG 10,(@101);:SENS:VOLT:DC:RANG:AUTO OFF,(@101)") is None
        assert q(s, "ROUT:SCAN (@101)") is None
        # 1 MΩ source behind the 10 MΩ input: the reading is 10/11.
        assert numbers(q(s, "READ?")) == pytest.approx([LOADED_101], rel=1e-5)
        # >10 GΩ on the 10 V range: the source is no longer loaded.
        assert q(s, "INP:IMP:AUTO ON,(@101)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([1.0], rel=1e-3)
        # The circuit really changed: the reading before the switch was lower by the divider.
        assert q(s, "INP:IMP:AUTO OFF,(@101)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([LOADED_101], rel=1e-5)
    finally:
        s.close()


def test_one_bus_trigger_runs_the_whole_scan_and_a_second_one_is_ignored():
    s = session()
    try:
        # scan_table's run_samples bakes TRIG:COUN into one run: one *TRG takes both sweeps.
        # (The guide's BUS scan count is the number of triggers; the deviation is in the coverage
        # table and is the scan_table component's debt.)
        assert q(s, "ROUT:SCAN (@101);:TRIG:SOUR BUS;:TRIG:COUN 2;:INIT") is None
        assert q(s, "*TRG") is None
        assert q(s, "SYST:ERR?") == '+0,"No error"'
        assert q(s, "*OPC?") == "1"
        assert q(s, "FETC?") == "+9.09090000E-01,+9.09090000E-01"
        assert q(s, "*TRG") is None
        assert q(s, "SYST:ERR?") == '-211,"Trigger ignored"'
    finally:
        s.close()


def test_read_with_a_bus_trigger_source_is_a_trigger_deadlock():
    s = session()
    try:
        assert q(s, "TRIG:SOUR BUS") is None
        assert q(s, "READ?") is None
        assert q(s, "SYST:ERR?") == '-214,"Trigger deadlock"'
    finally:
        s.close()


def test_abort_stops_a_scan_waiting_for_a_trigger():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@101);:TRIG:SOUR BUS;:INIT") is None
        assert q(s, "ABOR") is None
        assert q(s, "*TRG") is None
        assert q(s, "SYST:ERR?").startswith('-211,"Trigger ignored"')
    finally:
        s.close()


def test_the_channel_domain_is_the_three_slots_but_only_slot_one_has_a_module():
    s = session()
    try:
        # 101 is a channel of the 34901A in slot 1; 201 exists as a number but slot 2 is empty;
        # 121 is not a declared channel at all; 1 is an internal index, not a bus number.
        assert q(s, "ROUT:CLOS (@101)") is None
        assert q(s, "ROUT:CLOS? (@101)") == "1"
        for number in (201, 121, 1):
            assert q(s, f"ROUT:CLOS (@{number})") is None
            assert q(s, "SYST:ERR?").startswith('-114,"Header suffix out of range"')
        # A range may span slots and skips the numbers the numbering does not declare.
        assert q(s, "ROUT:SCAN (@101:205)") is None
        assert q(s, "ROUT:SCAN?") == "(@101:120,201:205)"
        # Running that scan reaches the empty slot and reports the missing channel.
        assert q(s, "READ?") is None
        assert q(s, "SYST:ERR?").startswith('-114,"Header suffix out of range"')
    finally:
        s.close()


def test_the_error_queue_holds_ten_entries():
    s = session()
    try:
        for _ in range(10):
            assert q(s, "ROUT:CLOS (@201)") is None
        replies = [q(s, "SYST:ERR?") for _ in range(10)]
        assert replies[0].startswith('-114,"Header suffix out of range"')
        assert replies[8].startswith('-114,"Header suffix out of range"')
        assert replies[9] == '-350,"Error queue overflow"'
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_reset_restores_the_manual_defaults_and_opens_the_relays():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@101:103);:TRIG:COUN 5;:TRIG:SOUR BUS;:TRIG:TIMER 5") is None
        assert q(s, "ROUT:CLOS (@101);:DISP OFF;:SENS:VOLT:DC:NPLC 10,(@101)") is None
        assert q(s, "*RST") is None
        assert q(s, "ROUT:SCAN?") == "(@)"
        assert q(s, "TRIG:COUN?") == "1"
        assert q(s, "TRIG:SOUR?") == "IMM"
        assert q(s, "TRIG:TIMER?") == "+0.00000000E+00"
        assert q(s, "ROUT:CLOS? (@101:103)") == "0,0,0"
        assert q(s, "DISP?") == "1"
        assert q(s, "SENS:VOLT:DC:NPLC? (@101)") == "+1.00000000E+00"
        assert q(s, "SENS:VOLT:DC:RANG:AUTO? (@101)") == "1"
    finally:
        s.close()


def test_display_and_scpi_version_are_accepted_without_changing_the_reading():
    s = session()
    try:
        assert q(s, "DISP OFF;:DISP:TEXT 'SCANNING'") is None
        assert q(s, "DISP?") == "0"
        assert q(s, "DISP:TEXT?") == '"SCANNING"'
        assert q(s, "SYST:VERS?") == "1994.0"
        # The displayed text and the display state do not change a scan's readings.
        assert q(s, "ROUT:SCAN (@101)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([LOADED_101], rel=1e-5)
        assert q(s, "DISP:TEXT:CLEAR") is None
        assert q(s, "DISP:TEXT?") == '""'
    finally:
        s.close()


def test_an_undefined_command_says_not_modeled_in_this_definition():
    s = session()
    try:
        assert q(s, "CONF:VOLT:DC 10,(@101)") is None
        error = q(s, "SYST:ERR?")
        assert error.startswith('-113,"Undefined header;not implemented in this definition"'), error
    finally:
        s.close()


def test_the_scan_interval_is_stored_but_does_not_change_the_reading_time_yet():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@101:102);:TRIG:SOUR TIMER;:TRIG:TIMER 5") is None
        assert q(s, "TRIG:TIMER?") == "+5.00000000E+00"
        t0 = s.now_ns
        assert q(s, "READ?") is not None
        # First-wave debt (scan_table): the interval is a setting, the readings take
        # relay settling + DMM reading time only.
        expected = 2 * (RELAY_SETTLE_S + DMM_READING_S)
        assert (s.now_ns - t0) / 1e9 == pytest.approx(expected, abs=1e-3)
    finally:
        s.close()


def test_readings_are_eight_digit_nr3_numbers():
    s = session()
    try:
        assert q(s, "ROUT:SCAN (@101)") is None
        reply = q(s, "READ?")
        assert reply == "+9.09090000E-01", reply
        assert math.isclose(float(reply), LOADED_101, rel_tol=1e-5)
    finally:
        s.close()
