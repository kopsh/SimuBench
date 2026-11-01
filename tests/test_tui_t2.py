"""Unit tests for the T2 screens (tui-screens.md 5, 6, 9, 10, 12): fast, mocked, no network.

The app under test is the real ``SimuBenchApp`` driven by Textual's Pilot, but its client is the
mock's ``FakeClient`` answering from payloads dumped out of a real session, and its WS stream is
replaced — events are injected with :func:`tui_mock.feed`. Each test runs in milliseconds. The
real-server scenarios (the spec's acceptance: 找到跳闸, remote takeover, the ``/screen``
contract) live in ``test_tui_t2_e2e.py``.
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

from simubench.tui.app import SimuBenchApp
from tui_mock import ev, feed, load, unit_app

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

SMU_BENCH = load("unit_smu.json")
SCOPE_BENCH = load("unit_scope_rc.json")


def fresh(fixture: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(fixture)


async def settle(pilot, tui: SimuBenchApp, deadline: float = 8.0) -> None:
    """The app's data is in (the connection loop's full refresh ran)."""
    end = asyncio.get_running_loop().time() + deadline
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        if tui.session_info and tui.data_version > 0:
            return
    raise AssertionError("the unit app never settled")


async def focus_card(pilot, tui: SimuBenchApp, name: str, timeout: float = 20.0) -> None:
    """Focus one overview card with its digit key (tui-screens.md 4.4).

    A data refresh can rebuild the grid and drop the focus, so the key is pressed again until
    the focus is on the right card.
    """
    from simubench.tui.screens.overview import Card, OverviewScreen

    session = tui.session_info or {}
    names = [i["name"] for i in session.get("instruments", [])]
    names += [p["name"] for p in session.get("parts", [])]
    digit = str(names.index(name) + 1)
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await pilot.press(digit)
        await pilot.pause(0.05)
        focused = tui.screen.focused if isinstance(tui.screen, OverviewScreen) else None
        if isinstance(focused, Card) and focused.card.name == name:
            return
    raise AssertionError(f"the digit never focused {name}'s card")


async def open_instrument(pilot, tui: SimuBenchApp, name: str, timeout: float = 20.0) -> None:
    """Two keystrokes from the overview: the digit key, then Enter (tui-screens.md 2.3)."""
    from simubench.tui.screens.instrument import InstrumentScreen
    from simubench.tui.screens.scope import ScopeScreen

    await focus_card(pilot, tui, name, timeout)
    await pilot.press("enter")
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        screen = tui.screen
        if isinstance(screen, InstrumentScreen | ScopeScreen) and screen.instrument_name == name:
            return
    raise AssertionError(f"{name}'s screen never opened (on {type(tui.screen).__name__})")


def state_of(tui: SimuBenchApp, name: str, path: str) -> object:
    info = tui.client._fixture["instruments"][name]
    return next(sv for sv in info["state"] if sv["path"] == path)["value"]


async def wait_for(pilot, predicate, timeout: float = 20.0, message: str = "condition") -> None:
    """Poll ``predicate`` (sync or async) until it is true."""
    import inspect

    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
    raise AssertionError(f"{message} never became true")


async def wait_flat(pilot, tui, *needles: str, timeout: float = 20.0) -> str:
    """The screen text captured when all ``needles`` first show at once."""
    from test_tui import flat

    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        text = flat(tui)
        if all(needle in text for needle in needles):
            return text
    raise AssertionError(f"{needles} never appeared on {type(tui.screen).__name__}")


# ---- the instrument screen per class (tui-screens.md 5) ---------------------------------------------


async def test_smu_screen_renders_identity_columns_and_reason():
    """The smu template: identity box, 状态/电压/电流 columns, buttons, wiring, load, log."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        text = await wait_flat(pilot, tui, "SIMUBENCH,SB-SMU-1", "HI → net-0", "r1 · DUT")
        # The identity box (5.1 #1).
        assert "定义  sb-smu-1" in text
        assert "TCPIP0::127.0.0.1" in text and "真实  TCPIP0::192.168.1.60" in text
        # The columns with the setting next to the limit (5.2), and the CC reason line (5.4).
        assert "状态" in text and "电压" in text and "电流" in text
        assert "设定" in text and "限值   10.000 mA" in text
        assert "原因" in text and "CC" in text
        # The buttons (5.3) and the per-instrument log (5.1 #5).
        assert "[输出] [源功能] [电压设定]" in text
        assert "通信 · smu" in text
        # Wiring and load boxes beside the channel box (5.1 #2).
        assert "1.0000 kΩ" in text
        # The queue carries the -222 from the dumped session.
        assert "队列 1" in text and "已读走：无" in text


async def test_generator_screen_has_a_box_per_channel():
    """The function generator template: one box per channel with its own controls (5.2)."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "awg")
        from simubench.tui.screens.instrument import InstrumentScreen

        assert isinstance(tui.screen, InstrumentScreen)
        text = await wait_flat(pilot, tui, "CH1")
        assert "CH2" in text
        assert "正弦" in text or "SINE" in text  # the option label of ch1.function
        assert "通信 · awg" in text


async def test_multimeter_screen_shows_panel_readouts():
    """The multimeter template: the panel's readouts, numbers bold, error box, log (5.2)."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "dmm")
        text = await wait_flat(pilot, tui, "直流电压")
        assert "交流电压" in text
        assert "通信 · dmm" in text


# ---- edits write through configure (the dialog rejects out-of-range before any write) ----------------


async def test_edit_number_writes_configure_and_shows_range_error():
    """⏎ opens the edit dialog; a value with a unit converts; out-of-range stays in the dialog."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        from simubench.tui.screens.dialogs import NumberEditScreen
        from simubench.tui.screens.instrument import ControlButton

        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.voltage.level")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, NumberEditScreen), message="edit dialog")
        text = await wait_flat(pilot, tui, "范围")
        assert "当前  15.000 V" in text and "-210.000 V … 210.000 V" in text
        for ch in "7.5V":
            await pilot.press(ch)
        await pilot.press("enter")
        await wait_for(
            pilot, lambda: ("smu", "source.voltage.level", 7.5) in fake.configures, message="write"
        )
        assert state_of(tui, "smu", "source.voltage.level") == 7.5

        # Out of range: rejected inside the dialog, nothing written.
        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.voltage.level")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, NumberEditScreen), message="edit dialog again")
        for ch in "999":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert "超出范围" in _flat(tui)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert state_of(tui, "smu", "source.voltage.level") == 7.5
        assert len(fake.configures) == 1


def _flat(tui: SimuBenchApp) -> str:
    from test_tui import flat

    return flat(tui)


async def test_select_dialog_writes_option():
    """An enum's ⏎ lists the option_labels; picking one writes it (tui-screens.md 5.3)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        from simubench.tui.screens.dialogs import SelectScreen
        from simubench.tui.screens.instrument import ControlButton

        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.function")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, SelectScreen), message="select dialog")
        text = _flat(tui)
        assert "电压源" in text and "电流源" in text
        await pilot.press("down")
        await pilot.press("enter")
        await wait_for(
            pilot, lambda: fake.configures and fake.configures[-1][1] == "source.function", message="write"
        )
        assert fake.configures[-1][2] == "CURRent"
        assert state_of(tui, "smu", "source.function") == "CURRent"


async def test_step_and_switch_writes():
    """``␣`` toggles a plain switch; ``+`` on a non-steppable control says 按 e (5.3)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        from simubench.tui.screens.instrument import ControlButton

        autozero = next(b for b in tui.screen.query(ControlButton) if b.control.path == "sense.autozero")
        autozero.focus()
        await pilot.pause(0.1)
        await pilot.press("space")
        await wait_for(
            pilot, lambda: ("smu", "sense.autozero", False) in fake.configures, message="toggle off"
        )
        assert state_of(tui, "smu", "sense.autozero") is False

        # + on the voltage setting: no panel step, no .scale path → the 按 e hint flashes.
        volt = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.voltage.level")
        volt.focus()
        await pilot.pause(0.1)
        await pilot.press("plus")
        await wait_flat(pilot, tui, "按 e 输入数值")
        assert state_of(tui, "smu", "source.voltage.level") == 15.0
        assert len(fake.configures) == 1


async def test_dangerous_switch_confirms_before_writing():
    """An electrical switch turning on confirms first; cancel keeps it off (10.1)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        from simubench.tui.screens.instrument import ControlButton
        from simubench.tui.screens.states import ConfirmScreen

        out = next(b for b in tui.screen.query(ControlButton) if b.control.path == "output.enabled")
        out.focus()
        await pilot.pause(0.1)
        await pilot.press("space")  # on → off writes directly
        await wait_for(
            pilot, lambda: ("smu", "output.enabled", False) in fake.configures, message="toggle off"
        )

        await pilot.press("space")  # off → on confirms
        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="confirm dialog")

        def confirm_ready() -> bool:
            text = _flat(tui)
            return (
                "打开输出？" in text
                and "output.enabled → 开" in text
                and "连着的脚本会看到输出改变" in text
                and "HI 接 dmm.IN 与 r1.A" in text
            )

        await wait_for(pilot, confirm_ready, message="confirm content")
        await pilot.press("n")  # cancel keeps it off
        await pilot.pause(0.1)
        assert state_of(tui, "smu", "output.enabled") is False
        await pilot.press("space")
        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="confirm again")
        await pilot.press("y")
        await wait_for(pilot, lambda: ("smu", "output.enabled", True) in fake.configures, message="toggle on")


# ---- the SCPI console (tui-screens.md 10.3) -----------------------------------------------------------


async def test_console_sends_reply_and_lands_in_the_log():
    """``:`` opens the console; the reply shows; the log records it with the source 终端."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    fake.scpi_reply["smu"] = {"response": "+9.99900009E+00", "errors": []}
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await pilot.press("colon")
        await pilot.pause(0.1)
        assert "smu ❯" in _flat(tui)
        for ch in ":MEAS:VOLT?":
            await pilot.press(ch)
        await pilot.press("enter")
        await wait_flat(pilot, tui, "→ +9.99900009E+00")
        assert fake.writes == [("smu", ":MEAS:VOLT?")]
        await pilot.press("escape")
        await pilot.pause(0.1)
        # The same command arriving as events shows in the log with the tui source (1.5).
        await feed(
            tui,
            ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="tui", text=":MEAS:VOLT?"),
            ev(2, 20_000_000, "scpi", instrument="smu", dir="read", client="tui", text="+9.99900009E+00"),
        )
        await wait_for(
            pilot, lambda: any(":MEAS:VOLT?" in ln.command for ln in tui.lines), message="log line"
        )
        line = next(ln for ln in tui.lines if ":MEAS:VOLT?" in ln.command)
        assert line.source == "终端" and line.result_text().startswith("→ +9.9")


async def test_console_history_persists_across_reopen(monkeypatch, tmp_path):
    """The console's ↑↓ history is per instrument and survives a reopen (10.3)."""
    import simubench.tui.screens.dialogs as dialogs

    monkeypatch.setattr(dialogs, "_HISTORY_PATH", tmp_path / "tui-history.json")
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await pilot.press("colon")
        await pilot.pause(0.1)
        for ch in ":MEAS:CURR?":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.1)
        await pilot.press("colon")
        await pilot.pause(0.1)
        for ch in ":MEAS:VOLT?":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.1)
        # Reopen: ↑ walks the instrument's own history, latest first.
        await pilot.press("colon")
        await pilot.pause(0.1)
        await pilot.press("up")
        from simubench.tui.screens.dialogs import ScpiConsole

        console = tui.screen.query_one(ScpiConsole)
        assert console._input.value == ":MEAS:VOLT?"
        await pilot.press("up")
        assert console._input.value == ":MEAS:CURR?"


# ---- the scope screen (tui-screens.md 6) --------------------------------------------------------------


async def test_scope_screen_renders_traces_settings_and_actions():
    """Enter on a scope card opens the scope screen: braille trace, settings, r/s via panel."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        from simubench.tui.screens.scope import ScopeScreen

        assert isinstance(tui.screen, ScopeScreen)
        text = await wait_flat(pilot, tui, "CH1 50.0 mV/div")
        assert "1.000 ms/div" in text and "运行" in text and "采集" in text
        assert "← rc.OUT · 10x 探头" in text  # the settings column's wiring line
        assert "⠂" in text or "⠁" in text  # a braille trace rendered from the binned frame
        frame = tui.screen_data["scope"]
        assert frame["traces"][0]["minmax"] and frame["traces"][0]["volts"] == []
        # r / s go through the panel actions (D32), not SCPI.
        await pilot.press("r")
        await pilot.press("s")
        await wait_for(
            pilot, lambda: fake.panels == [("scope", "stop"), ("scope", "single")], message="panel writes"
        )
        assert fake.writes == []


async def test_scope_screen_shows_silence_reason():
    """An open channel without a signal shows 无信号：<原因> in the wave area (6.3)."""
    fixture = fresh(SCOPE_BENCH)
    fixture["screen_scope_140"]["traces"][0]["silence"] = [{"kind": "unconnected"}]
    tui, _fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_flat(pilot, tui, "无信号", "未接线")


async def test_screen_points_requested_at_dot_width():
    """The scope screen requests /screen at twice its character width (6.1)."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_flat(pilot, tui, "CH1 50.0 mV/div")
        # The frame the screen displays was fetched through client.screen(name, points).
        assert tui.screen_data["scope"] is fake._fixture["screen_scope_140"]


# ---- the reason dialog (tui-screens.md 9.1) ------------------------------------------------------------


LIMITED_EVENTS = (
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
)


async def test_reason_dialog_on_limited_smu():
    """``?`` on a limited SMU: conclusion, 设定 with its command, 限值 from the bench file (D37)."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await feed(tui, *LIMITED_EVENTS)
        await open_instrument(pilot, tui, "smu")
        text = await wait_flat(pilot, tui, "原因", "CC", "▲")  # the 5.4 reason line
        assert "电流达到限值" in text
        await pilot.press("question_mark")
        text = await wait_flat(pilot, tui, "恒流（CC）")
        assert "source.voltage.level" in text and "15.000 V" in text
        assert "← 00:00:00.020" in text and "脚本" in text and ":SOUR:VOLT 15" in text
        assert "compliance.current.limit" in text and "测试台文件" in text
        await pilot.press("c")  # copy as plain text
        await pilot.pause(0.1)
        await pilot.press("enter")  # jump to the command in the fullscreen log
        await pilot.pause(0.2)
        from simubench.tui.screens.logscreen import LogScreen

        assert isinstance(tui.screen, LogScreen)
        assert ":SOUR:VOLT 15" in _flat(tui)


async def test_reason_dialog_names_error_and_classification():
    """``?`` explains the newest error: the command, its source and the 9.2 wording."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    # Not limited here, so ``?`` falls through to the newest error instead of the CC reason.
    fake._fixture["readings"]["smu"]["values"]["core.limited"] = False
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await feed(
            tui,
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
        )
        await open_instrument(pilot, tui, "smu")
        await pilot.press("question_mark")
        text = await wait_flat(pilot, tui, "原因 · smu 错误 -222")
        assert ":SOUR:VOLT 999" in text and "脚本" in text
        assert "参数不合法" in text and "source.voltage.level" in text and "-210 … 210 V" in text


# ---- the error history (tui-screens.md 5.1 #4) ---------------------------------------------------------


async def test_error_history_greys_read_away_errors():
    """A read-away error stays, greyed with its command; the queue count drops live."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    info = tui.client._fixture["instruments"]["smu"]
    info["errors"] = []  # the queue has been read empty; the events remember the error
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await feed(
            tui,
            ev(1, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":SOUR:VOLT 999"),
            ev(
                2,
                20_000_000,
                "error",
                instrument="smu",
                client="pyvisa",
                code=-222,
                message="Data out of range",
            ),
            ev(
                3,
                40_000_000,
                "scpi",
                instrument="smu",
                dir="read",
                client="pyvisa",
                text='-222,"Data out of range"',
            ),
        )
        await open_instrument(pilot, tui, "smu")
        text = await wait_flat(pilot, tui, "队列 0")
        assert "已读走" in text and "-222" in text and ":SOUR:VOLT 999" in text


# ---- the command-not-executed hints (tui-screens.md 9.2, six categories of #174) ------------------------


def hint_events(seq: int, kind: str, **fields: Any) -> tuple[dict[str, Any], ...]:
    """A write and the not-executed event that follows it (the engine's order)."""
    write = ev(seq, 20_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":FOO 1")
    payload = {"instrument": "smu", "client": "pyvisa", **fields}
    if kind == "ignored":
        payload.setdefault("header", ":FOO")
        payload.setdefault("query", False)
    return write, ev(seq + 1, 20_000_000, kind, **payload)


HINTS = [
    (
        "error",
        {
            "code": -113,
            "message": "Undefined header;not modeled by SimuBench",
            "reason": "not_modeled",
            "origin": "siglent-sds2104xplus.coverage.yaml",
        },
        "未建模 · 覆盖表 siglent-sds2104xplus.coverage.yaml 中标为未实现",
    ),
    (
        "error",
        {
            "code": -113,
            "message": "Undefined header",
            "reason": "not_in_model",
            "origin": "siglent.coverage.yaml",
            "closest": ":SYSTem:ERRor",
        },
        "覆盖表 siglent.coverage.yaml 中没有这条命令",
    ),
    (
        "error",
        {
            "code": -113,
            "message": "Undefined header",
            "reason": "not_in_definition",
            "origin": "sb-smu-1.yaml",
            "closest": ":SOURce:VOLTage",
        },
        "定义 sb-smu-1.yaml 中没有这条命令",
    ),
    (
        "ignored",
        {"reason": "ignored", "origin": "lfcal-reference-scope.yaml"},
        "已忽略 · 定义设为 unknown_commands: ignore",
    ),
    (
        "error",
        {
            "code": -113,
            "message": "Undefined header",
            "reason": "builtin_excluded",
            "origin": "fluke-5522a.yaml",
        },
        "这个型号不回答",
    ),
    (
        "error",
        {
            "code": -222,
            "message": "Data out of range",
            "reason": "bad_parameter",
            "param": "source.voltage.level",
            "range": "-210 … 210 V",
        },
        "参数不合法：source.voltage.level 应在 -210 … 210 V 内",
    ),
]


@pytest.mark.parametrize(("kind", "fields", "wording"), HINTS, ids=[h[1]["reason"] for h in HINTS])
async def test_hint_wording_per_classification(kind, fields, wording):
    """The log renders the 9.2 wording under the line, with the origin file (zh)."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(120, 36)) as pilot:
        await settle(pilot, tui)
        await feed(tui, *hint_events(1, kind, **fields))
        await open_instrument(pilot, tui, "smu")
        await pilot.press("l")
        text = await wait_flat(pilot, tui, wording)
        if fields.get("closest"):
            assert "最接近的：" in text
        if fields.get("origin"):
            origin = Path(fields["origin"]).name
            if fields["reason"] in ("not_modeled", "not_in_model", "not_in_definition"):
                assert origin in text


async def test_overview_enter_opens_screens_and_parts_say_not_implemented():
    """Enter opens the instrument screen; a part's Enter says the nets screen comes with T3."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        # rc is the 3rd card (a part): Enter does nothing and the bottom bar says why.
        await focus_card(pilot, tui, "rc")
        await pilot.press("enter")
        await pilot.pause(0.1)
        from simubench.tui.screens.overview import OverviewScreen

        assert isinstance(tui.screen, OverviewScreen)
        assert "网络屏幕尚未实现" in _flat(tui)
        # Enter on the scope card opens the scope screen.
        await focus_card(pilot, tui, "scope")
        await pilot.press("enter")
        from simubench.tui.screens.scope import ScopeScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, ScopeScreen), message="scope screen")
        await pilot.press("escape")
        await pilot.pause(0.1)
        # Enter on the awg card opens the plain instrument screen.
        await focus_card(pilot, tui, "awg")
        await pilot.press("enter")
        from simubench.tui.screens.instrument import InstrumentScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, InstrumentScreen), message="instrument screen")
        assert tui.screen.instrument_name == "awg"
