"""Interface-level tests for the TUI's pure logic: milliseconds, no Textual, no network.

These are the fast tier: the pairing rules of the communication log (7.1), the source labels
(1.5), the not-executed wording (9.2), the reason dialog's reports (9.1), the number formats
(1.4) and the event store's dedupe — the interfaces the screens render. The tests that need a
running Textual app (screens, keys, snapshots) carry the ``tui_boot`` marker and run on main.
"""

from __future__ import annotations

from typing import Any

import pytest

from simubench.engine.events import project
from simubench.tui.fmt import fmt, fmt_ns
from simubench.tui.log import build_lines, hint_text, source_label, visible
from simubench.tui.reasons import error_report, limited_report
from simubench.tui.store import BUFFER_LIMIT, EventStore

pytestmark = pytest.mark.unit  # the D49 budget guard applies (<=100 ms per test)

SMU_INFO: dict[str, Any] = {
    "name": "smu",
    "idn": "SIMUBENCH,SB-SMU-1,SBSMU000001,1.0.0",
    "state": [{"path": "source.voltage.level", "value": 15.0}],
}


def ev(seq: int, t_ns: int, kind: str, **fields: Any) -> dict[str, Any]:
    """One event exactly as the WS stream carries it: projected through the engine's event
    contract (issue 595), so the shape here and the server's cannot drift apart."""
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
        ("hislip:127.0.0.1:4880#0001", "Script :4880"),
        ("vxi11:127.0.0.1:53124#0002", "Script :53124"),
        ("replay", "Replay"),
        ("", "Event"),
    ],
)
def test_source_labels(client: str, label: str):
    assert source_label({"client": client, "kind": "scpi"}, "en") == label


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
    [(1000.0, "Hz", "1.0000 kHz"), (0.01, "A", "10.000 mA"), (15.0, "V", "15.000 V"), (0.0, "V", "0.0000 V")],
)
def test_engineering_formats(value: float, unit: str, text: str):
    assert fmt(value, unit) == text


def test_virtual_time_format():
    assert fmt_ns(4_200_000_000) == "00:00:04.200"


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
