"""Front-panel layouts (issue 128, instrument-authoring.md 7.3): the declaration in `display.panel`,
its static checks, and the class defaults of a multimeter and a calibrator."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.engine.validate_definition import validate_definition_data
from simubench.library.loader import load_yaml
from simubench.models.definition import InstrumentDefinition
from simubench.models.panels import effective_panel
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "src" / "simubench" / "library" / "instruments"
PANEL = ROOT / "web" / "e2e" / "fixtures" / "instruments" / "sb-panel-1.yaml"


def raw(path: Path) -> dict:
    return load_yaml(path.read_text(encoding="utf-8"))


def definition(path: Path) -> InstrumentDefinition:
    return InstrumentDefinition.model_validate(raw(path))


def undeclared(path: Path) -> InstrumentDefinition:
    """The model as if it declared no panel: what a class default is made from."""
    data = raw(path)
    data["display"].pop("panel", None)
    return InstrumentDefinition.model_validate(data)


def codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


# ---- class defaults ---------------------------------------------------------------------------------


def test_an_unknown_class_gets_no_class_default_panel():
    """#1410: no registry row, no default layout — the generic layout is the fallback."""
    data = raw(LIB / "keysight-34465a.yaml")
    data["display"].pop("panel", None)
    data["display"].pop("template", None)
    data["class"] = "no_such_class"
    assert effective_panel(InstrumentDefinition.model_validate(data)) == (None, None)


def test_multimeter_default_is_a_reading_with_function_and_range_keys():
    panel, source = effective_panel(undeclared(LIB / "keysight-34465a.yaml"))
    assert source == "class_default" and panel is not None
    # The reading of the selected function: DC while VOLT, AC while VOLT:AC.
    assert [(r.reading, r.unit, r.when) for r in panel.readouts] == [
        ("meter.DC", "V", {"sense.function": "VOLT"}),
        ("meter.AC", "V", {"sense.function": "VOLT:AC"}),
        ("meter.RES", "Ω", {"sense.function": "RES"}),  # resistance and current (#301)
        ("meter.FRES", "Ω", {"sense.function": "FRES"}),
        ("meter.DCI", "A", {"sense.function": "CURR"}),
        ("meter.ACI", "A", {"sense.function": "CURR:AC"}),
    ]
    # A 6½-digit meter shows 7 significant digits, not the 9 of its SCPI reply (#278).
    assert {r.digits for r in panel.readouts} == {7}
    groups = {g.label: g.controls for g in panel.groups}
    assert list(groups) == ["测量", "量程", "设置", "触发"]
    [function] = groups["测量"]
    assert function.control == "buttons" and [k.label for k in function.keys or []] == [
        "DC V",
        "AC V",
        "Ω 2W",
        "Ω 4W",
        "DC I",
        "AC I",
    ]
    [ranges] = groups["量程"]
    keys = ranges.keys or []
    assert [k.label for k in keys] == ["100 mV", "1 V", "10 V", "100 V", "1000 V", "AUTO"]
    assert keys[2].value == 10.0 and keys[2].sets == {"sense.range_auto": False}
    assert keys[-1].sets == {"sense.range_auto": True}
    # NPLC, the CONFigure resolution and input impedance, as the acceptance in 7.0 asks; the
    # trigger model's settings. The resolution joined with #286: the ADC's set_resolution is
    # bound to the setting, and a bound setting gets a control.
    assert [(c.state, c.control) for c in groups["设置"]] == [
        ("sense.nplc", "number"),
        ("sense.resolution", "number"),
        ("sense.imp_auto", "switch"),
        ("sense.res.range", "number"),  # the ohms and current functions' own ranges (#301)
        ("sense.curr.range", "number"),
        ("sense.ac.bandwidth", "number"),
    ]
    assert ("trigger.source", "select") in [(c.state, c.control) for c in groups["触发"]]


def test_calibrator_default_is_the_output_with_operate_and_standby_keys():
    panel, source = effective_panel(undeclared(LIB / "fluke-5522a.yaml"))
    assert source == "class_default" and panel is not None
    assert [r.reading for r in panel.readouts] == ["out.level", "out.frequency", "out.output"]
    assert panel.readouts[1].when == {"out.function": "ACV"}
    groups = {g.label: g.controls for g in panel.groups}
    assert [(c.state, c.control) for c in groups["输出"]] == [
        ("out.function", "buttons"),
        ("out.level", "number"),
        ("out.frequency", "number"),
    ]
    [operate] = groups["工作 / 待机"]
    assert [(k.label, k.value) for k in operate.keys or []] == [("OPER", True), ("STBY", False)]


@pytest.mark.parametrize(
    ("name", "units"),
    [
        ("fluke-5522a.yaml", {"DCV": "V", "ACV": "V", "DCI": "A", "ACI": "A", "RES": "Ω"}),
        ("fluke-5730a.yaml", {"DCV": "V", "ACV": "V", "DCI": "A", "ACI": "A", "OHM": "Ω"}),
    ],
)
def test_a_declared_calibrator_panel_shows_the_level_in_the_functions_unit(name, units):
    # #1326: the output reading's unit follows the function (V, A, Ω), so a current or a resistance
    # is not read as a voltage; the frequency belongs to the AC functions and is hidden for the rest.
    d = definition(LIB / name)
    panel, source = effective_panel(d)
    assert source == "declared" and panel is not None
    assert set(units) == set(d.state["out.function"].options or [])
    for function, unit in units.items():
        visible = [r for r in panel.readouts if all(value == function for value in r.when.values())]
        assert [(r.unit, r.digits) for r in visible if r.reading == "out.level"] == [(unit, 7)]
        assert bool([r for r in visible if r.reading == "out.frequency"]) == (function in ("ACV", "ACI"))


def test_daq_default_is_the_scan_and_the_closed_relays():
    panel, source = effective_panel(definition(ROOT / "validation" / "instruments" / "sb-daq-1.yaml"))
    assert source == "class_default" and panel is not None
    # The channel just read, the reading, the scan list and the closed relays.
    assert [r.reading for r in panel.readouts] == [
        "scan.channel",
        "scan.reading",
        "scan.list",
        "mux.closed",
    ]
    groups = {g.label: g.controls for g in panel.groups}
    assert list(groups) == ["测量", "扫描"]
    assert [(c.state, c.control) for c in groups["测量"]] == [
        ("sense.function", "select"),
        ("sense.nplc", "number"),
    ]
    assert [(c.state, c.control) for c in groups["扫描"]] == [
        ("scan.count", "number"),
        ("scan.interval", "number"),
    ]
    assert [s.action for s in panel.softkeys] == ["run", "stop"]


def test_power_supply_default_is_each_output_and_the_combination():
    panel, source = effective_panel(definition(ROOT / "validation" / "instruments" / "sb-ps-1.yaml"))
    assert source == "class_default" and panel is not None
    assert [r.reading for r in panel.readouts] == [
        "ch1.read.voltage",
        "ch1.read.current",
        "ch1.prot.tripped",
        "ch2.read.voltage",
        "ch2.read.current",
        "ch2.prot.tripped",
        "ch3.read.voltage",
        "ch3.read.current",
        "ch3.prot.tripped",
    ]
    groups = {g.label: g.controls for g in panel.groups}
    assert list(groups) == ["CH1", "CH2", "CH3", "组合"]
    assert [(c.state, c.control) for c in groups["CH1"]] == [
        ("ch1.output", "switch"),
        ("ch1.voltage", "number"),
        ("ch1.current", "number"),
    ]
    [mode] = groups["组合"]
    assert mode.control == "buttons"
    assert [k.value for k in mode.keys or []] == ["OFF", "PARallel", "SERies", "TRACk"]


def test_counter_default_is_the_reading_its_gate_and_both_inputs():
    panel, source = effective_panel(definition(ROOT / "validation" / "instruments" / "sb-counter-1.yaml"))
    assert source == "class_default" and panel is not None
    assert [r.reading for r in panel.readouts] == [
        "core.value",
        "core.value",
        "core.value",
        "core.value",
        *["stats.mean"] * 4,
        *["stats.sdev"] * 4,
        "stats.count",
    ]
    # The statistics carry the selected function's unit, as the main reading does (#278).
    stats = [(r.reading, r.unit, r.when) for r in panel.readouts if r.reading in ("stats.mean", "stats.sdev")]
    assert ("stats.mean", "Hz", {"function": "FREQuency"}) in stats
    assert ("stats.sdev", "s", {"function": "PERiod"}) in stats
    assert ("stats.mean", None, {"function": "TOTalize"}) in stats
    groups = {g.label: g.controls for g in panel.groups}
    assert list(groups) == ["测量", "门控", "输入 A", "输入 B"]
    assert [(c.state, c.control) for c in groups["测量"]] == [("function", "buttons")]
    assert [(c.state, c.control) for c in groups["门控"]] == [
        ("gate.time", "number"),
        ("gate.source", "select"),
        ("count", "number"),
    ]
    assert [(c.state, c.control) for c in groups["输入 A"]] == [
        ("input.a.impedance", "select"),
        ("input.a.coupling", "select"),
        ("input.a.threshold", "number"),
        ("input.a.sensitivity", "number"),
    ]
    assert [c.state for c in groups["输入 B"]] == [
        "input.b.impedance",
        "input.b.coupling",
        "input.b.threshold",
        "input.b.sensitivity",
    ]


def test_declared_panel_wins_and_dedicated_screens_have_none():
    panel, source = effective_panel(definition(PANEL))
    assert (
        source == "declared"
        and panel is not None
        and [g.label for g in panel.groups] == ["功能", "量程", "高级"]
    )
    # A dedicated screen's model declares groups only (the inspector's panel, D74); one that declares
    # nothing has no panel at all.
    for name in ("siglent-sds2104xplus.yaml", "siglent-sdg2042x.yaml"):
        panel, source = effective_panel(definition(LIB / name))
        assert source == "declared" and panel is not None and panel.groups and not panel.readouts
    assert effective_panel(definition(ROOT / "validation" / "instruments" / "sb-dso-4.yaml")) == (None, None)
    # A class without a default keeps the generic layout.
    assert effective_panel(definition(ROOT / "validation" / "instruments" / "sb-arb-1.yaml")) == (None, None)
    # A multimeter without bound settings still shows its readings.
    panel, _ = effective_panel(definition(ROOT / "validation" / "instruments" / "sb-dmm-1.yaml"))
    assert panel is not None and [r.reading for r in panel.readouts] == ["meter.DC", "meter.AC"]
    assert panel.groups == []


def test_session_info_carries_the_panel():
    manager = SessionManager(ROOT / "validation", scpi_base_port=None)
    app = create_app(manager, token="t", web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = "t"
        info = c.post("/api/sessions", json={"bench_path": "benches/fluke-5522a-dmm.bench.yaml"}).json()
        display = {i["name"]: i["display"] for i in info["instruments"]}
        assert display["cal"]["panel_source"] == "declared"
        assert display["cal"]["panel"]["groups"][1]["controls"][0]["state"] == "out.operate"
        assert display["dmm"]["panel"]["readouts"][0]["reading"] == "meter.DC"


# ---- static checks ----------------------------------------------------------------------------------


def test_the_declared_test_panel_is_clean():
    assert codes(raw(PANEL)) == []


def control(data: dict, group: int, index: int) -> dict:
    return data["display"]["panel"]["groups"][group]["controls"][index]


G = "/display/panel/groups"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        # A setting that does not exist, in a control, a key's `sets` and a readout's `when`.
        (
            lambda d: control(d, 0, 0).update(state="sense.func"),
            [("E-DEF-PANEL-STATE", f"{G}/0/controls/0/state")],
        ),
        (
            lambda d: control(d, 1, 0)["keys"][0].update(sets={"sense.auto": False}),
            [("E-DEF-PANEL-STATE", f"{G}/1/controls/0/keys/0/sets/sense.auto")],
        ),
        (
            lambda d: d["display"]["panel"]["readouts"][0].update(when={"function": "DC"}),
            [("E-DEF-PANEL-STATE", "/display/panel/readouts/0/when/function")],
        ),
        # A control that does not fit the setting's type.
        (
            lambda d: control(d, 2, 1).update(control="switch"),
            [("E-DEF-PANEL-CONTROL", f"{G}/2/controls/1/control")],
        ),
        (
            lambda d: control(d, 2, 2).update(control="number"),
            [("E-DEF-PANEL-CONTROL", f"{G}/2/controls/2/control")],
        ),
        (
            lambda d: control(d, 2, 0).update(control="select"),
            [
                ("E-DEF-PANEL-CONTROL", f"{G}/2/controls/0/control"),
                ("E-DEF-PANEL-CONTROL", f"{G}/2/controls/0/step"),  # step is for a number
            ],
        ),
        # A number as buttons needs its keys; keys and step belong to their own controls.
        (lambda d: control(d, 1, 0).pop("keys"), [("E-DEF-PANEL-CONTROL", f"{G}/1/controls/0/keys")]),
        (
            lambda d: control(d, 2, 2).update(keys=[{"value": True}]),
            [("E-DEF-PANEL-CONTROL", f"{G}/2/controls/2/keys")],
        ),
        (lambda d: control(d, 2, 1).update(step=1), [("E-DEF-PANEL-CONTROL", f"{G}/2/controls/1/step")]),
        # Values a key writes: an enum option as written, a number in range, a bool for a bool.
        (
            lambda d: control(d, 0, 0)["keys"][1].update(value="ACV"),
            [("E-DEF-PANEL-VALUE", f"{G}/0/controls/0/keys/1/value")],
        ),
        (
            lambda d: control(d, 1, 0)["keys"][2].update(value=1000),
            [("E-DEF-PANEL-VALUE", f"{G}/1/controls/0/keys/2/value")],
        ),
        (
            lambda d: control(d, 1, 0)["keys"][3].update(sets={"sense.range_auto": "yes"}),
            [("E-DEF-PANEL-VALUE", f"{G}/1/controls/0/keys/3/sets/sense.range_auto")],
        ),
        (
            lambda d: d["display"]["panel"]["softkeys"][0].update(value=50),
            [("E-DEF-PANEL-VALUE", "/display/panel/softkeys/0/value")],
        ),
        # A reading of a component the definition does not have; an action nothing runs.
        (
            lambda d: d["display"]["panel"]["readouts"][0].update(reading="dmm.DC"),
            [("E-DEF-PANEL-READING", "/display/panel/readouts/0/reading")],
        ),
        (
            lambda d: d["display"]["panel"]["softkeys"].append({"label": "单次", "action": "single"}),
            [("E-DEF-PANEL-ACTION", "/display/panel/softkeys/2/action")],
        ),
        # A class with a dedicated screen uses only the groups (for the inspector, D74): readouts and
        # softkeys there are not drawn; groups alone are clean.
        (lambda d: d["display"].update(template="oscilloscope"), [("W-DEF-PANEL-IGNORED", "/display/panel")]),
        (
            lambda d: (
                d["display"].update(template="oscilloscope"),
                d["display"]["panel"].update(readouts=[], softkeys=[]),
            ),
            [],
        ),
        # A group's or a control's `when` names a setting and a value it takes (#1315).
        (
            lambda d: control(d, 2, 1).update(when={"sense.func": "DC"}),
            [("E-DEF-PANEL-STATE", f"{G}/2/controls/1/when/sense.func")],
        ),
        (
            lambda d: d["display"]["panel"]["groups"][1].update(when={"sense.function": "ACV"}),
            [("E-DEF-PANEL-VALUE", f"{G}/1/when/sense.function")],
        ),
        # A `when` value may be a list (any of them holds); each item is checked.
        (lambda d: control(d, 2, 1).update(when={"sense.function": ["DC", "AC"]}), []),
        (
            lambda d: control(d, 2, 1).update(when={"sense.function": ["DC", "nope"]}),
            [("E-DEF-PANEL-VALUE", f"{G}/2/controls/1/when/sense.function")],
        ),
        # Channel groups need channels; a control may leave its kind to the setting's type.
        (
            lambda d: d["display"]["panel"].update(
                channel_groups=[{"label": "通道", "controls": [{"state": "level"}]}]
            ),
            [("E-DEF-PANEL-STATE", "/display/panel/channel_groups")],
        ),
        (lambda d: control(d, 2, 1).pop("control"), []),
    ],
)
def test_panel_counterexamples(change, expected):
    data = copy.deepcopy(raw(PANEL))
    change(data)
    assert codes(data) == expected


def test_a_large_model_without_a_panel_is_asked_for_one_and_its_channel_groups_are_checked():
    # More settings than a summary can stand for (#1315): the inspector would show the summary only.
    data = copy.deepcopy(raw(LIB / "keysight-uxr1104b.yaml"))
    panel = data["display"].pop("panel")
    assert codes(data) == [("W-DEF-PANEL-MISSING", "/display")]
    # Channel groups name a channel setting without its prefix; a wrong name is reported where it is.
    panel["channel_groups"][0]["controls"].append({"state": "scael"})
    data["display"]["panel"] = panel
    assert codes(data) == [("E-DEF-PANEL-STATE", "/display/panel/channel_groups/0/controls/7/state")]


def test_an_enum_value_in_short_form_suggests_the_option():
    data = copy.deepcopy(raw(PANEL))
    control(data, 2, 1)["control"] = "buttons"
    control(data, 2, 1)["keys"] = [{"value": "TENM"}]
    _, report = validate_definition_data(data)
    [d] = report.diagnostics
    assert d.code == "E-DEF-PANEL-VALUE" and d.suggestion == "改为 'TENMeg'"


def test_a_readonly_setting_cannot_have_a_control():
    data = copy.deepcopy(raw(LIB / "fluke-5522a.yaml"))
    data["display"]["panel"] = {
        "groups": [{"label": "选件", "controls": [{"state": "installed_options", "control": "select"}]}]
    }
    assert codes(data) == [("E-DEF-PANEL-CONTROL", f"{G}/0/controls/0/state")]


def test_a_softkey_has_an_action_or_a_setting():
    data = copy.deepcopy(raw(PANEL))
    data["display"]["panel"]["softkeys"][0]["action"] = "run"
    assert [c for c, _ in codes(data)] == ["E-SCHEMA"]
