"""Interface-level tests for the TUI's pure logic: milliseconds, no Textual, no network.

These are the fast tier: the pairing rules of the communication log (7.1), the source labels
(1.5), the not-executed wording (9.2), the reason dialog's reports (9.1), the number formats
(1.4) and the event store's dedupe — the interfaces the screens render. The tests that need a
running Textual app (screens, keys, snapshots) carry the ``tui_boot`` marker and run on main.
"""

from __future__ import annotations

from typing import Any

import pytest

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
    """One event exactly as the server's ``to_event`` produces it."""
    out: dict[str, Any] = {"seq": seq, "t_ns": t_ns, "kind": kind}
    out.update(fields)
    return out


def lines_of(*events: dict[str, Any]) -> list[Any]:
    return build_lines(list(events), "zh")


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
    assert [ln.command for ln in lines] == [":OUTP ON", "面板：enabled", "推进 10.000 ms", ":MEAS:VOLT?"]


# ---- the source labels (tui-screens.md 1.5) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("client", "label"),
    [
        ("ui", "界面"),
        ("tui", "终端"),
        ("mcp", "Agent"),
        ("pyvisa", "脚本"),
        ("socket:127.0.0.1:53122", "脚本 :53122"),
        ("hislip:127.0.0.1:4880#0001", "脚本 :4880"),
        ("replay", "回放"),
        ("", "事件"),
    ],
)
def test_source_labels(client: str, label: str):
    assert source_label({"client": client, "kind": "scpi"}, "zh") == label


# ---- the not-executed wording (tui-screens.md 9.2, #174's classifications) ------------------------------


def hint_line(**fields: Any) -> str | None:
    events = [
        ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":FOO 1"),
        ev(2, 20_000_000, "error", instrument="smu", client="pyvisa", **fields),
    ]
    return hint_text(lines_of(*events)[0], "zh")


def test_hint_not_modeled_names_the_coverage_file():
    text = hint_line(
        code=-113, message="Undefined header", reason="not_modeled", origin="/x/sds.coverage.yaml"
    )
    assert text == "未建模 · 覆盖表 sds.coverage.yaml 中标为未实现"


def test_hint_not_in_model_names_the_closest():
    text = hint_line(
        code=-113,
        message="Undefined header",
        reason="not_in_model",
        origin="sds.coverage.yaml",
        closest=":SYSTem:ERRor",
    )
    assert text is not None and "sds.coverage.yaml 中没有这条命令" in text and "SYSTem:ERRor" in text


def test_hint_not_in_definition():
    text = hint_line(
        code=-113,
        message="Undefined header",
        reason="not_in_definition",
        origin="sb-smu-1.yaml",
        closest=":SOURce:VOLTage",
    )
    assert text is not None and "定义 sb-smu-1.yaml 中没有这条命令" in text


def test_hint_ignored():
    assert hint_line(reason="ignored") == "已忽略 · 定义设为 unknown_commands: ignore"


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
    assert hint_text(lines[0], "zh") == "这个型号不回答 *TRG · 定义的 builtins"


def test_hint_bad_parameter_carries_the_range():
    text = hint_line(
        code=-222,
        message="Data out of range",
        reason="bad_parameter",
        param="source.voltage.level",
        range="-210 … 210 V",
    )
    assert text == "参数不合法：source.voltage.level 应在 -210 … 210 V 内"


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
    report = limited_report(SMU_INFO, readings, events, "zh")
    assert report is not None and "恒流（CC）" in report.conclusion
    setting = report.evidences[0]
    assert ":SOUR:VOLT 15" in (setting.source or "") and "脚本" in (setting.source or "")
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
    report = error_report(lines[0], "zh")
    assert report.title == "原因 · smu 错误 -222"
    assert report.hint is not None and "参数不合法" in report.hint
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
