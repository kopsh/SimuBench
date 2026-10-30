"""Front-panel layouts (issue 128, instrument-authoring.md 7.3): the declaration in `display.panel`,
its static checks, and the class defaults of a multimeter and a calibrator."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simubench.engine.validate_definition import validate_definition_data
from simubench.library.loader import load_yaml
from simubench.library.panels import effective_panel
from simubench.models.definition import InstrumentDefinition
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "src" / "simubench" / "library" / "instruments"
PANEL = ROOT / "web" / "e2e" / "fixtures" / "instruments" / "sb-panel-1.yaml"


def raw(path: Path) -> dict:
    return load_yaml(path.read_text(encoding="utf-8"))


def definition(path: Path) -> InstrumentDefinition:
    return InstrumentDefinition.model_validate(raw(path))


def codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


# ---- class defaults ---------------------------------------------------------------------------------


def test_multimeter_default_is_a_reading_with_function_and_range_keys():
    panel, source = effective_panel(definition(LIB / "keysight-34465a.yaml"))
    assert source == "class_default" and panel is not None
    # The reading of the selected function: DC while VOLT, AC while VOLT:AC.
    assert [(r.reading, r.unit, r.when) for r in panel.readouts] == [
        ("meter.DC", "V", {"sense.function": "VOLT"}),
        ("meter.AC", "V", {"sense.function": "VOLT:AC"}),
    ]
    groups = {g.label: g.controls for g in panel.groups}
    assert list(groups) == ["测量", "量程", "设置", "触发"]
    [function] = groups["测量"]
    assert function.control == "buttons" and [k.label for k in function.keys or []] == ["DC V", "AC V"]
    [ranges] = groups["量程"]
    keys = ranges.keys or []
    assert [k.label for k in keys] == ["100 mV", "1 V", "10 V", "100 V", "1000 V", "AUTO"]
    assert keys[2].value == 10.0 and keys[2].sets == {"sense.range_auto": False}
    assert keys[-1].sets == {"sense.range_auto": True}
    # NPLC and input impedance, as the acceptance in 7.0 asks; the trigger model's settings.
    assert [(c.state, c.control) for c in groups["设置"]] == [
        ("sense.nplc", "number"),
        ("sense.imp_auto", "switch"),
        ("sense.ac.bandwidth", "number"),
    ]
    assert ("trigger.source", "select") in [(c.state, c.control) for c in groups["触发"]]


def test_calibrator_default_is_the_output_with_operate_and_standby_keys():
    panel, source = effective_panel(definition(LIB / "fluke-5522a.yaml"))
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


def test_declared_panel_wins_and_dedicated_screens_have_none():
    panel, source = effective_panel(definition(PANEL))
    assert (
        source == "declared"
        and panel is not None
        and [g.label for g in panel.groups] == ["功能", "量程", "高级"]
    )
    for name in ("siglent-sds2104xplus.yaml", "siglent-sdg2042x.yaml"):
        assert effective_panel(definition(LIB / name)) == (None, None)
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
        assert display["cal"]["panel_source"] == "class_default"
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
        # A panel on a class with a dedicated screen is not used.
        (lambda d: d["display"].update(template="oscilloscope"), [("W-DEF-PANEL-IGNORED", "/display/panel")]),
    ],
)
def test_panel_counterexamples(change, expected):
    data = copy.deepcopy(raw(PANEL))
    change(data)
    assert codes(data) == expected


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
