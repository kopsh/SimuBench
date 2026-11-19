import copy

import pytest

from helpers import raises_with
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
            lambda d: d["instruments"]["awg"].update(hidden={"nope": {"z_out": 50.0}}),
            "E-HIDDEN-COMPONENT",
            "/instruments/awg/hidden/nope",
            "ch1.out",
        ),
        (
            lambda d: d["instruments"]["awg"].update(hidden={"ch1.out": {"z_outt": 50.0}}),
            "E-HIDDEN-PARAM",
            "/instruments/awg/hidden/ch1.out/z_outt",
            "z_out",
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
            "/wires/2",  # the newest wire of the net, the one that added the second driver (#1269)
            None,
        ),
        (  # #1455: a resistance of zero or below is no resistor
            lambda d: d["parts"]["rc"]["params"].update(r=-5),
            "E-PARAM-RANGE",
            "/parts/rc/params/r",
            None,
        ),
        (
            lambda d: d["parts"]["rc"]["params"].update(r=0),
            "E-PARAM-RANGE",
            "/parts/rc/params/r",
            None,
        ),
        (
            lambda d: d["parts"]["rc"]["params"].update(c=-1e-7),
            "E-PARAM-RANGE",
            "/parts/rc/params/c",
            None,
        ),
        (  # a tolerance of -100 % makes r·(1 + tol_r) zero
            lambda d: d["parts"]["rc"]["params"].update(tol_r=-1.0),
            "E-PARAM-RANGE",
            "/parts/rc/params/tol_r",
            None,
        ),
        (  # #1589: one address, spelled two ways (VISA's default inst0, case)
            lambda d: (
                d["instruments"]["awg"].update(address="TCPIP0::10.0.0.5::INSTR"),
                d["instruments"]["scope"].update(address="tcpip0::10.0.0.5::inst0::instr"),
            ),
            "E-ADDRESS-DUPLICATE",
            "/instruments/scope/address",
            "地址",
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


def _ranged_parameters():
    from simubench.engine.component import component_types

    return [
        (name, param, bound)
        for name, cls in sorted(component_types().items())
        if cls.category in ("part", "accessory")
        for param, bound in cls.param_ranges.items()
    ]


@pytest.mark.parametrize(("ctype", "param", "bound"), _ranged_parameters())
def test_a_part_parameter_just_outside_its_physical_range_is_an_error(ctype, param, bound):
    """#1455: every part and accessory declares the range of its physical parameters; one step past
    either bound is E-PARAM-RANGE at the parameter, the bound itself is the range's own say, and
    the type's default is always inside."""
    outside = []
    if bound.minimum is not None:
        outside.append(bound.minimum if bound.min_open else bound.minimum - 1.0)
    if bound.maximum is not None:
        outside.append(bound.maximum if bound.max_open else bound.maximum + 1.0)
    for value in outside:
        data = copy.deepcopy(BASE)
        data["parts"]["x"] = {"type": ctype, "params": {param: value}}
        found = [d for d in validate_data(data, LIB)[1].diagnostics if d.code == "E-PARAM-RANGE"]
        assert [d.pointer for d in found] == [f"/parts/x/params/{param}"], (ctype, param, value)
    data = copy.deepcopy(BASE)
    data["parts"]["x"] = {"type": ctype, "params": {}}
    assert "E-PARAM-RANGE" not in [d.code for d in validate_data(data, LIB)[1].diagnostics]


def test_distinct_or_missing_addresses_are_not_duplicates():
    data = copy.deepcopy(BASE)
    data["instruments"]["awg"]["address"] = "TCPIP0::10.0.0.5::INSTR"
    data["instruments"]["scope"]["address"] = "TCPIP0::10.0.0.6::INSTR"
    data["instruments"]["extra"] = {"model": "sb-dso-4"}  # no address: nothing to compare
    found, _ = codes(data)
    assert "E-ADDRESS-DUPLICATE" not in [code for code, _ in found]


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


def test_open_bench_refuses_an_unknown_hidden_parameter():
    """#256: a bench naming a component or parameter the model does not have must not open."""
    from simubench.api import open_bench
    from simubench.models.bench import BenchDefinition

    data = copy.deepcopy(BASE)
    data["instruments"]["awg"]["hidden"] = {"ch1.out": {"z_outt": 50.0}}
    with raises_with(ValueError, "unknown parameter ch1.out.z_outt"):
        open_bench(BenchDefinition.model_validate(data))


def _example_benches():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return sorted((root / "examples" / "benches").glob("*.bench.yaml"))


@pytest.mark.parametrize("path", _example_benches(), ids=lambda p: p.name)
def test_every_example_bench_opens_without_a_diagnostic(path):
    """#316: a built-in example opens with no warning; star joins (a divider's middle, a Kelvin
    pair) are not suspicious — two outputs on one net still are (E-NET-MULTIDRIVER above)."""
    import yaml

    _, report = validate_data(yaml.safe_load(path.read_text("utf-8")), LIB)
    assert [(d.code, d.pointer) for d in report.diagnostics] == []
