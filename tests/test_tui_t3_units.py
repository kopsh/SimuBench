"""Interface-level tests for the T3 screens (D49's unit tier): no Textual, milliseconds.

The fault dialog's field mapping and spec building (tui-screens.md 8), the advance input's
parsing and formatting (11.2), and the nets screen's row building and silence wording (11.1,
6.3) — everything the screens render, verified without a running app.
"""

from __future__ import annotations

from typing import Any

import pytest

from simubench.tui.screens.clockdialog import format_duration, parse_duration
from simubench.tui.screens.faultdialog import FIELDS, build_spec, fields_for_kind
from simubench.tui.screens.nets import net_rows, silence_text

pytestmark = pytest.mark.unit  # the D49 budget guard applies (<=100 ms per test)


# ---- the fault dialog's fields and payload (tui-screens.md 8) -----------------------------------------


def test_every_fault_kind_has_a_field_set():
    kinds = ("timeout", "disconnect", "garble", "latency", "busy", "error", "trip", "drift", "hidden")
    assert set(FIELDS) == set(kinds)
    for kind in kinds:
        assert fields_for_kind(kind)


def test_communication_spec_keeps_only_its_fields():
    spec = build_spec("timeout", "smu", {"count": "2", "code": "300", "duration_s": "5"})
    assert spec == {"kind": "timeout", "instrument": "smu", "count": 2, "duration_s": 5.0}


def test_instrument_spec_carries_code_and_message():
    spec = build_spec("trip", "smu", {"code": "-300", "message": "Overvoltage"})
    assert spec == {"kind": "trip", "instrument": "smu", "code": -300, "message": "Overvoltage"}


def test_component_spec_defaults_to_the_nets_first_component():
    spec = build_spec("drift", None, {"param": "r", "drift_per_s": "1e-4"}, components=["rc"])
    assert spec == {"kind": "drift", "param": "r", "drift_per_s": 1e-4, "component": "rc"}


def test_empty_inputs_stay_unset_and_start_at_once():
    spec = build_spec("garble", "scope", {"garble_rate": "", "at_s": ""})
    assert spec == {"kind": "garble", "instrument": "scope"}


# ---- the advance input (tui-screens.md 11.2) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("1 s", 1.0), ("10 ms", 0.01), ("500us", 5e-4), ("1.5", 1.5), ("2 min", 120.0)],
)
def test_parse_duration_accepts_what_an_engineer_types(text: str, seconds: float):
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "abc", "0 s", "-1 s", "5 parsec"])
def test_parse_duration_rejects_the_rest(text: str):
    assert parse_duration(text) is None


def test_format_duration_round_trips_the_default():
    assert format_duration(1.0) == "1 s"
    assert format_duration(0.01) == "10 ms"
    assert parse_duration(format_duration(5e-4)) == pytest.approx(5e-4)


# ---- the nets screen's rows and silence wording (tui-screens.md 11.1, 6.3) -----------------------------


def _session() -> dict[str, Any]:
    return {
        "instruments": [
            {"name": "awg", "ports": [{"name": "CH1", "net": "net-0"}, {"name": "CH2", "net": None}]},
            {"name": "scope", "ports": [{"name": "CH1", "net": "net-1"}]},
        ],
        "parts": [{"name": "rc", "ports": [{"name": "IN", "net": "net-0"}, {"name": "OUT", "net": "net-1"}]}],
        "nets": [
            {"id": "net-0", "members": ["awg.CH1", "rc.IN"]},
            {"id": "net-1", "members": ["rc.OUT", "scope.CH1"]},
        ],
    }


def test_net_rows_list_endpoints_levels_and_the_unconnected():
    levels = {
        "nets": [
            {"net": "net-0", "rms": 0.6971, "dc": 0.0, "active": True},
            {"net": "net-1", "rms": 0.0, "dc": 5.0, "active": False},
        ]
    }
    rows, unconnected = net_rows(_session(), levels, "zh")
    assert [r["net"] for r in rows] == ["net-0", "net-1"]
    assert rows[0]["active"] is True and "697.10 mV" in rows[0]["value"]
    assert rows[1]["active"] is False and rows[1]["value"] == "5.0000 V"  # a DC net shows its DC
    assert "awg.CH2" in unconnected


def test_silence_wording_per_reason_kind():
    text = silence_text(
        [{"kind": "source_off", "source": "awg"}, {"kind": "fault", "fault_id": "f-2"}],
        "zh",
    )
    assert "awg" in text and "f-2" in text and "故障" in text
