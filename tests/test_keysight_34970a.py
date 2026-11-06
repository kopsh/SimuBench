"""Keysight 34970A (issue 199): the User's Guide's scan, trigger and reading-memory flow.

The manual cases (tests/models/keysight-34970a/cases.yaml) check command by command through the
case runner; these tests run the real-instrument-style script
examples/scripts/keysight_34970a_scan.py on the example bench and check the parts a SCPI
transcript cannot see: the relay's closure loading the bench circuit (the internal DMM's 10 MΩ
divides a 1 MΩ source), the scan's virtual time (relay settling + DMM reading time), the reading
memory's fetch/remove semantics, and the 3-slot SCPI channel domain.

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original test's
body verbatim; acts that are provably state-compatible share one bench, with the bridging
commands noted.
"""

import math
from pathlib import Path

import pytest

from helpers import load_script, run_manual_script, session_query
from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "keysight_34970a.bench.yaml"
SCRIPT = ROOT / "examples" / "scripts" / "keysight_34970a_scan.py"
RELAY_SETTLE_S = 0.003
#: 1 PLC at 50 Hz plus the model's overhead.
DMM_READING_S = 0.021
#: A 1 MΩ source behind the internal DMM's 10 MΩ input reads 10/11 of its open-circuit voltage.
LOADED_101 = 10.0 / 11.0


script = load_script(SCRIPT)


def session():
    return open_bench(BENCH, idle="off")


def q(s, command: str) -> str | None:
    return session_query(s, "daq", command)


def numbers(reply: str) -> list[float]:
    return [float(v) for v in reply.split(",") if v.strip()]


def test_manual_script_runs_on_the_example_bench(tmp_path, monkeypatch, capsys):
    out = run_manual_script(script, ["daq"], BENCH, tmp_path, monkeypatch, capsys)
    assert out[0] == "Keysight Technologies,34970A,0,1.0-1.0-1.0"
    assert out[1] == "scan: 0.90909,1.81818,3.63636"
    assert out[2] == "opc: 1"
    assert out[3] == "memory: stored=6 points=6 removed=3 left=3"
    assert out[4] == "relays: closed=1,0 opened=0"
    assert out[5] == "high-z: 0.9999,1.9998,3.9996,0.9999,1.9998,3.9996"
    assert out[6] == '+0,"No error"'


# ---- the scan, the reading memory and the loaded source: one flow ---------------------------------


def test_the_scan_memory_and_loading_flow():
    """The scan list runs in ascending channel order and repeats; READ? answers without storing
    while INITiate stores into the memory for FETCh?/DATA:REMove?; a scan takes the relay
    settling plus the DMM reading time; the closed relay loads the source until high-Z removes
    the load."""
    s = session()
    try:
        # -- the list scans in ascending channel order and repeats it
        # (was test_the_scan_runs_in_ascending_order_and_repeats_the_list)
        assert q(s, "ROUT:SCAN (@103,101);:TRIG:COUN 2") is None
        assert q(s, "TRIG:COUN?") == "2"
        # The 34970A scans the list in ascending channel order, not in the order it was written.
        assert numbers(q(s, "READ?")) == pytest.approx(
            [LOADED_101, 4 * LOADED_101, LOADED_101, 4 * LOADED_101], rel=1e-4
        )
        # The stored list keeps the written order.
        assert q(s, "ROUT:SCAN?") == "(@103,101)"
        # -- bridge: the next act's INITiate must store one reading per scan, so the count is 1 again
        q(s, "TRIG:COUN 1")
        # -- READ? does not store, INITiate does (was test_read_does_not_store_readings_but_init_does)
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
        # -- the scan's time is the relay settling plus the DMM reading
        # (was test_the_scan_reading_time_is_the_relay_settling_plus_the_dmm_reading)
        assert q(s, "ROUT:SCAN (@101:103)") is None
        t0 = s.now_ns
        assert q(s, "READ?") is not None
        # Three readings at 1 PLC: 3 × (3 ms relay + 21 ms reading).
        expected = 3 * (RELAY_SETTLE_S + DMM_READING_S)
        assert (s.now_ns - t0) / 1e9 == pytest.approx(expected, abs=1e-3)
        # -- the relay closure loads the source, high-Z removes the load
        # (was test_the_relay_closure_loads_the_source_and_high_z_removes_the_load)
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


# ---- the bus-triggered scan: one flow (a fresh bench: the exact FETCh? strings below need an
# ---- empty reading memory) ---------------------------------------------------------------------------


def test_the_bus_trigger_flow():
    """With the BUS source each accepted *TRG runs the list once until the count is reached and
    further ones are ignored, READ? with a bus trigger is a deadlock, and ABORt stops a scan
    waiting for a trigger."""
    s = session()
    try:
        # -- each *TRG runs one scan until the count is reached
        # (was test_each_bus_trigger_runs_one_scan_until_the_count_is_reached)
        # #422: with the BUS source the trigger count is the number of *TRG accepted — each one
        # scans the list once; after the count, *TRG is ignored.
        assert q(s, "ROUT:SCAN (@101);:TRIG:SOUR BUS;:TRIG:COUN 2;:INIT") is None
        assert q(s, "*TRG") is None
        assert q(s, "SYST:ERR?") == '+0,"No error"'
        assert q(s, "*OPC?") == "1"
        assert q(s, "FETC?") == "+9.09090000E-01"
        assert q(s, "*TRG") is None
        assert q(s, "*OPC?") == "1"
        assert q(s, "FETC?") == "+9.09090000E-01,+9.09090000E-01"
        assert q(s, "*TRG") is None
        assert q(s, "SYST:ERR?") == '-211,"Trigger ignored"'
        # -- READ? with a bus trigger source is a trigger deadlock
        # (was test_read_with_a_bus_trigger_source_is_a_trigger_deadlock; the READ? refuses before
        # it initiates, so the exhausted model above stays idle and the queue is empty again)
        assert q(s, "TRIG:SOUR BUS") is None
        assert q(s, "READ?") is None
        assert q(s, "SYST:ERR?") == '-214,"Trigger deadlock"'
        # -- ABORt stops a scan waiting for a trigger (was test_abort_stops_a_scan_waiting_for_a_trigger)
        assert q(s, "ROUT:SCAN (@101);:TRIG:SOUR BUS;:INIT") is None
        assert q(s, "ABOR") is None
        assert q(s, "*TRG") is None
        assert q(s, "SYST:ERR?").startswith('-211,"Trigger ignored"')
    finally:
        s.close()


# ---- the channel domain, the reset defaults and the accepted-settings flow: one flow ---------------


def test_the_channel_domain_reset_and_settings_flow():
    """The channel numbers span the three declared slots with only slot 1 populated; *RST
    restores the manual defaults and opens the relays; display, SCPI version, an undefined
    header and the scan interval are accepted without changing the readings, which stay 8-digit
    NR3 numbers."""
    s = session()
    try:
        # -- the channel domain is the three slots, only slot 1 has a module
        # (was test_the_channel_domain_is_the_three_slots_but_only_slot_one_has_a_module)
        # 101 is a channel of the 34901A in slot 1; 201 exists as a number but slot 2 is empty;
        # 123 is not a declared channel at all (121/122 are the 34901A's current channels, #301);
        # 1 is an internal index, not a bus number.
        assert q(s, "ROUT:CLOS (@101)") is None
        assert q(s, "ROUT:CLOS? (@101)") == "1"
        for number in (201, 123, 1):
            assert q(s, f"ROUT:CLOS (@{number})") is None
            assert q(s, "SYST:ERR?").startswith('-114,"Header suffix out of range"')
        # A range may span slots and skips the numbers the numbering does not declare.
        assert q(s, "ROUT:SCAN (@101:205)") is None
        assert q(s, "ROUT:SCAN?") == "(@101:122,201:205)"
        # Running that scan reaches the empty slot and reports the missing channel.
        assert q(s, "READ?") is None
        assert q(s, "SYST:ERR?").startswith('-114,"Header suffix out of range"')
        # -- *RST restores the manual defaults and opens the relays
        # (was test_reset_restores_the_manual_defaults_and_opens_the_relays)
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
        # -- display and SCPI version are accepted without changing the reading
        # (was test_display_and_scpi_version_are_accepted_without_changing_the_reading)
        assert q(s, "DISP OFF;:DISP:TEXT 'SCANNING'") is None
        assert q(s, "DISP?") == "0"
        assert q(s, "DISP:TEXT?") == '"SCANNING"'
        assert q(s, "SYST:VERS?") == "1994.0"
        # The displayed text and the display state do not change a scan's readings.
        assert q(s, "ROUT:SCAN (@101)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([LOADED_101], rel=1e-5)
        assert q(s, "DISP:TEXT:CLEAR") is None
        assert q(s, "DISP:TEXT?") == '""'
        # -- an undefined command says not modeled in this definition
        # (was test_an_undefined_command_says_not_modeled_in_this_definition)
        assert q(s, "CONF:FREQ (@101)") is None  # frequency is not in this definition
        error = q(s, "SYST:ERR?")
        assert error.startswith('-113,"Undefined header;not implemented in this definition"'), error
        # -- the scan interval is stored but does not change the reading time yet
        # (was test_the_scan_interval_is_stored_but_does_not_change_the_reading_time_yet)
        assert q(s, "ROUT:SCAN (@101:102);:TRIG:SOUR TIMER;:TRIG:TIMER 5") is None
        assert q(s, "TRIG:TIMER?") == "+5.00000000E+00"
        t0 = s.now_ns
        assert q(s, "READ?") is not None
        # First-wave debt (scan_table): the interval is a setting, the readings take
        # relay settling + DMM reading time only.
        expected = 2 * (RELAY_SETTLE_S + DMM_READING_S)
        assert (s.now_ns - t0) / 1e9 == pytest.approx(expected, abs=1e-3)
        # -- readings are eight digit NR3 numbers (was test_readings_are_eight_digit_nr3_numbers)
        assert q(s, "ROUT:SCAN (@101)") is None
        reply = q(s, "READ?")
        assert reply == "+9.09090000E-01", reply
        assert math.isclose(float(reply), LOADED_101, rel_tol=1e-5)
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


# ---- resistance and current from the scan card's bus (#301) -------------------------------------


def _bench(parts: dict, wires: list):
    from simubench.models.bench import BenchDefinition

    return BenchDefinition.model_validate(
        {
            "schema": "simubench.bench/2",
            "name": "daq-301",
            "instruments": {"daq": {"model": "keysight-34970a"}},
            "parts": {"v0": {"type": "dc_supply", "params": {"v": 0.0, "r": 1.0e-6}}, **parts},
            "wires": wires,
            "run": {"clock": "command", "idle": "off"},
        }
    )


def _resistance_bench():
    """A 1 kΩ DUT to ground behind a 10 Ω lead on channel 101; channel 111 senses at the DUT."""
    return _bench(
        {
            "lead": {"type": "resistor", "params": {"r": 10.0}},
            "dut": {"type": "load_resistor", "params": {"r": 1000.0}},
        },
        [
            {"from": "daq.H101", "to": "lead.A"},
            {"from": "lead.B", "to": "dut.A"},
            {"from": "daq.L101", "to": "v0.OUT"},
            {"from": "daq.H111", "to": "dut.A"},
            {"from": "daq.L111", "to": "v0.OUT"},
        ],
    )


# ---- resistance from the scan card's bus: one flow (#301) ------------------------------------------


def test_the_resistance_measurement_flow():
    """Two-wire resistance reads the lead and both relay contacts, four-wire pairs channel n
    with n+10 and senses at the DUT, and an open channel or a too-small range reads overload."""
    s = open_bench(_resistance_bench())
    try:
        # -- two-wire resistance reads the lead and both relays
        # (was test_two_wire_resistance_reads_the_lead_and_both_relays)
        # 1 kΩ + the 10 Ω lead + two 0.5 Ω relay contacts: the test current flows through all.
        assert numbers(q(s, "MEAS:RES? (@101)")) == pytest.approx([1011.0], rel=1e-6)
        assert q(s, "SYST:ERR?") == '+0,"No error"'
        # -- four-wire pairs channel n with n+10 and senses at the DUT
        # (was test_four_wire_pairs_channel_n_with_n_plus_ten_and_senses_at_the_dut; the error
        # above was read out, so the queue is empty and -221 is the first entry again)
        assert numbers(q(s, "MEAS:FRES? (@101)")) == pytest.approx([1000.0], rel=1e-6)
        # Channel 11 is a sense channel: it has no pair of its own.
        assert q(s, "MEAS:FRES? (@111)") is None
        assert q(s, "SYST:ERR?") == '-221,"Settings conflict"'
        # -- an open channel and a too-small range read overload
        # (was test_an_open_channel_and_a_too_small_range_read_overload)
        assert numbers(q(s, "MEAS:RES? (@102)")) == pytest.approx([9.9e37])  # nothing on 102
        assert numbers(q(s, "MEAS:RES? 100,(@101)")) == pytest.approx([9.9e37])  # 1011 Ω on 100 Ω
    finally:
        s.close()


def test_current_is_measured_on_the_current_channels_through_the_shunt():
    """5 V behind 1 kΩ into channel 121: the 5 Ω shunt (10 mA range) and both relays are in the loop."""
    bench = _bench(
        {"src": {"type": "dc_supply", "params": {"v": 5.0, "r": 1000.0}}},
        [{"from": "src.OUT", "to": "daq.I121"}, {"from": "daq.L121", "to": "v0.OUT"}],
    )
    s = open_bench(bench)
    try:
        # 1 PLC: 3 ppm resolution.
        assert numbers(q(s, "MEAS:CURR? (@121)")) == pytest.approx([5.0 / 1006.0], rel=1e-5)
        # Current only on 21/22; the other functions not on them.
        assert q(s, "MEAS:CURR? (@101)") is None
        assert q(s, "SYST:ERR?") == '-221,"Settings conflict"'
        assert q(s, "MEAS:VOLT? (@121)") is None
        assert q(s, "SYST:ERR?") == '-221,"Settings conflict"'
    finally:
        s.close()


def test_configure_resistance_then_scan_two_channels():
    bench = _bench(
        {
            "lead": {"type": "resistor", "params": {"r": 10.0}},
            "dut": {"type": "load_resistor", "params": {"r": 1000.0}},
            "r2": {"type": "load_resistor", "params": {"r": 47000.0}},
        },
        [
            {"from": "daq.H101", "to": "lead.A"},
            {"from": "lead.B", "to": "dut.A"},
            {"from": "daq.L101", "to": "v0.OUT"},
            {"from": "daq.H102", "to": "r2.A"},
            {"from": "daq.L102", "to": "v0.OUT"},
        ],
    )
    s = open_bench(bench)
    try:
        assert q(s, "CONF:RES (@101,102)") is None
        assert q(s, "ROUT:SCAN?") == "(@101:102)"
        assert numbers(q(s, "READ?")) == pytest.approx([1011.0, 47001.0], rel=1e-6)
        # Back to DC volts: the bus carries the 10 MΩ input again, no test current.
        assert q(s, "CONF:VOLT:DC (@101)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([0.0], abs=1e-6)
    finally:
        s.close()


# ---- per-channel measurement configuration (#365) ------------------------------------------------


def test_one_scan_mixes_resistance_and_voltage_channels():
    """The #365 reproduction: CONF:VOLT on 102 must not turn 101's resistance into a voltage."""
    bench = _bench(
        {
            "lead": {"type": "resistor", "params": {"r": 10.0}},
            "dut": {"type": "load_resistor", "params": {"r": 1000.0}},
            "v2": {"type": "dc_supply", "params": {"v": 2.5, "r": 1.0e-6}},
        },
        [
            {"from": "daq.H101", "to": "lead.A"},
            {"from": "lead.B", "to": "dut.A"},
            {"from": "daq.L101", "to": "v0.OUT"},
            {"from": "daq.H111", "to": "dut.A"},
            {"from": "daq.L111", "to": "v0.OUT"},
            {"from": "daq.H102", "to": "v2.OUT"},
            {"from": "daq.L102", "to": "v0.OUT"},
        ],
    )
    s = open_bench(bench)
    try:
        assert q(s, "CONF:RES (@101)") is None
        assert q(s, "CONF:VOLT:DC (@102)") is None
        assert q(s, "ROUT:SCAN (@101,102)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([1011.0, 2.5], rel=1e-5)
        assert q(s, "CONF? (@101)").startswith('"RES ')
        assert q(s, "CONF? (@102)").startswith('"VOLT ')
        # Four-wire on 101 (sense on 111) in the same scan as the voltage on 102: the sense bus is
        # switched for 101 only, and the test current is off while 102 is read.
        assert q(s, "CONF:FRES (@101)") is None
        assert q(s, "ROUT:SCAN (@101,102)") is None
        assert numbers(q(s, "READ?")) == pytest.approx([1000.0, 2.5], rel=1e-5)
        # A per-channel setting stays with its channel.
        assert q(s, "SENS:VOLT:DC:NPLC 10,(@102)") is None
        assert q(s, "SENS:VOLT:DC:NPLC? (@101,102)") == "+1.00000000E+00,+1.00000000E+01"
        assert q(s, "MEAS:VOLT:DC? (@102)") is not None
        assert q(s, "CONF? (@101,102)").count('"') == 4  # two quoted entries, list order
        assert q(s, "SYST:ERR?") == '+0,"No error"'
    finally:
        s.close()


def test_a_scan_takes_each_channels_own_reading_time():
    """101 at 1 PLC (21 ms) and 102 at 10 PLC (201 ms), each after the 3 ms relay settling."""
    s = session()
    try:
        assert q(s, "SENS:VOLT:DC:NPLC 10,(@102);:ROUT:SCAN (@101,102)") is None
        t0 = s.now_ns
        assert len(numbers(q(s, "READ?"))) == 2
        expected = 2 * RELAY_SETTLE_S + DMM_READING_S + 0.201
        assert (s.now_ns - t0) / 1e9 == pytest.approx(expected, abs=1e-3)
    finally:
        s.close()
