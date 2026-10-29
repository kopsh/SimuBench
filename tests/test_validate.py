import copy

import pytest

from simubench.engine.validate import validate_data
from simubench.library.loader import DefinitionLibrary

LIB = DefinitionLibrary()

BASE = {
    "schema": "simubench.bench/2",
    "name": "t",
    "instruments": {"awg": {"model": "sb-awg-2"}, "scope": {"model": "sb-dso-4"}},
    "parts": {"rc": {"type": "rc_lowpass", "dut": True, "params": {"r": 1000, "c": 1e-7}}},
    "wires": [
        {"from": "awg.OUT1", "to": "rc.IN"},
        {"from": "rc.OUT", "to": "scope.CH1", "accessories": [{"type": "passive_probe"}]},
    ],
}


def codes(data):
    _, report = validate_data(data, LIB)
    return [(d.code, d.pointer) for d in report.diagnostics], report


def test_valid_bench_has_only_the_impedance_hint():
    found, report = codes(BASE)
    assert report.ok
    assert found == [("W-IMPEDANCE-MISMATCH", "/instruments/awg/state/ch1.load")]


def test_high_z_setting_clears_the_hint():
    data = copy.deepcopy(BASE)
    data["instruments"]["awg"]["state"] = {"ch1.load": "INF"}
    found, _ = codes(data)
    assert found == []


@pytest.mark.parametrize(
    ("mutate", "code", "pointer", "suggestion"),
    [
        (
            lambda d: d["instruments"]["awg"].update(model="sb-awg-3"),
            "E-MODEL-UNKNOWN",
            "/instruments/awg/model",
            "sb-awg-2",
        ),
        (lambda d: d["wires"][0].update(to="rc.INN"), "E-WIRE-PORT", "/wires/0/to", "rc.IN"),
        (lambda d: d["wires"][1].update(to="scop.CH1"), "E-WIRE-OWNER", "/wires/1/to", "scope.CH1"),
        (
            lambda d: d["instruments"]["awg"].update(state={"ch1.frequncy": 1}),
            "E-STATE-UNKNOWN",
            "/instruments/awg/state/ch1.frequncy",
            "ch1.frequency",
        ),
        (
            lambda d: d["instruments"]["awg"].update(state={"ch1.frequency": 1e12}),
            "E-STATE-RANGE",
            "/instruments/awg/state/ch1.frequency",
            None,
        ),
        (
            lambda d: d["parts"]["rc"].update(type="rc_lowpas"),
            "E-COMPONENT-UNKNOWN",
            "/parts/rc/type",
            "rc_lowpass",
        ),
        (
            lambda d: d["wires"].append({"from": "awg.OUT2", "to": "rc.IN"}),
            "E-NET-MULTIDRIVER",
            "/wires/0",
            None,
        ),
    ],
)
def test_errors_point_at_the_place_and_suggest_a_fix(mutate, code, pointer, suggestion):
    data = copy.deepcopy(BASE)
    mutate(data)
    _, report = validate_data(data, LIB)
    match = [d for d in report.diagnostics if d.code == code]
    assert match, report.diagnostics
    assert match[0].pointer == pointer
    if suggestion:
        assert suggestion in (match[0].suggestion or "")
    assert not report.ok


def test_schema_errors_are_diagnostics():
    _, report = validate_data({"schema": "simubench.bench/1", "wires": "x"}, LIB)
    assert not report.ok and all(d.code == "E-SCHEMA" for d in report.diagnostics)


def test_a_50_ohm_input_terminates_the_net():
    data = {
        "schema": "simubench.bench/1",
        "name": "t",
        "instruments": {
            "awg": {"model": "sb-awg-2"},
            "sa": {"model": "sb-sa-1"},
            "scope": {"model": "sb-dso-4"},
        },
        "wires": [{"from": "awg.OUT1", "to": "sa.RF_IN"}, {"from": "awg.OUT2", "to": "scope.CH2"}],
    }
    found, _ = codes(data)
    assert found == [("W-IMPEDANCE-MISMATCH", "/instruments/awg/state/ch2.load")]  # the analyzer is 50 Ω
    data["instruments"]["scope"]["state"] = {"ch2.impedance": "FIFTy"}
    found, _ = codes(data)
    assert found == []
