"""Change descriptions between two versions of a definition (T3, instrument-authoring.md 7.2): the
comparison speaks the instrument's language, not YAML, and misses nothing that matters."""

from __future__ import annotations

from simubench.server.definitionchanges import definition_changes

BASE = {
    "schema": "simubench.instrument/1",
    "id": "acme-x1",
    "description": "一台虚构的万用表",
    "identity": {"idn": "ACME,X1,{serial}", "serial": "S1"},
    "channels": {"count": 1},
    "state": {
        "sense.function": {"type": "options", "options": ["DC", "AC"], "default": "DC"},
        "sense.nplc": {"type": "number", "min": 0.01, "max": 100, "default": 1},
    },
    "components": [
        {"name": "input", "type": "input_port", "params": {"z_in": 10.0e6}},
        {"name": "meter", "type": "dmm_core", "params": {"ranges": {"DC": [0.1, 1, 10, 100]}}},
    ],
    "commands": [
        {"header": ":MEASure:VOLTage[:DC]", "query": {"reply": "+1.00000000E+00"}},
        {"header": ":SYSTem:ERRor?", "query": {"reply": '0,"no error"'}},
    ],
    "specs": {"dc_accuracy": "0.0035% of reading + 0.0005% of range"},
    "deviations": [{"param": "meter.gain", "limit": 0.0001, "unit": "ppm"}],
    "display": {"summary": ["acme"]},
}


def changed(**sections: object) -> dict:
    return {**BASE, **sections}


def as_tuples(changes: list) -> list[tuple]:
    return [(c.kind, c.action, c.target, c.detail) for c in changes]


def test_a_changed_command_reply_is_described():
    commands = [
        {"header": ":MEASure:VOLTage[:DC]", "query": {"reply": "+2.00000000E+00"}},
        {"header": ":SYSTem:ERRor?", "query": {"reply": '0,"no error"'}},
    ]
    [item] = definition_changes(BASE, changed(commands=commands))
    assert (item.kind, item.action, item.target, item.detail) == (
        "command",
        "changed",
        ":MEASure:VOLTage[:DC]",
        "query.reply",
    )
    assert item.old == "+1.00000000E+00" and item.new == "+2.00000000E+00"


def test_a_state_is_added_removed_and_its_range_changes():
    out = as_tuples(
        definition_changes(
            BASE,
            changed(
                state={
                    "sense.function": {"type": "options", "options": ["DC", "AC"], "default": "DC"},
                    "sense.nplc": {"type": "number", "min": 0.01, "max": 10, "default": 1},
                    "sense.zero": {"type": "bool", "default": False},
                }
            ),
        )
    )
    assert ("state", "changed", "sense.nplc", "max") in out
    assert ("state", "added", "sense.zero", None) in out
    assert len(out) == 2


def test_a_range_gains_a_value_and_an_impedance_changes():
    components = [
        {"name": "input", "type": "input_port", "params": {"z_in": 1.0e9}},
        {"name": "meter", "type": "dmm_core", "params": {"ranges": {"DC": [0.1, 1, 10, 100, 1000]}}},
    ]
    out = definition_changes(BASE, changed(components=components))
    assert [(c.action, c.target, c.detail, c.old, c.new) for c in out] == [
        ("changed", "input.z_in", None, 10.0e6, 1.0e9),  # 输入阻抗 10 MΩ → 1 GΩ
        ("added", "meter.ranges", "DC", None, 1000),  # 直流量程增加 1000 V
    ]


def test_a_reordered_range_list_is_no_change():
    components = [
        {"name": "input", "type": "input_port", "params": {"z_in": 10.0e6}},
        {"name": "meter", "type": "dmm_core", "params": {"ranges": {"DC": [100, 10, 1, 0.1]}}},
    ]
    assert definition_changes(BASE, changed(components=components)) == []


def test_a_deviation_limit_and_identity_and_specs_changes():
    out = as_tuples(
        definition_changes(
            BASE,
            changed(
                deviations=[{"param": "meter.gain", "limit": 0.0005, "unit": "ppm"}],
                identity={"idn": "ACME,X1B,{serial}", "serial": "S1"},
                specs={"dc_accuracy": "0.0030% of reading + 0.0005% of range"},
            ),
        )
    )
    assert ("deviation", "changed", "meter.gain", "limit") in out
    assert ("identity", "changed", "identity", "idn") in out
    assert ("specs", "changed", "specs", "dc_accuracy") in out
    assert len(out) == 3


def test_a_description_only_change_is_one_item():
    [item] = definition_changes(BASE, changed(description="一台虚构的台式万用表"))
    assert (item.kind, item.action, item.target) == ("description", "changed", "description")
    assert item.old == "一台虚构的万用表" and item.new == "一台虚构的台式万用表"


def test_template_inputs_and_a_port_are_described():
    out = definition_changes(
        {"template": {"id": "scope", "version": 1, "inputs": {"channels": 2}}},
        {"template": {"id": "scope", "version": 2, "inputs": {"channels": 4}}},
    )
    assert [(c.kind, c.detail, c.old, c.new) for c in out] == [
        ("template", "inputs.channels", 2, 4),
        ("template", "version", 1, 2),
    ]
    [port] = definition_changes(
        {"ports": {"instrument": {"OUT": "src.out"}}}, {"ports": {"instrument": {"OUT": "amp.out"}}}
    )
    assert (port.kind, port.target, port.detail, port.old, port.new) == (
        "ports",
        "ports",
        "instrument.OUT",
        "src.out",
        "amp.out",
    )


def test_key_order_is_no_change():
    """The comparison runs on parsed data: reordering keys (as a rewritten file would) says nothing."""
    import yaml

    reordered = yaml.safe_load(yaml.safe_dump(BASE, sort_keys=True))
    assert definition_changes(BASE, reordered) == []


def test_a_created_definition_describes_everything():
    out = definition_changes({}, BASE)
    kinds = {c.kind for c in out}
    assert {"command", "state", "component", "identity", "specs", "deviation", "description"} <= kinds
    assert all(c.action == "added" for c in out)
