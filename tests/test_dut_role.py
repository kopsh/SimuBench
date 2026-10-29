"""DUT is a role, not a kind of object: any instrument or part may be the DUT (#100)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from simubench.engine.edit import EditError, EditOp, apply
from simubench.models.bench import BenchDefinition
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
OPS = TypeAdapter(list[EditOp])


def ops(*items: dict) -> list:
    return OPS.validate_python(list(items))


def bench() -> BenchDefinition:
    return BenchDefinition.model_validate(
        {
            "name": "cal",
            "instruments": {"src": {"model": "sb-smu-1"}, "dmm": {"model": "sb-dmm-1"}},
            "parts": {"r1": {"type": "load_resistor", "params": {"r": 1000.0}}},
        }
    )


def test_an_instrument_can_be_the_dut():
    b = apply(bench(), ops({"op": "set_dut", "name": "dmm"}))
    assert b.duts() == ["dmm"] and b.instruments["dmm"].dut and not b.parts["r1"].dut
    b = apply(b, ops({"op": "set_dut", "name": "r1"}, {"op": "set_dut", "name": "dmm", "dut": False}))
    assert b.duts() == ["r1"]
    with pytest.raises(EditError, match="仪表或元件"):
        apply(b, ops({"op": "set_dut", "name": "nope"}))


def test_add_part_and_the_older_add_dut():
    b = apply(
        bench(),
        ops(
            {"op": "add_part", "name": "fixture", "type": "resistor"},
            {"op": "add_dut", "name": "rc", "type": "rc_lowpass"},
            {"op": "set_params", "part": "fixture", "params": {"r": 50.0}},
            {"op": "set_params", "dut": "rc", "params": {"r": 2000.0}},  # the older field name
        ),
    )
    assert not b.parts["fixture"].dut and b.parts["rc"].dut  # add_dut means a part that is the DUT
    assert b.parts["fixture"].params["r"] == 50.0 and b.parts["rc"].params["r"] == 2000.0
    assert b.duts() == ["rc"]


async def test_the_session_view_and_files_name_the_duts(tmp_path):
    shutil.copy(ROOT / "validation" / "benches" / "awg_rc_scope.bench.yaml", tmp_path / "a.bench.yaml")
    manager = SessionManager(tmp_path, scpi_base_port=None)
    doc = manager.files.read("a.bench.yaml")
    assert doc.data["schema"] == "simubench.bench/2" and doc.data["parts"]["rc"]["dut"] is True
    served = await manager.start("a.bench.yaml")
    try:
        view = manager.describe(served)
        assert view.duts == ["rc"]
        assert [p.name for p in view.parts if p.dut] == ["rc"]
        assert not any(i.dut for i in view.instruments)
    finally:
        await manager.stop(served.id)


def test_an_older_file_is_shown_in_the_current_format(tmp_path):
    (tmp_path / "old.bench.yaml").write_text(
        "schema: simubench.bench/1\nname: old\nduts:\n  rc: {type: rc_lowpass}\n", encoding="utf-8"
    )
    doc = SessionManager(tmp_path, scpi_base_port=None).files.read("old.bench.yaml")
    assert doc.ok and "duts" not in doc.data and doc.data["parts"]["rc"]["dut"] is True
