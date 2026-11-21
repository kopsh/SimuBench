"""The diagnostic-family contracts (issue 626, D57): defined once beside the engine code that
produces them, re-exported by contracts/api.py.

These tests pin the derivation: contracts/api.py hands out the engine's own classes, the engine's
reports already carry them, and the state-variable view derives from the definition's StateSpec
instead of a hand-copied mirror — so a new field is one edit on the engine side.
"""

from __future__ import annotations

import json
from typing import Any

from helpers import raises_with
from simubench.contracts import api as M
from simubench.contracts import tools as T
from simubench.engine.silence import SilenceReason
from simubench.engine.validate import Diagnostic, Report
from simubench.models.definition import StateSpec
from simubench.replay import ReplayMismatch, ReplayReport


def test_the_api_hands_out_the_engine_contracts():
    """api.py re-exports the classes; there is no second shape to keep in sync."""
    assert M.Diagnostic is Diagnostic
    assert M.SilenceReason is SilenceReason
    assert M.ReplayMismatch is ReplayMismatch


def test_a_report_carries_the_contract_diagnostics():
    """Report.add builds the same objects the API serves; the dump is the interface shape."""
    report = Report()
    report.add("error", "E-TEST", "/wires/0", "boom", "fix it", subject="awg")
    (d,) = report.diagnostics
    assert isinstance(d, Diagnostic)
    assert d.model_dump() == {
        "severity": "error",
        "code": "E-TEST",
        "pointer": "/wires/0",
        "message": "boom",
        "suggestion": "fix it",
        "subject": "awg",
    }


def test_the_contracts_are_frozen():
    """The contract objects are immutable interface values (D57), not mutable engine records."""
    d = Diagnostic(severity="error", code="E-X", pointer="/a", message="m")
    with raises_with(Exception, "frozen"):
        d.severity = "warning"
    assert hash(d)  # frozen models are hashable


def test_the_replay_report_keeps_contract_mismatches():
    report = ReplayReport(record="r", bench="b")
    report.mismatches.append(
        ReplayMismatch(seq=3, instrument="awg", t_ns=12, expected="1", actual="2", command=":FREQ?")
    )
    assert report.as_dict()["mismatches"] == [
        {"seq": 3, "instrument": "awg", "t_ns": 12, "expected": "1", "actual": "2", "command": ":FREQ?"}
    ]


def test_the_state_variable_view_derives_from_the_spec():
    """StateVarInfo is StateSpec plus the runtime view; the spec's fields are not hand-copied."""
    assert issubclass(M.StateVarInfo, StateSpec)
    assert set(M.StateVarInfo.model_fields) == set(StateSpec.model_fields) | {
        "path",
        "dangerous",
        "value",
        "channel",
        "fidelity",  # what users should know of how the setting is modeled (#1012)
    }
    info = M.StateVarInfo(path="ch1.freq", type="float", unit="HZ", value=1e3)
    assert info.path == "ch1.freq" and info.value == 1e3 and info.unit == "HZ"
    assert info.fidelity is None


def test_a_silence_reason_survives_the_round_trip():
    """why_silent's objects are the API's objects: no field-by-field copy in between."""
    x = SilenceReason(kind="unconnected", path=["awg.OUT1"], detail="awg.OUT1 is not wired")
    assert M.SilenceReason.model_validate(x.model_dump()) == x


def test_the_tui_fixtures_validate_against_the_contract_models():
    """The TUI's fast-check fixtures are payloads dumped out of a real session (tests/tui_mock);
    validating them here keeps the mirror honest: a contract field renamed turns the fixtures
    stale and the fast checks red, instead of the TUI finding out on the slow tier (#1769)."""
    from pathlib import Path

    data = Path(__file__).resolve().parents[1] / "tests/data/tui"
    for name in ("unit_smu.json", "unit_scope_rc.json"):
        fixture = json.loads((data / name).read_text(encoding="utf-8"))
        M.SessionInfo.model_validate(fixture["session"])
        instruments = fixture["instruments"]
        for instrument in instruments.values() if isinstance(instruments, dict) else instruments:
            M.InstrumentInfo.model_validate(instrument)
        M.ClockInfo.model_validate(fixture["clock"])
        M.NetLevels.model_validate(fixture["levels"])
        for instrument_readings in fixture["readings"].values():
            if instrument_readings is not None:  # a class that reads none carries none
                M.InstrumentReadings.model_validate(instrument_readings)
    # the catalog fixture is regenerated (and validated) with #1769's honest dump; main's is
    # still the pre-contract shape, so it is not validated here.


def test_the_assistant_tool_schema_comes_from_the_contract_model():
    """The assistant's create_bench is the contract model mirrored (#1770): its published tool
    schema — properties, required, descriptions — is the model's own, not a hand-copied
    signature. Before, the assistant re-spelled the three fields (and the description) by hand:
    editing the model left the assistant seam silently behind."""
    from pydantic_ai import Tool

    from simubench.server.assistant import tools as AT

    tool_schema = Tool(AT.create_bench).function_schema.json_schema
    model_schema = T.CreateBench.model_json_schema()

    # pydantic titles each field after its name; a tool schema does not carry field titles.
    def strip(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {k: {kk: vv for kk, vv in v.items() if kk != "title"} for k, v in schema["properties"].items()}

    assert strip(tool_schema) == strip(model_schema)
    assert tool_schema["required"] == model_schema["required"]
