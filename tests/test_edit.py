import pytest
from pydantic import TypeAdapter

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
    with pytest.raises(EditError, match="第 2 个操作"):
        apply(b, ops({"op": "move", "name": "rc", "x": 1, "y": 2}, {"op": "remove", "name": "nope"}))
    assert b.layout["rc"].x != 1


def test_rename_rewrites_wires_and_layout():
    b = apply(build(), ops({"op": "rename", "name": "rc", "new_name": "filter"}))
    assert "filter" in b.duts and "rc" not in b.layout
    assert [(w.from_, w.to) for w in b.wires] == [("awg.OUT1", "filter.IN"), ("filter.OUT", "scope.CH1")]


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
        ({"op": "set_params", "dut": "awg", "params": {}}, "DUT"),
    ],
)
def test_errors_explain(op, message):
    with pytest.raises(EditError, match=message):
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
