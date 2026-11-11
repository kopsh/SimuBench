import pytest
from pydantic import TypeAdapter

from helpers import raises_with
from simubench.engine.edit import EditError, EditOp, apply, to_yaml_data
from simubench.engine.validate import validate_data
from simubench.library.loader import DefinitionLibrary
from simubench.models.bench import BenchDefinition

OPS = TypeAdapter(list[EditOp])
LIB = DefinitionLibrary()


def empty() -> BenchDefinition:
    return BenchDefinition.model_validate({"schema": "simubench.bench/1", "name": "t"})


def ops(*items):
    return OPS.validate_python(list(items))


def build() -> BenchDefinition:
    return apply(
        empty(),
        ops(
            {"op": "add_instrument", "name": "awg", "model": "sb-awg-2"},
            {"op": "add_instrument", "name": "scope", "model": "sb-dso-4", "x": 600, "y": 0},
            {"op": "add_dut", "name": "rc", "type": "rc_lowpass", "params": {"r": 1000}},
            {"op": "connect", "from": "awg.OUT1", "to": "rc.IN"},
            {"op": "connect", "from": "rc.OUT", "to": "scope.CH1"},
            {"op": "set_state", "instrument": "awg", "path": "ch1.load", "value": "INF"},
        ),
    )


def test_built_bench_validates_clean():
    b = build()
    _, report = validate_data(to_yaml_data(b), LIB)
    assert report.ok and report.diagnostics == []
    assert b.layout["scope"].x == 600
    assert b.layout["rc"].x > b.layout["awg"].x  # placed to the right of existing nodes


def test_apply_is_all_or_nothing():
    b = build()
    with raises_with(EditError, "第 2 个操作"):
        apply(b, ops({"op": "move", "name": "rc", "x": 1, "y": 2}, {"op": "remove", "name": "nope"}))
    assert b.layout["rc"].x != 1


def test_rename_rewrites_wires_and_layout():
    b = apply(build(), ops({"op": "rename", "name": "rc", "new_name": "filter"}))
    assert "filter" in b.parts and "rc" not in b.layout
    assert [(w.from_, w.to) for w in b.wires] == [("awg.OUT1", "filter.IN"), ("filter.OUT", "scope.CH1")]


def test_replace_instrument_keeps_name_place_and_what_the_new_model_shares():
    """Swapping the hello bench's scope for another four-channel scope: name, label, layout and
    the CH1 wire stay; a setting the new model also has stays, the old unit's identity goes."""
    b = apply(
        empty(),
        ops(
            {"op": "add_instrument", "name": "awg", "model": "siglent-sdg2042x"},
            {
                "op": "add_instrument",
                "name": "scope",
                "model": "siglent-sds2104xplus",
                "label": "示波器",
                "x": 400,
                "y": 0,
            },
            {"op": "connect", "from": "awg.CH1", "to": "scope.CH1"},
            {"op": "set_state", "instrument": "scope", "path": "ch1.scale", "value": 0.25},
            {"op": "set_state", "instrument": "scope", "path": "acquire.mdepth", "value": "10k"},
            {"op": "set_identity", "instrument": "scope", "serial": "SN1"},
        ),
        LIB,
    )
    swapped = apply(b, ops({"op": "replace_instrument", "name": "scope", "model": "keysight-dsox2024a"}), LIB)
    scope = swapped.instruments["scope"]
    assert scope.model == "keysight-dsox2024a" and scope.label == "示波器"
    assert swapped.layout["scope"].x == 400
    assert [(w.from_, w.to) for w in swapped.wires] == [("awg.CH1", "scope.CH1")]
    assert "ch1.scale" in scope.state and "acquire.mdepth" not in scope.state
    assert scope.serial is None
    _, report = validate_data(to_yaml_data(swapped), LIB)
    assert report.ok, report.diagnostics
    with raises_with(EditError, "仪表库"):
        apply(b, ops({"op": "replace_instrument", "name": "scope", "model": "keysight-dsox2024a"}))
    with raises_with(EditError, "没有仪表定义"):
        apply(b, ops({"op": "replace_instrument", "name": "scope", "model": "nope"}), LIB)


def test_remove_drops_wires():
    b = apply(build(), ops({"op": "remove", "name": "rc"}))
    assert b.wires == [] and "rc" not in b.layout


@pytest.mark.parametrize(
    "op, message",
    [
        ({"op": "add_dut", "name": "rc", "type": "resistor"}, "已被使用"),
        ({"op": "add_dut", "name": "1x", "type": "resistor"}, "无效"),
        ({"op": "connect", "from": "awg.OUT1", "to": "rc.IN"}, "已有连线"),
        ({"op": "connect", "from": "awg.OUT2", "to": "ghost.IN"}, "ghost"),
        ({"op": "connect", "from": "awg", "to": "rc.IN"}, "<名称>.<端口>"),
        ({"op": "disconnect", "index": 5}, "共 2 根"),
        ({"op": "set_params", "dut": "awg", "params": {}}, "元件"),  # the older field name still works
    ],
)
def test_errors_explain(op, message):
    with raises_with(EditError, message):
        apply(build(), ops(op))


def test_set_state_none_removes_and_yaml_is_tidy():
    b = apply(build(), ops({"op": "set_state", "instrument": "awg", "path": "ch1.load", "value": None}))
    data = to_yaml_data(b)
    assert "state" not in data["instruments"]["awg"]
    assert all("accessories" not in w for w in data["wires"])
    assert list(data)[:2] == ["schema", "name"]
    assert BenchDefinition.model_validate(data) == b


def test_layout_at_origin_is_written():
    b = apply(build(), ops({"op": "move", "name": "awg", "x": 0, "y": 0}))
    assert to_yaml_data(b)["layout"]["awg"] == {"x": 0.0, "y": 0.0}


def test_yaml_reads_engineering_floats_as_numbers():
    from simubench.library.loader import load_yaml

    assert load_yaml("r: 1.0e7\nc: 1e-7\nn: 1000\naddr: TCPIP0::1::INSTR") == {
        "r": 1e7,
        "c": 1e-7,
        "n": 1000,
        "addr": "TCPIP0::1::INSTR",
    }


def test_saving_keeps_preset_faults_and_the_register():
    from simubench.engine.edit import to_yaml_data
    from simubench.models.bench import BenchDefinition

    bench = BenchDefinition.model_validate(
        {
            "name": "x",
            "instruments": {"dmm": {"model": "sb-dmm-1", "serial": "MY001"}},
            "faults": [{"kind": "timeout", "instrument": "dmm", "at_s": 1.0}],
            "assets": [{"serial": "MY001", "cal_due": "2026-06-30"}],
            "run": {"start": "2026-05-01T08:00:00Z"},
        }
    )
    data = to_yaml_data(bench)
    assert data["faults"] == [{"kind": "timeout", "instrument": "dmm", "at_s": 1.0}]
    assert data["assets"] == [{"serial": "MY001", "cal_due": "2026-06-30"}]
    assert data["run"]["start"].startswith("2026-05-01T08:00:00")
    assert BenchDefinition.model_validate(data) == bench


def test_set_identity():
    idn = "LECROY,WAVEPRO 254HD,{serial},{firmware}"
    b = apply(
        empty(),
        ops(
            {"op": "add_instrument", "name": "scope", "model": "sb-dso-2x"},
            {"op": "set_identity", "instrument": "scope", "idn": idn, "serial": "X1"},
        ),
    )
    inst = b.instruments["scope"]
    assert (inst.idn, inst.serial, inst.firmware) == (idn, "X1", None)
    assert apply(b, ops({"op": "set_identity", "instrument": "scope"})).instruments["scope"].idn is None
    with raises_with(EditError, "nope"):
        apply(b, ops({"op": "set_identity", "instrument": "nope", "idn": "x"}))


@pytest.mark.parametrize(
    ("taken", "wanted", "given"),
    [
        (["GPIB0::5::INSTR"], "GPIB0::5::INSTR", "GPIB0::6::INSTR"),
        (["GPIB0::30::INSTR"], "gpib0::30::instr", "gpib0::1::instr"),
        (
            ["TCPIP0::192.168.1.20::INSTR", "TCPIP0::192.168.1.21::INSTR"],
            "TCPIP0::192.168.1.20::INSTR",
            "TCPIP0::192.168.1.22::INSTR",
        ),
        (
            ["USB0::0x2A8D::0x1301::MY001::INSTR"],
            "USB0::0x2A8D::0x1301::MY001::INSTR",
            "USB0::0x2A8D::0x1301::MY002::INSTR",
        ),
        ([], "GPIB0::5::INSTR", "GPIB0::5::INSTR"),
    ],
)
def test_an_added_instrument_gets_a_free_address_when_its_own_is_taken(taken, wanted, given):
    b = empty()
    for i, address in enumerate(taken):
        b = apply(b, ops({"op": "add_instrument", "name": f"i{i}", "model": "sb-awg-2", "address": address}))
    b = apply(b, ops({"op": "add_instrument", "name": "new", "model": "sb-awg-2", "address": wanted}))
    assert b.instruments["new"].address == given
