"""Pieces of definition authoring (instrument-authoring.md 5.4, issue 46): JSON Patch and drafts."""

from __future__ import annotations

import pytest

from simubench.engine.jsonpatch import PatchError, PatchOp, apply
from simubench.engine.scriptscan import _guess, draft_definition
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.loader import DefinitionLibrary


def ops(*items):
    return [PatchOp.model_validate(i) for i in items]


def test_json_patch_operations():
    doc = {"a": {"b": [1, 2]}, "c": "x"}
    out = apply(
        doc,
        ops(
            {"op": "add", "path": "/a/b/-", "value": 3},
            {"op": "add", "path": "/a/b/0", "value": 0},
            {"op": "replace", "path": "/c", "value": "y"},
            {"op": "copy", "from": "/c", "path": "/d"},
            {"op": "move", "from": "/a/b", "path": "/e"},
            {"op": "remove", "path": "/a"},
            {"op": "test", "path": "/e/3", "value": 3},
            {"op": "add", "path": "/k~1j", "value": 1},
        ),
    )
    assert out == {"c": "y", "d": "y", "e": [0, 1, 2, 3], "k/j": 1}
    assert doc == {"a": {"b": [1, 2]}, "c": "x"}  # not changed


@pytest.mark.parametrize(
    ("op", "message"),
    [
        ({"op": "remove", "path": "/x"}, "nothing to remove"),
        ({"op": "replace", "path": "/a/5", "value": 1}, "beyond the list"),
        ({"op": "test", "path": "/a/0", "value": 2}, "test failed"),
        ({"op": "add", "path": "a", "value": 1}, "starts with '/'"),
        ({"op": "move", "from": "/a", "path": "/a/0"}, "into itself"),
    ],
)
def test_json_patch_errors_change_nothing(op, message):
    with pytest.raises(PatchError, match=message):
        apply({"a": [1]}, ops({"op": "add", "path": "/b", "value": 1}, op))


@pytest.mark.parametrize(
    ("values", "spec"),
    [
        (["ON", "OFF"], {"type": "bool", "default": True}),
        (["10 V"], {"type": "float", "default": 10.0, "unit": "V"}),
        (["200 mV"], {"type": "float", "default": 0.0, "unit": "V"}),
        (["1e3", "2.5"], {"type": "float", "default": 1000.0}),
        (["SINusoid", "SQU"], {"type": "enum", "options": ["SINUSOID", "SQU"], "default": "SINUSOID"}),
        (['"hello"'], {"type": "string", "default": "hello"}),
        (["0", "1"], {"type": "float", "default": 0.0}),
    ],
)
def test_types_are_guessed_from_the_values_written(values, spec):
    assert _guess(values) == spec


SCRIPT = """
import pyvisa
rm = pyvisa.ResourceManager()
awg = rm.open_resource("TCPIP0::1.2.3.4::INSTR")
awg.write(":SOURce1:FREQuency 1000")
awg.write(":SOURce2:SWEep:TIME 2 S")
awg.write(":OUTPut1 ON;:SOURce1:BURSt:NCYCles 3")
awg.query(":SOURce1:BURSt:NCYCles?")
"""


def test_a_draft_extends_a_base_definition_with_what_it_lacks():
    base = DefinitionLibrary().get("sb-awg-2")
    out = draft_definition(SCRIPT, "my-awg", base=base)
    assert [d["header"] for d in out["drafted"]] == [":SOURCE<ch>:SWEEP:TIME", ":SOURCE<ch>:BURST:NCYCLES"]
    data = out["definition"]
    assert data["id"] == "my-awg" and data["channel_state"]["source_sweep_time"]["unit"] == "S"
    assert data["channel_state"]["source_burst_ncycles"]["label"].startswith("草稿（待确认）")
    assert len(data["commands"]) == len(base.commands) + 2
    _, report = validate_definition_data(data)
    assert report.ok, [d.as_dict() for d in report.diagnostics]


def test_a_script_with_several_instruments_needs_a_receiver():
    two = SCRIPT + 'dmm = rm.open_resource("GPIB0::22::INSTR")\ndmm.write("RANGE 10")\n'
    with pytest.raises(ValueError, match="receiver"):
        draft_definition(two, "x")
    assert draft_definition(two, "x", receiver="dmm")["drafted"][0]["header"] == ":RANGE"
