"""Static checks of instrument definitions and the circuit roles bench checks read (issue 40)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import ClassVar

import pytest

from simubench.cli import main
from simubench.engine.component import _REGISTRY, Component, PortSpec, component
from simubench.engine.validate import validate_data
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.loader import DefinitionLibrary, load_yaml

ROOT = Path(__file__).resolve().parents[1]
AWG = load_yaml((ROOT / "validation" / "instruments" / "sb-awg-2.yaml").read_text(encoding="utf-8"))
DSO = load_yaml((ROOT / "validation" / "instruments" / "sb-dso-2x.yaml").read_text(encoding="utf-8"))
DEFINITIONS = sorted(
    p
    for d in (
        "src/simubench/library/instruments",
        "validation/instruments",
        "examples/lowfreq-cal/instruments",
    )
    for p in (ROOT / d).glob("*.yaml")
    if not p.name.endswith(".coverage.yaml")
)


def codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


def command(data: dict, header: str) -> dict:
    return next(c for c in data["commands"] if c["header"] == header)


@pytest.mark.parametrize("path", DEFINITIONS, ids=lambda p: p.stem)
def test_every_definition_in_the_repository_is_clean(path):
    _, report = validate_definition_data(load_yaml(path.read_text(encoding="utf-8")))
    assert [d.as_dict() for d in report.diagnostics] == []


def test_format_and_schema():
    assert codes({**AWG, "schema": "simubench.instrument/9"}) == [("E-FORMAT", "/schema")]
    bad = copy.deepcopy(AWG)
    bad["channel_state"]["frequency"]["type"] = "number"
    assert codes(bad) == [("E-SCHEMA", "/channel_state/frequency/type")]


def test_unknown_component_type():
    bad = copy.deepcopy(AWG)
    bad["channel_components"][1]["type"] = "output_stag"
    _, report = validate_definition_data(bad)
    [d] = report.diagnostics  # wiring and ports to it are not reported again
    assert (d.code, d.pointer) == ("E-DEF-COMPONENT-UNKNOWN", "/channel_components/1/type")
    assert d.suggestion == "改为 'output_stage'"


def test_duplicate_component_name():
    bad = copy.deepcopy(AWG)
    bad["channel_components"][1]["name"] = "gen"
    assert ("E-DEF-COMPONENT-DUPLICATE", "/channel_components/1/name") in codes(bad)


def test_unknown_parameter_is_a_warning():
    bad = copy.deepcopy(AWG)
    bad["channel_components"][1]["params"]["z_outt"] = 50
    _, report = validate_definition_data(bad)
    assert report.ok
    [d] = report.diagnostics
    assert (d.code, d.pointer, d.suggestion) == (
        "W-DEF-PARAM-UNKNOWN",
        "/channel_components/1/params/z_outt",
        "改为 'z_out'",
    )


def test_state_binding_of_a_component_parameter():
    bad = copy.deepcopy(AWG)
    bad["channel_components"][0]["params"]["frequency"] = "$ch.frequncy"
    _, report = validate_definition_data(bad)
    [d] = report.diagnostics
    assert (d.code, d.pointer) == ("E-DEF-STATE-UNKNOWN", "/channel_components/0/params/frequency")
    assert d.suggestion == "改为 'ch.frequency'"


def test_channel_state_outside_a_channel():
    bad = copy.deepcopy(DSO)
    bad["components"][0]["params"] = {"overhead_s": "$ch.scale"}  # acq is one per instrument
    assert codes(bad) == [("E-DEF-STATE-UNKNOWN", "/components/0/params/overhead_s")]
    bad["components"][0]["params"] = {"overhead_s": "$ch3.scale"}  # two channels
    assert codes(bad) == [("E-DEF-STATE-UNKNOWN", "/components/0/params/overhead_s")]
    bad["components"][0]["params"] = {"overhead_s": "$ch2.scale"}
    assert codes(bad) == []


def test_state_binding_of_a_command():
    bad = copy.deepcopy(AWG)
    command(bad, "[:SOURce<ch>]:FREQuency[:CW]")["state"] = "ch.freq"
    i = bad["commands"].index(command(bad, "[:SOURce<ch>]:FREQuency[:CW]"))
    assert codes(bad) == [("E-DEF-STATE-UNKNOWN", f"/commands/{i}/state")]
    bad = copy.deepcopy(AWG)
    command(bad, "[:SOURce<ch>]:FREQuency[:CW]")["header"] = ":FREQuency"  # no channel number
    assert codes(bad) == [("E-DEF-STATE-UNKNOWN", f"/commands/{i}/state")]


def test_parameter_type_against_its_state():
    bad = copy.deepcopy(AWG)
    bad["channel_components"][0]["params"]["frequency"] = "$ch.function"  # a number bound to an enum
    bad["channel_state"]["function"]["type"] = "string"
    bad["channel_state"]["function"].pop("options")
    assert codes(bad) == [("E-DEF-PARAM-TYPE", "/channel_components/0/params/frequency")]


def test_operations():
    bad = copy.deepcopy(DSO)
    i = next(i for i, c in enumerate(bad["commands"]) if c.get("op") == "acq.preamble")
    bad["commands"][i]["op"] = "acq.preambel"
    _, report = validate_definition_data(bad)
    [d] = report.diagnostics
    assert (d.code, d.pointer, d.suggestion) == ("E-DEF-OP-UNKNOWN", f"/commands/{i}/op", "改为 acq.preamble")
    bad["commands"][i]["op"] = "aqc.preamble"
    assert codes(bad) == [("E-DEF-OP-UNKNOWN", f"/commands/{i}/op")]
    bad["commands"][i]["op"] = "ch.front.preamble"  # a channel component, header without <ch>
    assert codes(bad) == [("E-DEF-OP-UNKNOWN", f"/commands/{i}/op")]


def test_wiring_and_ports():
    bad = copy.deepcopy(DSO)
    bad["wiring"]["channel"][0] = ["inp.out", "frnt.in"]
    bad["wiring"]["instrument"][0] = ["ch{n}.front.output", "acq.in{n}"]
    bad["ports"]["channel"]["CH{n}"] = "inp.pn"
    assert codes(bad) == [
        ("E-DEF-WIRE-COMPONENT", "/wiring/channel/0/1"),
        ("E-DEF-WIRE-PORT", "/wiring/instrument/0/0"),
        ("E-DEF-WIRE-PORT", "/ports/channel/CH{n}"),
    ]
    bad = copy.deepcopy(DSO)
    bad["wiring"]["instrument"][0] = ["ch{n}.front.out", "acq.input{n}"]  # not one of in1..inN
    assert codes(bad) == [("E-DEF-WIRE-PORT", "/wiring/instrument/0/1")]


def test_headers_that_do_not_parse():
    bad = copy.deepcopy(AWG)
    bad["commands"][0]["header"] = ":TRACe[1]:TYPE"  # [1] is not a node: TRAC1:TYPE would not match
    assert codes(bad) == [("E-DEF-HEADER", "/commands/0/header")]
    bad["commands"][0]["header"] = "*I-DN"
    assert codes(bad) == [("E-DEF-HEADER", "/commands/0/header")]


def test_two_commands_for_one_header():
    bad = copy.deepcopy(AWG)
    n = len(bad["commands"])
    bad["commands"].append({"header": ":SOURce<ch>:FREQuency", "state": "ch.frequency"})
    _, report = validate_definition_data(bad)
    [d] = report.diagnostics
    assert (d.code, d.pointer) == ("E-DEF-HEADER-DUPLICATE", f"/commands/{n}/header")
    assert ":SOURCE:FREQUENCY" in d.message
    bad["commands"][-1]["header"] = ":SOURce<ch>:FREQuency:MODE"  # a different header
    assert codes(bad) == []


def test_cli_validates_definitions_and_benches(tmp_path, capsys):
    bad = copy.deepcopy(AWG)
    bad["channel_components"][1]["type"] = "output_stag"
    path = tmp_path / "x.yaml"
    path.write_text(__import__("yaml").safe_dump(bad, allow_unicode=True), encoding="utf-8")
    bench = ROOT / "examples" / "lowfreq-cal" / "lowfreq_cal.bench.yaml"
    assert main(["validate", str(DEFINITIONS[0]), str(bench)]) == 0
    assert main(["validate", str(path)]) == 1
    assert "x.yaml:/channel_components/1/type: E-DEF-COMPONENT-UNKNOWN" in capsys.readouterr().err


# ---- circuit roles: bench checks follow what a component declares, not its name ----


@pytest.fixture
def custom_components():
    @component("test_source")
    class TestSource(Component):
        category = "dut"
        role = "driver"
        ports: ClassVar[dict[str, PortSpec]] = {"OUT": PortSpec("out", "electrical")}

    @component("test_link")
    class TestLink(Component):
        category = "accessory"
        role = "series"
        ports: ClassVar[dict[str, PortSpec]] = {
            "IN": PortSpec("inout", "electrical"),
            "OUT": PortSpec("inout", "electrical"),
        }

    yield
    _REGISTRY.pop("test_source")
    _REGISTRY.pop("test_link")


def _bench(wires: list[dict], duts: dict | None = None, load: float = 50.0) -> dict:
    return {
        "schema": "simubench.bench/1",
        "name": "roles",
        "instruments": {
            "awg": {"model": "sb-awg-2", "state": {"ch1.load": load}},
            "scope": {"model": "sb-dso-2x", "state": {"ch1.impedance": "FIFTy"}},
        },
        "duts": duts or {},
        "wires": wires,
    }


def _diagnostics(data: dict) -> list[str]:
    lib = DefinitionLibrary([ROOT / "validation" / "instruments"])
    _, report = validate_data(data, lib)
    return [d.code for d in report.diagnostics]


def test_a_declared_driver_counts_as_a_driver(custom_components):
    wires = [{"from": "awg.OUT1", "to": "scope.CH1"}, {"from": "src.OUT", "to": "scope.CH1"}]
    assert "E-NET-MULTIDRIVER" in _diagnostics(_bench(wires, {"src": {"type": "test_source"}}))


def test_termination_is_seen_through_a_declared_series_accessory(custom_components):
    through = [{"from": "awg.OUT1", "to": "scope.CH1", "accessories": [{"type": "test_link"}]}]
    assert _diagnostics(_bench(through)) == []
    resistor = [{"from": "awg.OUT1", "to": "scope.CH1", "accessories": [{"type": "passive_probe"}]}]
    assert "W-IMPEDANCE-MISMATCH" in _diagnostics(_bench(resistor))
