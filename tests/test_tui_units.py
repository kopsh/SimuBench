"""Interface-level tests for the TUI's pure logic: milliseconds, no terminal boot, no network.

These are the fast tier: the pairing rules of the communication log (7.1), the source labels
(1.5), the not-executed wording (9.2), the reason dialog's reports (9.1), the number formats
(1.4), the event store's dedupe, and the screens' interface-level pieces (the fault dialog's
fields, the advance input, the nets rows, the steps file, the runs wording) — the interfaces
the screens render, one file for all of them (#699, formerly test_tui_t3_units.py and
test_tui_t4_units.py). The tests that need a running Textual app (screens, keys, snapshots)
carry the ``tui_boot`` marker and run on main.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from simubench.engine.events import project
from simubench.tui.client import ws_url
from simubench.tui.fmt import braille, cell_len, fmt, fmt_ns
from simubench.tui.log import build_lines, hint_text, pending_writes, source_label, visible
from simubench.tui.reasons import error_report, limited_report
from simubench.tui.screens.clockdialog import format_duration, parse_duration
from simubench.tui.screens.faultdialog import FIELDS, build_spec, fields_for_kind
from simubench.tui.screens.nets import net_rows, silence_text
from simubench.tui.screens.runs import (
    first_mismatch_text,
    fmt_run_duration,
    fmt_run_size,
    run_header,
    run_rows,
    selection_text,
)
from simubench.tui.script import Steps, duration_seconds, split_key, validate
from simubench.tui.store import BUFFER_LIMIT, EventStore

pytestmark = pytest.mark.unit  # the D49 budget guard applies (<=100 ms per test)

SMU_INFO: dict[str, Any] = {
    "name": "smu",
    "idn": "SIMUBENCH,SB-SMU-1,SBSMU000001,1.0.0",
    "state": [{"path": "source.voltage.level", "value": 15.0}],
}


def ev(seq: int, t_ns: int, kind: str, **fields: Any) -> dict[str, Any]:
    """One event exactly as the WS stream carries it: projected through the engine's event
    contract (issue 595), so the shape here and the server's cannot drift apart.

    Kept local on purpose: ``tui_mock`` imports the app (Textual), and the pure-logic tier of
    this file stays importable without it (D49's unit tier)."""
    return project({"t_ns": t_ns, "kind": kind, **fields}, seq)


def lines_of(*events: dict[str, Any]) -> list[Any]:
    return build_lines(list(events), "en")


# ---- the communication log's pairing (tui-screens.md 7.1) --------------------------------------------


def test_write_and_read_pair_into_one_line():
    lines = lines_of(
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":MEAS:CURR?"),
        ev(2, 20_000_000, "scpi", instrument="smu", dir="read", client="pyvisa", text="+1.000000E-02"),
    )
    assert len(lines) == 1
    assert lines[0].command == ":MEAS:CURR?" and lines[0].result_text().startswith("→ +1.0")


def test_a_write_without_a_reply_shows_the_dot():
    lines = lines_of(
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":OUTP ON")
    )
    assert len(lines) == 1 and lines[0].result_text() == "·"


def test_an_error_after_the_write_merges_into_its_line():
    lines = lines_of(
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":SOUR:VOLT 999"),
        ev(2, 20_000_000, "error", instrument="smu", client="pyvisa", code=-222, message="Data out of range"),
    )
    assert len(lines) == 1 and lines[0].has_error and lines[0].error_code == -222
    assert lines[0].result_text().startswith("✗ -222")


def test_an_ignored_command_is_not_an_error():
    lines = lines_of(
        ev(1, 20_000_000, "scpi", instrument="scope", dir="write", client="pyvisa", text="C1:BUZZ 1"),
        ev(2, 20_000_000, "ignored", instrument="scope", client="pyvisa", header="C1:BUZZ", query=False),
    )
    assert len(lines) == 1 and lines[0].ignored and not lines[0].has_error
    assert lines[0].result_text() == "·"


def test_the_embedded_log_shows_only_commands():
    events = [
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":OUTP ON"),
        ev(
            2,
            20_000_000,
            "state",
            instrument="smu",
            path="output.enabled",
            source="scpi",
            old=False,
            new=True,
        ),
        ev(3, 30_000_000, "advance", dt_ns=10_000_000),
        ev(4, 30_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":MEAS:VOLT?"),
        ev(5, 30_000_000, "scpi", instrument="smu", dir="read", client="pyvisa", text="+9.99"),
    ]
    lines = lines_of(*events)
    embedded = visible(lines, embedded=True)
    assert [ln.command for ln in embedded] == [":OUTP ON", ":MEAS:VOLT?"]
    assert [ln.command for ln in lines] == [":OUTP ON", "Panel: enabled", "Advance 10.000 ms", ":MEAS:VOLT?"]


# ---- the source labels (tui-screens.md 1.5) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("client", "label"),
    [
        ("ui", "UI"),
        ("tui", "Terminal"),
        ("mcp", "Agent"),
        ("pyvisa", "Script"),
        ("socket:127.0.0.1:53122", "Script :53122"),
        ("socket:192.168.1.5:1234", "Script 192.168.1.5:1234"),
        ("hislip:127.0.0.1:4880#0001", "Script :4880"),
        ("replay", "Replay"),
        ("", "Event"),
    ],
)
def test_source_labels(client: str, label: str):
    assert source_label({"client": client, "kind": "scpi"}, "en") == label


def test_source_labels_of_faults_and_advances():
    assert source_label({"kind": "fault", "source": "bench"}, "en") == "Preset"
    assert source_label({"kind": "advance"}, "en") == "Event"


# ---- the not-executed wording (tui-screens.md 9.2, #174's classifications) ------------------------------


def hint_line(**fields: Any) -> str | None:
    events = [
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":FOO 1"),
        ev(2, 20_000_000, "error", instrument="smu", client="pyvisa", **fields),
    ]
    return hint_text(lines_of(*events)[0], "en")


def test_hint_not_modeled_names_the_coverage_file():
    text = hint_line(
        code=-113, message="Undefined header", reason="not_modeled", origin="/x/sds.coverage.yaml"
    )
    assert text == "Not modeled · listed as not implemented in sds.coverage.yaml"


def test_hint_not_in_model_names_the_closest():
    text = hint_line(
        code=-113,
        message="Undefined header",
        reason="not_in_model",
        origin="sds.coverage.yaml",
        closest=":SYSTem:ERRor",
    )
    assert (
        text is not None and "Not in the coverage table sds.coverage.yaml" in text and "SYSTem:ERRor" in text
    )


def test_hint_not_in_definition():
    text = hint_line(
        code=-113,
        message="Undefined header",
        reason="not_in_definition",
        origin="sb-smu-1.yaml",
        closest=":SOURce:VOLTage",
    )
    assert text is not None and "Not in the definition sb-smu-1.yaml" in text


def test_hint_ignored():
    assert hint_line(reason="ignored") == "Ignored · the definition sets unknown_commands: ignore"


def test_hint_builtin_excluded():
    events = [
        ev(1, 20_000_000, "scpi", instrument="cal", dir="write", client="pyvisa", text="*TRG"),
        ev(
            2,
            20_000_000,
            "error",
            instrument="cal",
            client="pyvisa",
            code=-113,
            message="Undefined header",
            reason="builtin_excluded",
            origin="fluke-5522a.yaml",
        ),
    ]
    lines = lines_of(*events)
    assert hint_text(lines[0], "en") == "This model does not answer *TRG · builtins in the definition"


def test_hint_bad_parameter_carries_the_range():
    text = hint_line(
        code=-222,
        message="Data out of range",
        reason="bad_parameter",
        param="source.voltage.level",
        range="-210 … 210 V",
    )
    assert text == "Bad parameter: source.voltage.level must be within -210 … 210 V"


def test_an_ordinary_device_error_has_no_hint():
    assert hint_line(code=-300, message="Device-specific error") is None


# ---- the reason dialog's reports (tui-screens.md 9.1) ----------------------------------------------------


def test_limited_report_conclusion_and_traced_command():
    readings = {
        "values": {
            "core.limited": True,
            "core.regulation": "CC",
            "core.voltage": 9.999,
            "core.current": 0.01,
            "core.limit": 0.01,
        }
    }
    events = [
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":SOUR:VOLT 15"),
        ev(
            2,
            20_000_000,
            "state",
            instrument="smu",
            path="source.voltage.level",
            source="scpi",
            old=5.0,
            new=15.0,
        ),
    ]
    report = limited_report(SMU_INFO, readings, events, "en")
    assert report is not None and "constant current (CC)" in report.conclusion
    setting = report.evidences[0]
    assert ":SOUR:VOLT 15" in (setting.source or "") and "Script" in (setting.source or "")
    assert report.jump_seq == 1  # ⏎ jumps to the write's log line


def test_error_report_names_the_command_and_classification():
    events = [
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":SOUR:VOLT 999"),
        ev(
            2,
            20_000_000,
            "error",
            instrument="smu",
            client="pyvisa",
            code=-222,
            message="Data out of range",
            reason="bad_parameter",
            param="source.voltage.level",
            range="-210 … 210 V",
        ),
    ]
    lines = lines_of(*events)
    report = error_report(lines[0], "en")
    assert report.title == "Reason · smu error -222"
    assert report.hint is not None and "Bad parameter" in report.hint
    assert report.jump_seq == 1


# ---- number formats (tui-screens.md 1.4) and the event store ---------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (1000.0, "Hz", "1.0000 kHz"),
        (0.01, "A", "10.000 mA"),
        (15.0, "V", "15.000 V"),
        (0.0, "V", "0.0000 V"),
        (1e7, "OHM", "10.000 MΩ"),
        (0.001, "S", "1.0000 ms"),
        (float("inf"), "OHM", "High Z"),
    ],
)
def test_engineering_formats(value: float, unit: str, text: str):
    assert fmt(value, unit) == text


def test_fmt_of_none_is_the_em_dash():
    assert fmt(None) == "—"


def test_virtual_time_format():
    assert fmt_ns(0) == "00:00:00.000"
    assert fmt_ns(4_200_000_000) == "00:00:04.200"
    assert fmt_ns(3_723_004_000_000) == "01:02:03.004"


def test_the_store_dedupes_replayed_sequences_and_stays_bounded():
    store = EventStore(limit=8)
    assert store.add({"seq": 1}) and not store.add({"seq": 1})  # the backlog replays on reconnect
    for seq in range(2, 30):
        store.add({"seq": seq})
    assert len(store) == 8 and store.events[0]["seq"] == 22  # the oldest were dropped, not the newest
    assert BUFFER_LIMIT >= 10_000  # the session buffer the spec requires (7.2)


# ---- instrument screen values (#283) ---------------------------------------------------------------


def test_an_int_setting_reads_as_an_integer():
    from simubench.tui.controls import summary_text

    assert summary_text(1.0, {"type": "int"}, "en") == "1"
    assert summary_text(101, {"type": "int", "unit": "S"}, "en") == "101 s"


def test_an_angle_reads_in_degrees():
    assert fmt(0.0, "DEG") == "0.0000°"
    assert fmt(-45.5, "DEG") == "-45.500°"


def test_an_infinite_load_sent_as_inf_reads_as_on_the_overview_card():
    from simubench.tui.controls import summary_text

    spec = {"type": "float", "unit": "OHM"}
    assert summary_text("INF", spec, "en") == fmt(float("inf"), "OHM") == "High Z"


def test_each_generator_channel_box_gets_its_own_buttons():
    from simubench.tui.controls import generator_controls

    info = {
        "state": [
            {"path": f"ch{ch}.{k}", "channel": ch, "type": "float", "value": 0.0}
            for ch in (1, 2)
            for k in ("frequency", "amplitude")
        ]
    }
    assert [c.path for c in generator_controls(info, "en", 2)] == ["ch2.frequency", "ch2.amplitude"]
    assert len(generator_controls(info, "en")) == 4


def test_a_manual_value_under_its_auto_switch_is_not_in_effect():
    from simubench.tui.controls import inactive_under_auto

    state = {
        "bw.res": {"type": "float", "value": 3e6},
        "bw.res_auto": {"type": "bool", "value": True},
        "sweep.time": {"type": "float", "value": 1e-3},
        "sweep.time_auto": {"type": "bool", "value": False},
    }
    assert inactive_under_auto(state, "bw.res")
    assert not inactive_under_auto(state, "sweep.time")
    assert not inactive_under_auto(state, "freq.center")


def test_braille_rows_draw_a_peak_at_the_top_and_the_floor_at_the_bottom():
    """#282: the analyzer's trace — a peak in the middle reaches the top row, the floor stays in
    the bottom one, every row is exactly ``cols`` characters."""
    from simubench.tui.fmt import braille_rows

    wave = [-100.0] * 50 + [0.0] + [-100.0] * 49
    rows = braille_rows(wave, 20, 3)
    assert len(rows) == 3 and all(len(r) == 20 for r in rows)
    assert rows[0].strip("⠀") != ""  # the peak lights the top row
    assert rows[0][0] == "⠀" and rows[-1][0] != "⠀"  # the floor sits on the bottom row
    assert braille_rows([], 5, 2) == ["⠀" * 5] * 2


def test_a_panel_key_labelled_only_in_chinese_reads_the_enum_word():
    """D23 (#454): the definitions' option_labels are Chinese; the terminal shows the value's own
    word instead, while a Latin key label (the multimeter's `DC V`, `Ω 2W`) stays."""
    from simubench.tui.controls import panel_controls

    keys = [
        {"value": "OFF", "label": "独立"},
        {"value": "PARallel", "label": "并联"},
        {"value": "SERies", "label": "串联"},
        {"value": "DC", "label": "DC V"},
        {"value": "RES", "label": "Ω 2W"},
    ]
    group = {
        "label_en": "Measure",
        "controls": [{"state": "output.pair", "control": "buttons", "keys": keys}],
    }
    labels_en = [c.label for c in panel_controls(group, {"state": []}, "en")]
    assert labels_en == ["OFF", "PARallel", "SERies", "DC V", "Ω 2W"]
    assert [c.label for c in panel_controls(group, {"state": []}, "zh")] == [
        "独立",
        "并联",
        "串联",
        "DC V",
        "Ω 2W",
    ]


def test_a_softkey_labelled_only_in_chinese_reads_its_action():
    """D23 (#454): an English label wins, a Chinese-only one gives way to the action's name."""
    from simubench.tui.controls import softkey_controls

    panel = {
        "softkeys": [
            {"action": "run", "label": "运行", "label_en": "Run"},
            {"action": "stop", "label": "停止"},
        ]
    }
    assert [c.label for c in softkey_controls(panel, "en")] == ["Run", "stop"]
    assert [c.label for c in softkey_controls(panel, "zh")] == ["运行", "停止"]


def test_a_control_labelled_only_in_chinese_keeps_the_settings_own_label():
    """D23 (#454): an explicitly declared Chinese control label gives way to the setting's own
    English label instead of the path."""
    from simubench.tui.controls import panel_controls

    group = {"label_en": "G", "controls": [{"state": "out.level", "control": "number", "label": "电平"}]}
    info = {"state": [{"path": "out.level", "type": "float", "value": 1.0, "label_en": "Level"}]}
    assert [c.label for c in panel_controls(group, info, "en")] == ["Level"]


# ---- more pairing rules (from test_tui.py's log section, #699) ---------------------------------------


def test_a_reply_merges_with_its_write_across_other_events():
    """The reply merges with its write even when other events came in between (same client+instrument)."""
    lines = lines_of(
        ev(1, 25_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":MEAS:CURR?"),
        ev(2, 25_000_000, "state", instrument="smu", path="x", old=1, new=2, source="pyvisa"),
        ev(3, 25_000_000, "scpi", instrument="smu", dir="read", client="pyvisa", text="+1.0E-2"),
    )
    assert [line.kind for line in lines] == ["scpi", "state"]
    assert lines[0].result_text() == "→ +1.0E-2"  # the reply crossed the state event and still merged


def test_an_error_merges_whether_it_precedes_or_follows_the_write():
    """The engine emits the error during the command, before the write event; the spec words it
    as following the write. Both orders merge by instrument and tick."""
    events = [
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":SOUR:VOLT 999"),
        ev(2, 20_000_000, "error", instrument="smu", code=-222, message="Data out of range"),
    ]
    assert lines_of(*events)[0].result_text() == "✗ -222 Data out of range"
    assert lines_of(*reversed(events))[0].result_text() == "✗ -222 Data out of range"


def test_a_reply_after_an_error_keeps_the_error_display():
    lines = lines_of(
        ev(1, 20_000_000, "scpi", instrument="scope", dir="write", client="ui", text=":FOO"),
        ev(2, 20_000_000, "error", instrument="scope", code=-113, message="Undefined header"),
        ev(
            3, 20_000_000, "scpi", instrument="scope", dir="read", client="ui", text='-113,"Undefined header"'
        ),
    )
    assert lines[0].result_text() == "✗ -113 Undefined header"


def test_a_write_closes_after_one_second_of_virtual_time():
    """A write whose reply takes longer than 1 s of virtual time shows ·; the late read stands alone."""
    lines = lines_of(
        ev(1, 0, "scpi", instrument="smu", dir="write", client="pyvisa", text=":INIT"),
        ev(2, 2_000_000_000, "advance", dt_ns=2_000_000_000),
        ev(3, 2_000_000_000, "scpi", instrument="smu", dir="read", client="pyvisa", text="1"),
    )
    assert lines[0].result_text() == "·"
    assert [ln.kind for ln in lines] == ["scpi", "advance", "scpi"]
    assert lines[2].result_text() == "→ 1"


def test_the_next_write_closes_the_open_one():
    lines = lines_of(
        ev(1, 0, "scpi", instrument="smu", dir="write", client="pyvisa", text=":A"),
        ev(2, 10_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":B"),
        ev(3, 10_000_000, "scpi", instrument="smu", dir="read", client="pyvisa", text="b"),
    )
    assert [line.command for line in lines] == [":A", ":B"]
    assert lines[0].result_text() == "·" and lines[1].result_text() == "→ b"


def test_a_binary_read_is_summarized_with_its_size():
    lines = lines_of(
        ev(1, 31_000_000, "scpi", instrument="scope", dir="write", client="pyvisa", text=":WAV:DATA?"),
        ev(
            2,
            31_000_000,
            "scpi",
            instrument="scope",
            dir="read",
            client="pyvisa",
            size=10011,
            text="#9000010000… (10011 B)",
        ),
    )
    assert lines[0].result_text() == "→ #9000010000…  10 011 B"
    assert lines[0].binary_size == 10011


def test_pending_writes_are_the_busy_state():
    lines = lines_of(ev(1, 0, "scpi", instrument="smu", dir="write", client="pyvisa", text=":A"))
    assert pending_writes(lines)


def test_the_embedded_view_hides_every_non_command_line():
    events = [
        ev(1, 0, "scpi", instrument="smu", dir="write", client="ui", text=":A"),
        ev(2, 0, "state", instrument="smu", path="p", old=1, new=2, source="ui"),
        ev(3, 0, "fault", fault="f-1", reason="active", message="trip", source="bench"),
    ]
    assert [ln.command for ln in visible(lines_of(*events), embedded=True)] == [":A"]
    assert len(visible(lines_of(*events), embedded=False)) == 3


# ---- the event store's replay dedupe and drop counting (from test_tui.py, #699) -----------------------


def test_the_store_dedupes_a_replayed_backlog():
    store = EventStore()
    events = [
        ev(1, 0, "scpi", instrument="awg", dir="write", client="ui", text="x"),
        ev(2, 0, "scpi", instrument="awg", dir="read", client="ui", text="y"),
        ev(3, 0, "state", instrument="awg", path="p", old=1, new=2, source="ui"),
    ]
    assert store.add_many(events) == 3
    assert store.add_many(events) == 0  # full replay on reconnect
    assert store.add_many(events[:1]) == 0
    assert store.add(ev(4, 0, "scpi", instrument="awg", dir="write", client="ui", text="z")) is True
    assert len(store) == 4


def test_the_store_counts_its_dropped_events():
    store = EventStore(limit=10)
    for i in range(1, 26):
        store.add(ev(i, i, "advance", dt_ns=i))
    assert len(store) == 10
    assert store.dropped == 15
    assert store.events[0]["seq"] == 16  # the newest survive


# ---- the braille thumbnail (the scope card's one-row preview) ------------------------------------------


def test_braille_thumbnail_renders_one_row():
    wave = [0, 0.5, 1, 0.5, 0, -0.5, -1, -0.5] * 4
    out = braille(wave, 6)
    assert len(out) == 6
    assert any(ch != "⠀" for ch in out)
    assert braille([], 4) == "⠀" * 4


# ---- the fault dialog's fields and payload (tui-screens.md 8; from test_tui_t3_units.py, #699) ---------


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


# ---- the advance input (tui-screens.md 11.2; from test_tui_t3_units.py, #699) ---------------------------


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


# ---- the nets screen's rows and silence wording (tui-screens.md 11.1, 6.3; from t3_units, #699) --------


def _nets_session() -> dict[str, Any]:
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
    rows, unconnected = net_rows(_nets_session(), levels, "en")
    assert [r["net"] for r in rows] == ["net-0", "net-1"]
    assert rows[0]["active"] is True and "697.10 mV" in rows[0]["value"]
    assert rows[1]["active"] is False and rows[1]["value"] == "5.0000 V"  # a DC net shows its DC
    assert "awg.CH2" in unconnected


def test_silence_wording_per_reason_kind():
    text = silence_text(
        [{"kind": "source_off", "source": "awg"}, {"kind": "fault", "fault_id": "f-2"}],
        "en",
    )
    assert "awg" in text and "f-2" in text and "fault" in text


# ---- the steps file (tui-screens.md 6; from test_tui_t4_units.py, #699) ---------------------------------


def _steps(tmp_path: Path, data: dict) -> Steps:
    path = tmp_path / "steps.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Steps.load(path)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_steps_load_size_and_items(tmp_path: Path):
    steps = _steps(
        tmp_path,
        {"size": [120, 36], "steps": [{"caption": "started"}, {"key": "enter"}, {"screenshot": "overview"}]},
    )
    assert steps.size == (120, 36)
    assert [list(s) for s in steps.items] == [["caption"], ["key"], ["screenshot"]]


def test_steps_default_size(tmp_path: Path):
    assert Steps.load(_write(tmp_path, {"steps": []})).size == (100, 32)


def test_validate_accepts_a_good_file(tmp_path: Path):
    steps = Steps.load(
        _write(
            tmp_path,
            {
                "steps": [
                    {"caption": "x", "wait": 1.5},
                    {"key": "1"},
                    {"scpi": {"instrument": "smu", "command": ":SYSTem:ERRor?"}},
                    {"advance": 0.01},
                    {"highlight": ["source.voltage.level"]},
                    {"screenshot": "end"},
                ]
            },
        )
    )
    assert validate(steps) == []


def test_validate_accepts_the_design_example(tmp_path: Path):
    """`key` and `caption` on one step, with `wait` and `highlight` too (tui-design.md 6)."""
    steps = Steps.load(
        _write(
            tmp_path,
            {
                "steps": [
                    {"key": "enter", "caption": "Open it", "wait": "1s", "highlight": ["output.enabled"]},
                ]
            },
        )
    )
    assert validate(steps) == []


def test_validate_rejects_unknown_or_empty_steps(tmp_path: Path):
    steps = Steps.load(_write(tmp_path, {"steps": [{"keyp": "1"}, {}, {"wait": 1}]}))
    problems = validate(steps)
    assert len(problems) == 3
    assert "unknown field" in problems[0] and "nothing to do" in problems[1]


def test_validate_rejects_bad_field_types(tmp_path: Path):
    steps = Steps.load(
        _write(tmp_path, {"steps": [{"key": 5}, {"scpi": {"instrument": "smu"}}, {"highlight": "x"}]})
    )
    assert len(validate(steps)) == 3


def test_wait_and_advance_accept_seconds_and_suffixes(tmp_path: Path):
    steps = Steps.load(
        _write(
            tmp_path,
            {
                "steps": [
                    {"caption": "x", "wait": "1.5s"},
                    {"key": "1", "wait": "200ms"},
                    {"advance": "10 ms"},
                    {"screenshot": "end", "wait": 0.25},
                ]
            },
        )
    )
    assert validate(steps) == []  # the design's `wait: 1.5s` form is accepted (6)
    assert duration_seconds("1.5s") == 1.5
    assert duration_seconds("200ms") == 0.2
    assert duration_seconds("10 ms") == 0.01
    assert duration_seconds(2) == 2.0
    assert duration_seconds("soon") is None and duration_seconds(-1) is None


def test_split_key_named_vs_typed():
    assert split_key("enter") == ["enter"]
    assert split_key("question_mark") == ["question_mark"]
    assert split_key("4") == ["4"]
    assert split_key("abc") == ["a", "b", "c"]


# ---- the runs screen's interface (tui-screens.md 11.3; from test_tui_t4_units.py, #699) ------------------


def test_run_rows_keep_the_columns_and_name_the_live_session():
    runs = [
        {
            "run_id": "r2",
            "bench": "smu",
            "started": 1793577600,
            "ended": None,
            "clock": "command",
            "seed": 5,
            "events": 56,
            "duration_ns": 31_000_000,
            "size": 1_500_000,
        },
        {
            "run_id": "r1",
            "bench": "siglent_scope_rc",
            "started": 1793491200,
            "ended": 1793491230,
            "clock": "command",
            "seed": 5,
            "events": 9,
            "duration_ns": 25_000_000,
            "size": 3_072,
        },
    ]
    rows = run_rows(runs, None, "en")
    header = run_header("en")
    assert rows[0].startswith("20") and "live" not in "\n".join(rows)  # the selection line marks it
    assert all(cell_len(row) == cell_len(header) for row in rows)  # one width: nothing wraps
    assert "1.4 MB" in rows[0] and "3 KB" in rows[1] and "0.025 s" in rows[1]
    assert "live (this session)" in selection_text(runs[0], "en")
    assert "live" not in selection_text(runs[1], "en")


def test_duration_and_size_wording():
    assert fmt_run_duration(4_210_000_000) == "4.210 s"
    assert fmt_run_duration(0) == "0.000 s"
    assert fmt_run_size(3_072) == "3 KB"
    assert fmt_run_size(217_088) == "212 KB"
    assert fmt_run_size(1_500_000) == "1.4 MB"
    assert fmt_run_size(512) == "512 B"


def test_replay_wording_ok_and_first_mismatch():
    ok = first_mismatch_text({"actions": 56, "replies": 56, "mismatches": []}, "en")
    assert "56" in ok and "all equal" in ok
    bad = first_mismatch_text(
        {
            "actions": 56,
            "replies": 56,
            "mismatches": [
                {
                    "seq": 31,
                    "instrument": "scope",
                    "command": ":WAV:DATA?",
                    "expected": "0x7F",
                    "actual": "0x80",
                }
            ],
        },
        "en",
    )
    assert "#31" in bad and "scope" in bad and "0x7F" in bad and "0x80" in bad


# ---- app construction, the CLI guard and the pure helpers (from test_tui.py, #699) ----------------------


def test_no_color_selects_the_mono_theme(monkeypatch):
    from simubench.tui.app import SimuBenchApp

    monkeypatch.setenv("NO_COLOR", "1")
    app = SimuBenchApp("http://127.0.0.1:1", sid="x")
    assert app.theme == "simubench-mono"
    monkeypatch.delenv("NO_COLOR")
    app2 = SimuBenchApp("http://127.0.0.1:1", sid="x")
    assert app2.theme == "simubench"


def test_lang_default_follows_env(monkeypatch):
    from simubench.tui.i18n import default_lang

    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("SIMUBENCH_LANG", raising=False)
    assert default_lang() == "en"
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert default_lang() == "en"
    monkeypatch.delenv("LANG")
    assert default_lang() == "en"  # the fallback


def test_ws_url_carries_the_token():
    assert ws_url("http://127.0.0.1:8000", "s1") == "ws://127.0.0.1:8000/api/sessions/s1/events"
    assert ws_url("http://127.0.0.1:8000", "s1", "t") == "ws://127.0.0.1:8000/api/sessions/s1/events?token=t"
    assert (
        ws_url("https://bench.example.com", "s1", "t")
        == "wss://bench.example.com/api/sessions/s1/events?token=t"
    )


def test_the_scaled_clock_shows_its_rate_in_the_activity_status():
    from simubench.tui.widgets import clock_activity

    class App:
        clock: ClassVar = {"mode": "scaled", "now_ns": 0, "scale": 10.0}

    assert clock_activity(App(), "en", "scaled", busy=False)[-1][0] == "clock running ×10"
    assert clock_activity(App(), "en", "wall", busy=False)[-1][0] == "clock running"
