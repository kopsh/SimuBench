"""calibrator_output, the quantities parameter type and the declared error query (#90,
metrology-authoring.md 5): operate/standby, settling, overload to standby, per-unit parameters."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

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
    s.write(inst, (cmd + "\n").encode())
    out = s.read(inst)
    return out.decode().strip() if out else None


def reading(s, function: str = "DC") -> float:
    """One multimeter reading, taken at the instant the command arrives."""
    reply = q(s, f":MEAS:VOLT:{function}?", "dmm")
    assert reply, reply
    return float(reply)


def err(s) -> str:
    reply = q(s, "ERR?")
    assert reply, reply
    return reply


# ---- operate and standby ---------------------------------------------------------------------------


def test_operate_reaches_the_setting_and_standby_reads_zero(s):
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


def test_out_with_a_frequency_is_ac_and_with_zero_hz_dc(s):
    q(s, "OUT 1 V, 1 kHz")
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "AC") == pytest.approx(1.0, abs=1e-9)  # one line of 1 V RMS
    q(s, "STBY")
    q(s, "OUT 2 V, 0 HZ")  # 0 Hz is DC
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "DC") == pytest.approx(2.0, abs=1e-9)


def test_a_bare_voltage_write_switches_back_to_dc(s):
    q(s, "OUT 1 V, 1 kHz")
    q(s, "OPER")
    q(s, "*OPC?")
    q(s, "OUT 2 V")  # the combination V alone is DCV
    q(s, "*OPC?")
    assert reading(s, "DC") == pytest.approx(2.0, abs=1e-9)  # the line is gone


# ---- settling ---------------------------------------------------------------------------------------


def test_opc_waits_for_the_settling_time(s):
    q(s, "OUT 1 V")
    t0 = s.now_ns
    q(s, "OPER")
    assert s.now_ns == t0  # OPERate itself returns at once
    assert q(s, "*OPC?") == "1"
    assert s.now_ns - t0 == round(SETTLE_DCV * 1e9)
    q(s, "STBY")
    q(s, "OUT 1 V, 1 kHz")
    t0 = s.now_ns
    q(s, "OPER")
    q(s, "*WAI")
    assert s.now_ns - t0 == round(SETTLE_ACV * 1e9)  # the table gives ACV its own settling time


def test_opc_sets_esr_bit_zero_once_settled(s):
    q(s, "OUT 1 V")
    q(s, "OPER")
    q(s, "*OPC")
    assert q(s, "*ESR?") == "0"  # the settling is not done yet
    s.advance(SETTLE_DCV)
    assert q(s, "*ESR?") == "1"


def test_reading_mid_settle_is_between_old_and_new(s):
    q(s, "OUT 1 V")
    q(s, "OPER")
    s.advance(SETTLE_DCV / 2)
    tau = SETTLE_DCV / 5
    assert reading(s) == pytest.approx(1.0 - math.exp(-SETTLE_DCV / 2 / tau), abs=1e-6)


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


# ---- overload ---------------------------------------------------------------------------------------


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


# ---- high-voltage confirmation ----------------------------------------------------------------------


def test_standby_above_returns_to_standby(s):
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


def test_a_change_below_the_threshold_does_not_return_to_standby(s):
    q(s, "OUT 10 V")
    q(s, "OPER")
    q(s, "*OPC?")
    q(s, "OUT 5 V")
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(5.0, abs=1e-9)


def test_the_high_setting_can_be_stored_in_standby(s):
    q(s, "OUT 30 V")  # in standby the interlock does not apply
    assert reading(s) == 0.0
    q(s, "OPER")  # entering operate is the confirmation
    q(s, "*OPC?")
    assert reading(s) == pytest.approx(30.0, abs=1e-9)


# ---- quantities -------------------------------------------------------------------------------------


def test_units_write_their_settings_and_the_reply_shows_them(s):
    q(s, "OUT 10 mV, 1 kHz")
    assert q(s, "OUT?") == "+1.000000E-02,V,+1.000000E+03,HZ"
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "AC") == pytest.approx(0.01, abs=1e-9)  # 10 mV RMS at 1 kHz


def test_unit_order_does_not_matter(s):
    q(s, "OUT 1 kHz, 1 V")
    q(s, "OPER")
    q(s, "*OPC?")
    assert reading(s, "AC") == pytest.approx(1.0, abs=1e-9)
    assert q(s, "OUT?") == "+1.000000E+00,V,+1.000000E+03,HZ"


def test_quantities_errors(s):
    q(s, "OUT 1 V")
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


def test_mega_and_milli_suffixes(s):
    q(s, "OUT 3 MHZ, 20 mV")
    assert q(s, "OUT?") == "+2.000000E-02,V,+3.000000E+06,HZ"


# ---- the error query --------------------------------------------------------------------------------


def test_next_error_and_error_count(s):
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


def test_the_calibrator_is_in_the_component_catalog():
    cls = component_types()["calibrator_output"]
    assert cls.role == "driver"
    assert cls.piecewise is True


# ---- static validation of the quantities table --------------------------------------------------------


def load_def() -> dict:
    with DEF_FILE.open() as f:
        return yaml.safe_load(f)


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
