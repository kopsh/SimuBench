"""The override write protocol (#462): hidden state has one door — ``Session.set_parameter`` at
runtime, the ``engine.deviation`` primitives underneath it — so the priority hidden > drawn
deviation > ``$state`` binding is one place's business, and it is tested directly here."""

import yaml

from helpers import raises_with
from simubench.api import open_bench
from simubench.engine.component import component_type
from simubench.engine.deviation import draw_overrides, set_override
from simubench.models.bench import Fault
from simubench.models.deviation import DeviationRange

BENCH = {
    "schema": "simubench.bench/2",
    "name": "protocol",
    "parts": {"rc": {"type": "rc_lowpass", "dut": True, "params": {"r": 1000.0, "c": 1.0e-7}}},
    "run": {"seed": 3, "clock": "command", "idle": "off"},
}


def open_protocol(tmp_path, **part):
    path = tmp_path / "protocol.bench.yaml"
    path.write_text(
        yaml.safe_dump({**BENCH, "parts": {"rc": {**BENCH["parts"]["rc"], **part}}}), encoding="utf-8"
    )
    return open_bench(path)


def test_hidden_beats_the_draw(tmp_path):
    """A part's ``hidden`` occupies the parameter: the draw skips it and records nothing."""
    s = open_protocol(tmp_path, hidden={"r": 1234.0}, deviation={"r": {"limit": 10.0, "unit": "%"}})
    assert s.components["rc"].param("r") == 1234.0
    assert s.part_deviations == {}


def test_the_draw_lands_where_hidden_leaves_it_free(tmp_path):
    s = open_protocol(tmp_path, deviation={"r": {"limit": 10.0, "unit": "%"}})
    drawn = s.part_deviations["rc.r"]
    assert drawn != 1000.0  # it deviates from the nominal…
    assert s.components["rc"].param("r") == drawn  # …and it is what the parameter reads


def test_a_drawn_value_beats_the_state_binding():
    """Read order on the component: an occupied override answers before the ``$`` binding would."""
    comp = component_type("rc_lowpass")("rc", {"r": {"from": 1000.0}}, None, None)
    assert comp.param("r") == 1000.0  # the binding answers while nothing occupies it
    drawn = draw_overrides(comp, [("r", DeviationRange(limit=10.0, unit="%"))], 3)
    assert drawn["r"] != 1000.0
    assert comp.param("r") == drawn["r"]  # the draw, not the binding


def test_an_override_short_circuits_even_an_unreadable_binding():
    comp = component_type("rc_lowpass")("rc", {"r": {"from": "$nowhere"}}, None, None)
    with raises_with(ValueError, "outside an instrument"):
        comp.param("r")  # the binding alone cannot be resolved
    set_override(comp, "r", 1234.0, invalidate=False)  # no session: nothing to invalidate
    assert comp.param("r") == 1234.0


def test_set_parameter_writes_and_emits_once(tmp_path):
    s = open_protocol(tmp_path)
    try:
        seen: list[dict] = []
        unsubscribe = s.subscribe(seen.append)
        s.set_parameter("rc", "", "r", 1234.0, client="t")
        assert s.components["rc"].param("r") == 1234.0
        hidden = [e for e in seen if e.get("kind") == "hidden"]
        assert [(e["owner"], e["component"], e["parameter"], e["value"], e["source"]) for e in hidden] == [
            ("rc", "", "r", 1234.0, "t")
        ]
        assert s.seq == 0  # not in the communication record (D32, like a panel operation)
        unsubscribe()
    finally:
        s.close()


def test_set_parameter_can_occupy_a_drawn_parameter(tmp_path):
    s = open_protocol(tmp_path, deviation={"r": {"limit": 10.0, "unit": "%"}})
    try:
        assert s.part_deviations  # the draw landed
        s.set_parameter("rc", "", "r", 999.0)
        assert s.components["rc"].param("r") == 999.0  # hidden beats the drawn value
    finally:
        s.close()


def test_set_parameter_validates(tmp_path):
    s = open_protocol(tmp_path)
    try:
        with raises_with(ValueError, "no parameter"):
            s.set_parameter("rc", "", "nope", 1.0)
        with raises_with(KeyError, "no part"):
            s.set_parameter("nope", "", "r", 1.0)
        with raises_with(KeyError, "is a part"):
            s.set_parameter("rc", "nope", "r", 1.0)
        # #495: the value must fit the parameter's declared type, with the parameter and the
        # received value in the message; a finite number passes.
        for bad in ("abc", None, float("nan"), float("inf"), True):
            with raises_with(ValueError, "rc.r expects a finite number, got"):
                s.set_parameter("rc", "", "r", bad)
        s.set_parameter("rc", "", "r", 1234.0)
        assert s.components["rc"].param("r") == 1234.0
        # A numpy scalar is a number too (#501): draws and computed values are exactly those.
        import numpy as np

        for v in (np.int64(1000), np.float32(1000.5), np.float64(2000.0)):
            s.set_parameter("rc", "", "r", v)
        # A numeric parameter also takes a per-cell table (every leaf a finite number)…
        s.set_parameter("rc", "", "r", {"fast": 900.0, "slow": {"1": 1100.0}})
        # …and a bad leaf is named down to its cell.
        with raises_with(ValueError, "rc.r['slow']['2'] expects a finite number, got 'x'"):
            s.set_parameter("rc", "", "r", {"fast": 900.0, "slow": {"2": "x"}})
    finally:
        s.close()


def test_a_hidden_fault_value_is_checked_at_inject(tmp_path):
    """#495: the inject rejects a hidden fault whose value does not fit — not the later start,
    far from the caller."""
    s = open_protocol(tmp_path)
    try:
        with raises_with(ValueError, "expects a finite number"):
            s.faults.inject(Fault(kind="hidden", component="rc", param="r", value="abc"), source="t")
        assert s.faults.snapshot() == []  # nothing was scheduled
    finally:
        s.close()
