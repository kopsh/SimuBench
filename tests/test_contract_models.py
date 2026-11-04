"""The diagnostic-family contracts (issue 626, D57): defined once beside the engine code that
produces them, re-exported by models/api.py.

These tests pin the derivation: models/api.py hands out the engine's own classes, the engine's
reports already carry them, and the state-variable view derives from the definition's StateSpec
instead of a hand-copied mirror — so a new field is one edit on the engine side.
"""

from __future__ import annotations

import pytest

from simubench.engine.replay import ReplayMismatch, ReplayReport
from simubench.engine.silence import SilenceReason
from simubench.engine.validate import Diagnostic, Report
from simubench.models import api as M
from simubench.models.definition import StateSpec


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
    with pytest.raises(Exception, match="frozen"):
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
    }
    info = M.StateVarInfo(path="ch1.freq", type="float", unit="HZ", value=1e3)
    assert info.path == "ch1.freq" and info.value == 1e3 and info.unit == "HZ"


def test_a_silence_reason_survives_the_round_trip():
    """why_silent's objects are the API's objects: no field-by-field copy in between."""
    x = SilenceReason(kind="unconnected", path=["awg.OUT1"], detail="awg.OUT1 is not wired")
    assert M.SilenceReason.model_validate(x.model_dump()) == x
