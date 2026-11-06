"""calibrator_output, the quantities parameter type and the declared error query (#90,
metrology-authoring.md 5): operate/standby, settling, overload to standby, per-unit parameters.

Flow tests per D49/#704: one test walks one flow, each ``# --`` act preserves an original
test's body verbatim (its own bench open when its scenario needs a fresh one); acts that are
provably state-compatible share one bench, with the bridging commands noted.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from helpers import session_query
from simubench.api import open_bench
from simubench.engine.component import component_types
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition

DEFS = Path(__file__).parent / "definitions"
DEF_FILE = DEFS / "test-calibrator.yaml"
SETTLE_DCV = 0.004  # s, the test definition's settling time per function
SETTLE_ACV = 0.01
METER_Z = 10.0e6  # the test meter's input resistance


def bench(**hidden: dict) -> BenchDefinition:
    data = {
        "name": "cal",
        "instruments": {
            "cal": {"model": "test-calibrator"},
            "dmm": {"model": "test-trigger-meter"},
        },
        "wires": [{"from": "cal.OUT", "to": "dmm.IN"}],
        "run": {"clock": "command", "idle": "off"},
    }
    if hidden:
        data["instruments"]["cal"]["hidden"] = {"out": dict(hidden)}
    return BenchDefinition.model_validate(data)


def loaded_bench(load_ohm: float, **hidden: dict) -> BenchDefinition:
    data = bench(**hidden).model_dump(by_alias=True)
    data["parts"] = {"load": {"type": "load_resistor", "params": {"r": load_ohm}}}
    data["wires"].append({"from": "cal.OUT", "to": "load.A"})
    return BenchDefinition.model_validate(data)


@pytest.fixture
def s():
    session = open_bench(bench(), definition_dirs=[DEFS])
    yield session
    session.close()


def q(s, cmd: str, inst: str = "cal") -> str | None:
    return session_query(s, inst, cmd)


def reading(s, function: str = "DC") -> float:
    """One multimeter reading, taken at the instant the command arrives."""
    reply = q(s, f":MEAS:VOLT:{function}?", "dmm")
    assert reply, reply
    return float(reply)


def err(s) -> str:
    reply = q(s, "ERR?")
    assert reply, reply
    return reply


# ---- operate and standby: one flow across function selection ------------------------------------------


def test_the_operate_standby_flow_across_function_selections(s):
    """Standby reads zero, the setting alone drives nothing, operate settles to the value;
    the function comes from a frequency (AC), a bare voltage (DC) or 0 Hz (DC) (#90)."""
    # -- standby and operate (was test_operate_reaches_the_setting_and_standby_reads_zero)
    assert reading(s) == 0.0  # standby: the terminal is out of the circuit
    q(s, "OUT 1 V")
    assert reading(s) == 0.0  # still standby: the setting alone drives nothing
    q(s, "OPER")
    assert q(s, "*OPC?") == "1"
    assert reading(s) == pytest.approx(1.0, abs=1e-9)
    q(s, "STBY")
    assert reading(s) == 0.0
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(1.0, abs=1e-9)  # entering operate settles from 0 again
    # -- a frequency makes the line AC (was test_out_with_a_frequency_is_ac_and_with_zero_hz_dc)
    q(s, "STBY")
    q(s, "OUT 1 V, 1 kHz")
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "AC") == pytest.approx(1.0, abs=1e-9)  # one line of 1 V RMS
    # -- a bare voltage switches the line back to DC (was test_a_bare_voltage_write_switches_back_to_dc)
    q(s, "OUT 2 V")  # the combination V alone is DCV
    q(s, "*OPC?")
    assert reading(s, "DC") == pytest.approx(2.0, abs=1e-9)  # the line is gone
    # -- 0 Hz is DC (the second half of test_out_with_a_frequency_is_ac_and_with_zero_hz_dc)
    q(s, "STBY")
    q(s, "OUT 2 V, 0 HZ")  # 0 Hz is DC
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "DC") == pytest.approx(2.0, abs=1e-9)


# ---- settling: the ESR bit, the OPC/WAI timeline, the mid-settle reading -------------------------------


def test_the_settling_timeline(s):
    """*OPC marks the settling done on a fresh bench; OPERate returns at once, *OPC?/*WAI wait
    the function's settling time, a reading mid-settle sits between old and new; ACV settles on
    its own table row."""
    # -- the ESR bit 0 marks the settling (was test_opc_sets_esr_bit_zero_once_settled; first act:
    # no completed settle may be behind it, which the fresh fixture bench guarantees)
    q(s, "OUT 1 V")
    q(s, "OPER")
    q(s, "*OPC")
    assert q(s, "*ESR?") == "0"  # the settling is not done yet
    s.advance(SETTLE_DCV)
    assert q(s, "*ESR?") == "1"
    # -- OPERate returns at once, the mid-settle reading, OPC? waits the rest
    # (was test_opc_waits_for_the_settling_time + test_reading_mid_settle_is_between_old_and_new)
    q(s, "STBY")
    q(s, "OUT 1 V")
    t0 = s.now_ns
    q(s, "OPER")
    assert s.now_ns == t0  # OPERate itself returns at once
    s.advance(SETTLE_DCV / 2)
    tau = SETTLE_DCV / 5
    assert reading(s) == pytest.approx(1.0 - math.exp(-SETTLE_DCV / 2 / tau), abs=1e-6)
    assert q(s, "*OPC?") == "1"
    assert s.now_ns - t0 == round(SETTLE_DCV * 1e9)  # OPC waited out the whole settling time
    # -- ACV settles per its own table row (the second half of test_opc_waits_for_the_settling_time)
    q(s, "STBY")
    q(s, "OUT 1 V, 1 kHz")
    t0 = s.now_ns
    q(s, "OPER")
    q(s, "*WAI")
    assert s.now_ns - t0 == round(SETTLE_ACV * 1e9)  # the table gives ACV its own settling time


def test_settle_zero_reaches_the_value_at_once(s):
    s.close()
    session = open_bench(bench(settle=0.0), definition_dirs=[DEFS])
    q(session, "OUT 1 V")
    t0 = session.now_ns
    q(session, "OPER")
    assert q(session, "*OPC?") == "1"
    assert session.now_ns == t0  # nothing to wait for
    assert reading(session) == pytest.approx(1.0, abs=1e-9)
    session.close()


# ---- overload: the error-queue counts are absolute per bench, so the three keep their own benches -----


def test_overload_trips_to_standby_and_reports(s):
    s.close()
    session = open_bench(loaded_bench(10.0), definition_dirs=[DEFS])  # 1 V into 10 ohm: 100 mA
    q(session, "OUT 1 V")
    q(session, "OPER")
    session.advance(SETTLE_DCV)  # the output is up: 100 mA against the 50 mA capability
    assert reading(session) == 0.0  # the trip took the terminal out of the circuit
    assert err(session) == '21,"Excess current"'
    out = session.instruments["cal"].components["out"]
    assert out.readings()["values"]["tripped"] is True
    assert out.readings()["values"]["output"] == "STBY"
    assert q(session, "ERR:COUNt?") == "0"  # the entry was read
    session.close()


def test_overload_trips_again_after_operate(s):
    s.close()
    session = open_bench(loaded_bench(10.0), definition_dirs=[DEFS])
    q(session, "OUT 1 V")
    for expected in (1, 2):
        q(session, "OPER")
        session.advance(SETTLE_DCV)
        assert reading(session) == 0.0
        assert q(session, "ERR:COUNt?") == str(expected)
    session.close()


def test_a_load_within_the_capability_does_not_trip(s):
    s.close()
    session = open_bench(loaded_bench(1000.0), definition_dirs=[DEFS])  # 1 mA
    q(session, "OUT 1 V")
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session) == pytest.approx(1.0, abs=1e-3)  # 1000 ohm against the 10 Mohm meter
    assert q(session, "ERR:COUNt?") == "0"
    session.close()


# ---- output impedance -------------------------------------------------------------------------------


def test_z_out_divides_against_the_load(s):
    s.close()
    session = open_bench(loaded_bench(1000.0, **{"z_out": 1000.0}), definition_dirs=[DEFS])
    q(session, "OUT 1 V")
    q(session, "OPER")
    q(session, "*OPC?")
    rl = 1000.0 * METER_Z / (1000.0 + METER_Z)  # the meter loads the divider
    assert reading(session) == pytest.approx(rl / (1000.0 + rl), abs=1e-6)  # 7 reply digits
    session.close()


# ---- the high-voltage confirmation and the standby rule: one flow --------------------------------------


def test_the_standby_above_rule_flow(s):
    """One flow on the 22 V interlock: an above-threshold change returns to standby until
    OPERate confirms; a below-threshold change settles; a high setting stores in standby; the
    either rule is strict exactly at the threshold (#553, #558)."""
    # -- above -> standby, confirm; the old above -> standby even though the new is safe
    # (was test_standby_above_returns_to_standby)
    q(s, "OUT 10 V")
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(10.0, abs=1e-9)
    q(s, "OUT 30 V")  # the new setting is above 22 V: back to standby
    assert reading(s) == 0.0
    assert q(s, "ERR:COUNt?") == "0"  # the interlock is not an error
    q(s, "OPER")  # the confirmation
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(30.0, abs=1e-9)
    q(s, "OUT 5 V")  # the old setting was above 22 V: back to standby even though 5 V is safe
    assert reading(s) == 0.0
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(5.0, abs=1e-9)
    # -- a change below the threshold settles (was test_a_change_below_the_threshold_does_not_return_to_standby)
    q(s, "OUT 10 V;*WAI")  # bridge: operating at 10 V again (5 -> 10 stays below the threshold)
    q(s, "OUT 5 V")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(5.0, abs=1e-9)
    # -- a high setting stores in standby (was test_the_high_setting_can_be_stored_in_standby)
    q(s, "STBY")
    q(s, "OUT 30 V")  # in standby the interlock does not apply
    assert reading(s) == 0.0
    q(s, "OPER")  # entering operate is the confirmation
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(30.0, abs=1e-9)
    # -- the either rule: exactly at the threshold is not above
    # (was test_either_rule_is_strict_at_the_threshold)
    q(s, "STBY")
    q(s, "OUT 10 V")
    q(s, "OPER")
    q(s, "*OPC?")
    q(s, "OUT 22 V;*WAI")  # 10 -> 22, exactly at the threshold: neither side is above it
    assert reading(s) == pytest.approx(22.0, abs=1e-9)
    q(s, "OUT 22.1 V")  # the new setting is above: standby
    assert reading(s) == 0.0
    assert q(s, "ERR:COUNt?") == "0"  # the rule is not an error
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(22.1, abs=1e-9)
    q(s, "OUT 5 V")  # the old setting is above: standby even though 5 V is safe
    assert reading(s) == 0.0
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(5.0, abs=1e-9)


# ---- the crossing standby rule: one flow (#553, #558) ---------------------------------------------------


def test_the_crossing_standby_rule():
    """5730A/5522A Operators Manual: the crossing rule trips only when the magnitude crosses the
    threshold upward; the boundary itself counts as crossing; negative levels judge by
    magnitude (#558). One flow on one bench."""
    session = open_bench(crossing_session(), definition_dirs=[DEFS])
    # -- trips only when crossing upward (was test_crossing_rule_trips_only_when_crossing_upward)
    q(session, "OUT 25 V")  # already above the threshold
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session) == pytest.approx(25.0, abs=1e-9)
    q(session, "OUT 30 V;*WAI")  # 25 -> 30: both sides above, no crossing
    assert reading(session) == pytest.approx(30.0, abs=1e-9)
    q(session, "OUT 10 V;*WAI")  # downward: never trips
    assert reading(session) == pytest.approx(10.0, abs=1e-9)
    q(session, "OUT 25 V")  # 10 -> 25 crosses the threshold upward: back to standby
    assert reading(session) == 0.0
    assert q(session, "ERR:COUNt?") == "0"
    q(session, "OPER")  # the explicit confirmation
    q(session, "*OPC?")
    assert reading(session) == pytest.approx(25.0, abs=1e-9)
    # -- a setting exactly at the threshold counts as crossing
    # (was test_crossing_rule_includes_a_setting_exactly_at_the_threshold; bridge to 10 V first)
    q(session, "OUT 10 V;*WAI")  # bridge: operating at 10 V
    q(session, "OUT 22 V")  # the new setting equals the threshold exactly
    assert reading(session) == 0.0
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session) == pytest.approx(22.0, abs=1e-9)
    # -- negative levels judge by magnitude (was test_crossing_rule_judges_negative_levels_by_magnitude)
    q(session, "OUT 5 V;*WAI")  # bridge: operating at 5 V
    q(session, "OUT -22 V")  # |−22 V| = 22 V crosses the threshold upward from 5 V
    assert reading(session) == 0.0
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session) == pytest.approx(-22.0, abs=1e-9)
    q(session, "OUT -30 V;*WAI")  # 22 -> 30 in magnitude: no crossing
    assert reading(session) == pytest.approx(-30.0, abs=1e-9)
    session.close()


def crossing_session() -> BenchDefinition:
    return bench(**{"standby_rule": "crossing"})


# ---- the standby-on-function flag: one flow (#553, #558) -------------------------------------------------


def test_the_standby_on_function_flag():
    """With the flag, a function change while operating returns to standby regardless of the
    level; in standby the setting only stores, and a frequency change with the function
    unchanged does not trip. One flow on one bench."""
    session = open_bench(on_function_session(), definition_dirs=[DEFS])
    # -- trips on a function change (was test_standby_on_function_trips_on_a_function_change, #553)
    q(session, "OUT 10 V")
    q(session, "OPER")
    q(session, "*OPC?")
    q(session, "OUT 10 V, 1 kHz")  # DCV -> ACV while operating, below the threshold
    assert reading(session) == 0.0
    assert q(session, "ERR:COUNt?") == "0"  # not an error
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session, "AC") == pytest.approx(10.0, abs=1e-9)
    # -- standby stores, frequency changes alone
    # (was test_standby_on_function_leaves_standby_and_frequency_changes_alone, #558)
    q(session, "STBY")  # bridge: the next act's scenario starts in standby
    q(session, "OUT 10 V, 1 kHz")  # in standby: no rule applies, the function is only stored
    assert q(session, "ERR:COUNt?") == "0"
    assert reading(session) == 0.0
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session, "AC") == pytest.approx(10.0, abs=1e-9)  # operate confirms the function
    q(session, "OUT 10 V, 2 kHz;*WAI")  # still ACV: only the frequency moved
    assert reading(session, "AC") == pytest.approx(10.0, abs=1e-9)
    session.close()


def on_function_session() -> BenchDefinition:
    return bench(**{"standby_on_function": True})


def test_standby_on_function_above_keeps_a_low_voltage_dc_ac_switch_operating():
    """#566: with ``above`` a DCV <-> ACV change below ``standby_above`` (22 V here) keeps operating
    and settles to the new function; at or above it the change returns to standby."""
    session = open_bench(bench(**{"standby_on_function": "above"}), definition_dirs=[DEFS])
    q(session, "OUT 10 V")
    q(session, "OPER")
    q(session, "*OPC?")
    q(session, "OUT 10 V, 1 kHz;*WAI")  # below the threshold: still operating
    assert reading(session, "AC") == pytest.approx(10.0, abs=1e-9)
    q(session, "OUT 10 V, 0 Hz;*WAI")  # and back to DC
    assert reading(session) == pytest.approx(10.0, abs=1e-9)
    q(session, "OUT 22 V;*WAI")  # the either rule: 22 V is not above 22 V, still operating
    assert reading(session) == pytest.approx(22.0, abs=1e-9)
    q(session, "OUT 22 V, 1 kHz")  # at the threshold: the function change returns to standby
    assert reading(session) == 0.0
    assert reading(session, "AC") == 0.0
    assert q(session, "ERR:COUNt?") == "0"
    session.close()


# ---- quantities and the error query: one flow -----------------------------------------------------------


def test_the_quantities_and_error_query_flow(s):
    """Units write and read back (order-free, M/m prefixes), broken combinations are refused
    with their codes and leave the setting untouched, and the error queue counts and reads out
    (#90, #119)."""
    # -- units write their settings (was test_units_write_their_settings_and_the_reply_shows_them)
    q(s, "OUT 10 mV, 1 kHz")
    assert q(s, "OUT?") == "+1.000000E-02,V,+1.000000E+03,HZ"
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "AC") == pytest.approx(0.01, abs=1e-9)  # 10 mV RMS at 1 kHz
    # -- unit order does not matter (was test_unit_order_does_not_matter)
    q(s, "STBY")
    q(s, "OUT 1 kHz, 1 V")
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "AC") == pytest.approx(1.0, abs=1e-9)
    assert q(s, "OUT?") == "+1.000000E+00,V,+1.000000E+03,HZ"
    # -- broken quantities are refused (was test_quantities_errors)
    q(s, "STBY")
    q(s, "OUT 1 V, 0 HZ")  # bridge: the fresh-bench baseline (the previous act left 1 kHz)
    cases = [
        ("OUT 1", '22,"Value requires a unit"'),
        ("OUT 1 W", '23,"Unit not usable here"'),
        ("OUT 1 V, 2 V", '24,"Unit given twice"'),
        ("OUT 1 kHz", '25,"Combination not allowed"'),
    ]
    for command, reply in cases:
        assert q(s, command) is None
        assert err(s) == reply
        assert q(s, "OUT?") == "+1.000000E+00,V,+0.000000E+00,HZ"  # nothing was written
    # -- M and m prefixes (was test_mega_and_milli_suffixes)
    q(s, "OUT 3 MHZ, 20 mV")
    assert q(s, "OUT?") == "+2.000000E-02,V,+3.000000E+06,HZ"
    # -- the error queue counts and reads out (was test_next_error_and_error_count)
    assert q(s, "ERR:COUNt?") == "0"
    assert err(s) == '0,"No error"'
    q(s, "OUT 1 V")
    assert q(s, "OUT 1") is None  # a command error
    assert q(s, "ERR:COUNt?") == "1"
    assert err(s) == '22,"Value requires a unit"'
    assert q(s, "ERR:COUNt?") == "0"


def test_hidden_errors_are_the_truth_behind_a_calibration(s):
    s.close()
    session = open_bench(bench(**{"gain_error_ppm": 5.0, "offset": 2.0e-6}), definition_dirs=[DEFS])
    q(session, "OUT 1 V")
    q(session, "OPER")
    q(session, "*OPC?")
    assert reading(session) == pytest.approx(1.0 * (1 + 5e-6) + 2e-6, rel=1e-9)
    session.close()


# ---- parameterless commands: one flow (#119) ------------------------------------------------------------


def test_the_parameterless_commands_flow(s):
    """OPER? reads what OPER/STBY write; REMOTE/LOCKOUT/LOCAL write the interface state."""
    # -- OPER? (was test_operate_query_reads_the_setting_the_set_form_writes)
    assert q(s, "OPER?") == "0"
    q(s, "OPER")
    assert q(s, "OPER?") == "1"
    q(s, "STBY")
    assert q(s, "OPER?") == "0"
    # -- interface state (was test_parameterless_commands_write_the_interface_state)
    cal = s.instrument("cal")
    for command, expected in (("REMOTE", "REMOTE"), ("LOCKOUT", "LOCKOUT"), ("LOCAL", "LOCAL")):
        q(s, command)
        assert cal.state["interface"] == expected


def test_the_calibrator_is_in_the_component_catalog():
    cls = component_types()["calibrator_output"]
    assert cls.role == "driver"
    assert cls.piecewise is True


# ---- static validation of the quantities table --------------------------------------------------------


def load_def() -> dict:
    return yaml.safe_load(DEF_FILE.read_text(encoding="utf-8"))


def codes(data: dict) -> set[str]:
    report = validate_definition_data(copy.deepcopy(data))[1]
    return {d.code for d in report.diagnostics if d.severity == "error"}


def test_the_test_definition_validates_clean():
    assert codes(load_def()) == set()


def test_unknown_states_in_the_units_table():
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    out["params"][0]["units"]["V"]["state"] = "out.nothing"
    out["params"][0]["units"]["V"]["sets"] = {"out.nope": "V"}
    report = validate_definition_data(data)[1]
    problems = [d for d in report.diagnostics if d.severity == "error"]
    assert {d.code for d in problems} == {"E-DEF-STATE-UNKNOWN"}
    assert any("/units/V/state" in d.pointer for d in problems)
    assert any("/units/V/sets/out.nope" in d.pointer for d in problems)


def test_unknown_unit_and_value_in_the_combine_table():
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    out["params"][0]["combine"]["V,W"] = {"out.function": "DCV"}
    out["params"][0]["combine"]["V,HZ"]["out.function"] = "DCX"
    report = validate_definition_data(data)[1]
    problems = [d for d in report.diagnostics if d.severity == "error"]
    assert {d.code for d in problems} == {"E-DEF-QUANTITY"}
    assert any("W" in d.message for d in problems)
    assert any("DCX" in d.message for d in problems)


def test_unknown_if_zero_state():
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    out["params"][0]["combine"]["V,HZ"]["out.function"] = {
        "if_zero": "out.nope",
        "then": "DCV",
        "else": "ACV",
    }
    report = validate_definition_data(data)[1]
    assert {d.code for d in report.diagnostics if d.severity == "error"} == {"E-DEF-STATE-UNKNOWN"}


def test_schema_rejects_broken_quantities_commands():
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    del out["params"][0]["units"]
    assert "E-SCHEMA" in codes(data)
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    out["op"] = "out.operate"
    assert "E-SCHEMA" in codes(data)
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    del out["reply"]
    assert "E-SCHEMA" in codes(data)


def test_reply_template_must_name_declared_states():
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    out["reply"] = "{out.nope},V"
    report = validate_definition_data(data)[1]
    assert {d.code for d in report.diagnostics if d.severity == "error"} == {"E-DEF-REPLY"}


# ---- parameterless validation (#119) ------------------------------------------------------------------


def test_read_must_name_an_existing_setting():
    data = load_def()
    oper = next(c for c in data["commands"] if c["header"] == "OPER")
    oper["read"] = "out.nothing"
    assert codes(data) == {"E-DEF-STATE-UNKNOWN"}


def test_read_and_set_value_go_with_their_command_kinds():
    from simubench.models.definition import InstrumentDefinition

    data = load_def()
    local = next(c for c in data["commands"] if c["header"] == "LOCAL")
    local["read"] = "interface"
    with pytest.raises(ValueError, match="read"):
        InstrumentDefinition.model_validate(data)
    data = load_def()
    oper = next(c for c in data["commands"] if c["header"] == "OPER")
    oper["set_value"] = "x"
    with pytest.raises(ValueError, match="set_value"):
        InstrumentDefinition.model_validate(data)
    data = load_def()
    out = next(c for c in data["commands"] if c["header"] == "OUT")
    out["set_value"] = "V"
    with pytest.raises(ValueError, match="set_value"):
        InstrumentDefinition.model_validate(data)
