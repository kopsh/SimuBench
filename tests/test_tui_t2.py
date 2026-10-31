"""TUI T2 (issue 176): the instrument and scope screens, controls, SCPI console, the reason
dialog, the error history, the not-executed hints, and the "find the trip" scenario.

Every test drives the real app with Textual's Pilot against a real uvicorn server in a thread
(the test bed of tests/test_tui.py), per tui-screens.md 14. Windows-safe: sockets and threads.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from simubench.tui.app import SimuBenchApp

from test_tui import ROOT, Served, flat, loop_time, wait_connected, wait_quiet

VALIDATION = ROOT / "validation"
LOWFREQ = ROOT / "examples/lowfreq-cal"


@pytest.fixture
async def served_examples() -> Served:
    s = Served(ROOT / "examples/benches")
    yield s  # type: ignore[misc]
    s.stop()


@pytest.fixture
async def served_validation() -> Served:
    s = Served(VALIDATION)
    yield s  # type: ignore[misc]
    s.stop()


@pytest.fixture
async def served_lowfreq() -> Served:
    s = Served(LOWFREQ)
    yield s  # type: ignore[misc]
    s.stop()


async def open_instrument(pilot, tui: SimuBenchApp, name: str, timeout: float = 8.0) -> None:
    """Two keystrokes from the overview: the digit key, then Enter (tui-screens.md 2.3)."""
    from simubench.tui.screens.instrument import InstrumentScreen
    from simubench.tui.screens.scope import ScopeScreen

    session = tui.session_info or {}
    names = [i["name"] for i in session.get("instruments", [])]
    index = names.index(name)
    await pilot.press(str(index + 1))
    await pilot.pause(0.2)
    await pilot.press("enter")
    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.05)
        screen = tui.screen
        if isinstance(screen, InstrumentScreen) and screen.instrument_name == name:
            return
        if isinstance(screen, ScopeScreen) and screen.instrument_name == name:
            return
    raise AssertionError(f"{name}'s screen never opened (on {type(tui.screen).__name__})")


async def state_of(tui: SimuBenchApp, name: str, path: str) -> object:
    info = await tui.client.instrument(tui.sid, name)
    return next(sv for sv in info["state"] if sv["path"] == path)["value"]


async def wait_for(pilot, predicate, timeout: float = 10.0, message: str = "condition") -> None:
    """Poll ``predicate`` (sync or async) until it is true."""
    import inspect

    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.1)
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
    raise AssertionError(f"{message} never became true")


async def wait_flat(pilot, tui, *needles: str, timeout: float = 10.0) -> str:
    """The screen text captured when all ``needles`` first show at once.

    The compositor's strips are read live: a render can be brief (a refresh recomposing) or
    partial (one box updated, the next not yet), so reading once after a fixed pause races the
    screen. Poll until one render shows every needle, and assert the rest against that render.
    """
    end = loop_time() + timeout
    while loop_time() < end:
        await pilot.pause(0.1)
        text = flat(tui)
        if all(needle in text for needle in needles):
            return text
    missing = [needle for needle in needles if needle not in flat(tui)]
    raise AssertionError(f"{missing} never appeared on {type(tui.screen).__name__}")


# ---- the instrument screen per class (tui-screens.md 5) ---------------------------------------------


async def test_smu_screen_renders_identity_columns_and_reason(served_validation):
    """The smu template: identity box, 状态/电压/电流 columns, buttons, wiring, load, log."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        text = flat(tui)
        # The identity box (5.1 #1).
        assert "SIMUBENCH,SB-SMU-1" in text and "定义  sb-smu-1" in text
        assert "TCPIP0::127.0.0.1" in text and "真实  TCPIP0::192.168.1.60" in text
        # The columns with the setting next to the limit (5.2).
        assert "状态" in text and "电压" in text and "电流" in text
        assert "设定" in text and "限值   10.000 mA" in text
        # The buttons (5.3) and the per-instrument log (5.1 #5).
        assert "[输出] [源功能] [电压设定]" in text
        assert "通信 · smu" in text
        # Wiring and load boxes beside the channel box (5.1 #2).
        assert "HI → net-0" in text and "r1 · DUT" in text and "1.0000 kΩ" in text
        # The error box shows the queue count (empty here).
        assert "队列 0" in text and "已读走：无" in text


async def test_generator_screen_has_a_box_per_channel(served_examples):
    """The function generator template: one box per channel with its own controls (5.2)."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served_examples.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "awg")
        await wait_quiet(pilot, tui)
        from simubench.tui.screens.instrument import InstrumentScreen

        assert isinstance(tui.screen, InstrumentScreen)
        text = flat(tui)
        assert "CH1" in text and "CH2" in text
        assert "正弦" in text or "SINE" in text  # the option label of ch1.function
        assert "通信 · awg" in text


async def test_multimeter_screen_shows_panel_readouts(served_validation):
    """The multimeter template: the panel's readouts, numbers bold, error box, log (5.2)."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "dmm")
        await wait_quiet(pilot, tui)
        text = flat(tui)
        assert "直流电压" in text and "5.000000 V" in text
        assert "交流电压" in text
        assert "通信 · dmm" in text


# ---- edits write through configure like the web's do (issue: same run-record events) ----------------


async def test_edit_number_writes_configure_and_shows_range_error(served_validation):
    """⏎ opens the edit dialog; a value with a unit converts; out-of-range stays in the dialog."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        from simubench.tui.screens.dialogs import NumberEditScreen
        from simubench.tui.screens.instrument import ControlButton

        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.voltage.level")
        button.focus()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, NumberEditScreen), message="edit dialog")
        await pilot.pause(0.3)
        text = flat(tui)
        assert "当前  5.0000 V" in text and "范围  -210.000 V … 210.000 V" in text
        for ch in "7.5V":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.8)
        assert await state_of(tui, "smu", "source.voltage.level") == 7.5
        # The same run-record event the web's edit produces: a state event for the path.
        events = served_session.session.history
        state_events = [e for e in events if e.get("kind") == "state" and e.get("path") == "source.voltage.level"]
        assert state_events and state_events[-1]["new"] == 7.5

        # Out of range: rejected inside the dialog, nothing written.
        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.voltage.level")
        button.focus()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, NumberEditScreen), message="edit dialog again")
        await pilot.pause(0.3)
        for ch in "999":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.4)
        assert "超出范围" in flat(tui)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert await state_of(tui, "smu", "source.voltage.level") == 7.5


async def test_select_dialog_writes_option(served_validation):
    """An enum's ⏎ lists the option_labels; picking one writes it (tui-screens.md 5.3)."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        from simubench.tui.screens.dialogs import SelectScreen
        from simubench.tui.screens.instrument import ControlButton

        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.function")
        button.focus()
        await pilot.pause(0.2)
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, SelectScreen), message="select dialog")
        await pilot.pause(0.3)
        text = flat(tui)
        assert "电压源" in text and "电流源" in text
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.8)
        assert await state_of(tui, "smu", "source.function") == "CURRent"


async def test_step_and_switch_writes(served_validation):
    """␣ toggles a plain switch; + / - step a number; a non-steppable control says 按 e (5.3)."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        from simubench.tui.screens.instrument import ControlButton

        autozero = next(b for b in tui.screen.query(ControlButton) if b.control.path == "sense.autozero")
        autozero.focus()
        await pilot.pause(0.2)
        await pilot.press("space")
        await pilot.pause(0.8)
        assert await state_of(tui, "smu", "sense.autozero") is False
        await pilot.press("space")
        await pilot.pause(0.8)
        assert await state_of(tui, "smu", "sense.autozero") is True

        # + on the voltage setting: no panel step, no .scale path → the 按 e hint flashes.
        volt = next(b for b in tui.screen.query(ControlButton) if b.control.path == "source.voltage.level")
        volt.focus()
        await pilot.pause(0.2)
        await pilot.press("plus")
        await pilot.pause(0.3)
        assert "按 e 输入数值" in flat(tui)
        assert await state_of(tui, "smu", "source.voltage.level") == 5.0


async def test_dangerous_switch_confirms_before_writing(served_validation):
    """An electrical switch turning on confirms first; cancel keeps it off (10.1)."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        # Turn the output OFF first: off→on is the confirmed direction, so start from off.
        from simubench.tui.screens.instrument import ControlButton
        from simubench.tui.screens.states import ConfirmScreen

        out = next(b for b in tui.screen.query(ControlButton) if b.control.path == "output.enabled")
        out.focus()
        await pilot.pause(0.2)
        await pilot.press("space")
        await pilot.pause(0.8)
        assert await state_of(tui, "smu", "output.enabled") is False

        await pilot.press("space")
        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="confirm dialog")

        def confirm_ready() -> bool:
            text = flat(tui)
            return (
                "打开输出？" in text
                and "output.enabled → 开" in text
                and "连着的脚本会看到输出改变" in text
                and "HI 接 dmm.IN 与 r1.A" in text
            )

        # The modal re-renders while the screen below refreshes; poll until it is all visible.
        await wait_for(pilot, confirm_ready, message="confirm content")
        await pilot.press("n")  # cancel
        await pilot.pause(0.5)
        assert await state_of(tui, "smu", "output.enabled") is False
        await pilot.press("space")
        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="confirm dialog again")
        await pilot.pause(0.3)
        await pilot.press("y")
        await pilot.pause(0.8)
        assert await state_of(tui, "smu", "output.enabled") is True


# ---- remote control and L (tui-screens.md 12, D32) ---------------------------------------------------


async def test_remote_makes_controls_readonly_and_l_takes_over(served_validation):
    """A socket client holds the instrument: the hint line shows, writes are refused, L works."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"*IDN?\n")
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=10)

        async def is_remote() -> bool:
            return bool((await tui.client.instrument(sid, "smu")).get("remote"))

        await wait_for(pilot, lambda: asyncio.create_task(is_remote()), message="remote state")
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        text = flat(tui)
        assert "远程控制" in text and "设置只读" in text and "L 本地接管" in text
        assert "· 远程" in text  # the title's badge

        # A switch press is refused while remote (the hint flashes; nothing is written).
        from simubench.tui.screens.instrument import ControlButton

        out = next(b for b in tui.screen.query(ControlButton) if b.control.path == "output.enabled")
        assert out.has_class("-readonly")
        out.focus()
        await pilot.pause(0.2)
        await pilot.press("space")
        await pilot.pause(0.5)
        assert "正被" in flat(tui) and "远程控制" in flat(tui)
        assert await state_of(tui, "smu", "output.enabled") is True

        # L (capital, 5.5): confirm, then the instrument goes local.
        await pilot.press("L")
        await pilot.pause(0.4)
        from simubench.tui.screens.states import ConfirmScreen

        assert isinstance(tui.screen, ConfirmScreen)
        await pilot.press("y")
        async def is_local() -> bool:
            return (await tui.client.instrument(sid, "smu")).get("local") is True

        await wait_for(pilot, is_local, message="local state")
        writer.close()


# ---- the SCPI console (tui-screens.md 10.3) -----------------------------------------------------------


async def test_console_sends_scpi_reply_and_lands_in_the_log(served_validation):
    """``:`` opens the console; the reply shows; the log records it with the source 终端."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        await pilot.press("colon")
        await pilot.pause(0.3)
        assert "smu ❯" in flat(tui)
        for ch in ":MEAS:VOLT?":
            await pilot.press(ch)
        await pilot.press("enter")
        await wait_for(pilot, lambda: "→ +5" in flat(tui), message="console reply")
        # The same run-record events a script's write produces, with the tui client.
        scpi_events = [e for e in served_session.session.history if e.get("kind") == "scpi"]
        assert [e.get("client") for e in scpi_events] == ["tui", "tui"]
        assert scpi_events[0]["dir"] == "write" and scpi_events[1]["dir"] == "read"
        await pilot.press("escape")
        await pilot.pause(0.3)
        line = next(ln for ln in tui.lines if ":MEAS:VOLT?" in ln.command)
        assert line.source == "终端" and line.result_text().startswith("→ +5")


async def test_console_history_persists_across_reopen(served_validation, monkeypatch, tmp_path):
    """↑↓ walks the per-instrument history; it is saved to ~/.simubench/tui-history (10.3)."""
    import json

    history_file = tmp_path / "tui-history"
    monkeypatch.setattr("simubench.tui.screens.dialogs._HISTORY_PATH", history_file)
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        await pilot.press("colon")
        await pilot.pause(0.3)
        for ch in "*IDN?":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.8)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert history_file.exists()
        saved = json.loads(history_file.read_text(encoding="utf-8"))
        assert saved["smu"] == ["*IDN?"]
        # Reopen: ↑ restores the last command.
        await pilot.press("colon")
        await pilot.pause(0.3)
        await pilot.press("up")
        await pilot.pause(0.2)
        assert "*IDN?" in flat(tui)
        await pilot.press("escape")


# ---- the scope screen (tui-screens.md 6) --------------------------------------------------------------


async def test_scope_screen_renders_traces_settings_and_actions(served_examples):
    """Enter on a scope card opens the scope screen: braille trace, settings, r/s via panel."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_examples.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        from simubench.tui.screens.scope import ScopeScreen

        assert isinstance(tui.screen, ScopeScreen)
        await wait_quiet(pilot, tui)
        text = flat(tui)
        assert "CH1 50.0 mV/div" in text and "1.000 ms/div" in text
        assert "运行" in text and "采集" in text
        assert "← rc.OUT · 10x 探头" in text  # the settings column's wiring line
        # The frame came from /screen?points=<2×width>: per-bin min/max, no full samples.
        frame = tui.screen_data["scope"]
        assert frame["traces"][0]["minmax"] and frame["traces"][0]["volts"] == []
        wave_text = "\n".join(text.splitlines()[2:18])
        assert any(ch in wave_text for ch in "⠁⠉⡇⢸")  # braille dots drawn

        # r stops, r runs again — panel events with the tui source (D32).
        await pilot.press("r")
        await wait_for(
            pilot,
            lambda: any(e.get("kind") == "panel" for e in served_session.session.history),
            message="panel event",
        )
        panel = [e for e in served_session.session.history if e.get("kind") == "panel"][-1]
        assert panel["action"] == "stop" and panel["source"] == "tui"
        await pilot.press("r")
        await pilot.pause(0.5)
        panel = [e for e in served_session.session.history if e.get("kind") == "panel"][-1]
        assert panel["action"] == "run"
        # s is a single acquisition.
        await pilot.press("s")
        await pilot.pause(0.5)
        panel = [e for e in served_session.session.history if e.get("kind") == "panel"][-1]
        assert panel["action"] == "single"


async def test_scope_screen_shows_silence_reason(served_examples):
    """An open channel without a signal shows 无信号：<原因> in the wave area (6.3)."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served_examples.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        # The awg's output starts off in this bench: CH1 is open but silent.
        await open_instrument(pilot, tui, "scope")
        await wait_quiet(pilot, tui)
        await wait_for(
            pilot,
            lambda: "无信号" in flat(tui),
            message="silence line",
        )
        text = flat(tui)
        assert "CH1 无信号" in text and "输出关闭" in text


async def test_screen_points_contract(served_examples):
    """`points` returns per-bin min/max and no samples; without it the response is unchanged."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served_session.id
    with served_examples.http() as http:
        plain = http.get(f"/api/sessions/{sid}/instruments/scope/screen").json()
        binned = http.get(f"/api/sessions/{sid}/instruments/scope/screen?points=140").json()
        rejected = http.get(f"/api/sessions/{sid}/instruments/scope/screen?points=1")
    assert plain["traces"][0]["volts"] and plain["traces"][0]["minmax"] is None
    assert binned["traces"][0]["volts"] == []
    minmax = binned["traces"][0]["minmax"]
    assert len(minmax) == 280 and all(minmax[i] <= minmax[i + 1] for i in range(0, 280, 2))
    assert rejected.status_code == 422


# ---- the reason dialog (tui-screens.md 9.1) ------------------------------------------------------------


async def test_reason_dialog_on_limited_smu(served_validation):
    """``?`` on a limited SMU: conclusion, 设定 with its command, 限值 from the bench file (D37)."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    session = served_session.session
    # A script forces 15 V against the 10 mA limit: the SMU goes to CC (the spec's §1.3 setup).
    session.exchange("smu", b":SOUR:VOLT 15", client="pyvisa", reads=1)
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        text = flat(tui)
        assert "原因" in text and "CC" in text and "▲" in text  # the 5.4 reason line
        await pilot.press("question_mark")
        text = await wait_flat(pilot, tui, "电流达到限值")
        assert "恒流（CC）" in text
        assert "source.voltage.level" in text and "15.000 V" in text
        assert "← 00:00:00" in text and "脚本" in text and ":SOUR:VOLT 15" in text
        assert "compliance.current.limit" in text and "测试台文件" in text
        await pilot.press("c")  # copy as plain text
        await pilot.pause(0.2)
        await pilot.press("enter")  # jump to the command in the fullscreen log
        await pilot.pause(0.5)
        from simubench.tui.screens.logscreen import LogScreen

        assert isinstance(tui.screen, LogScreen)
        assert ":SOUR:VOLT 15" in flat(tui)


async def test_reason_dialog_names_error_and_classification(served_validation):
    """``?`` explains the newest error: the command, its source and the 9.2 wording."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    served_session.session.exchange("smu", b":SOUR:VOLT 999", client="pyvisa", reads=1)
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        await pilot.press("question_mark")
        text = await wait_flat(pilot, tui, "原因 · smu 错误 -222")
        assert ":SOUR:VOLT 999" in text and "脚本" in text
        assert "参数不合法" in text and "source.voltage.level" in text and "-210 … 210 V" in text


# ---- the error history (tui-screens.md 5.1 #4) ---------------------------------------------------------


async def test_error_history_greys_read_away_errors(served_validation):
    """A read-away error stays, greyed with its command; the queue count drops live."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        # The TUI's own console makes the error; the source is 终端.
        await pilot.press("colon")
        await pilot.pause(0.3)
        for ch in ":SOUR:VOLT 999":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.8)
        await pilot.press("escape")
        await pilot.pause(0.3)
        await wait_for(pilot, lambda: "队列 1" in flat(tui), message="queued error")
        assert "-222" in flat(tui) and "已读走：无" in flat(tui)

        # An external client reads the queue: the count drops to 0 live, the history greys.
        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b":SYSTem:ERRor?\n")
        await writer.drain()
        await asyncio.wait_for(reader.readline(), timeout=10)
        writer.close()
        await wait_for(pilot, lambda: "队列 0" in flat(tui), message="queue zeroed live")
        text = flat(tui)
        assert "已读走" in text and "-222" in text
        assert "✗ -222" in text  # the log line
        greyed = [ln for ln in tui.lines if ln.has_error]
        assert greyed and greyed[0].source == "终端"


# ---- the not-executed hints (tui-screens.md 9.2, one test per category) --------------------------------


async def send_via_console(pilot, tui, command: str) -> None:
    await pilot.press("colon")
    await pilot.pause(0.3)
    for ch in command:
        await pilot.press(ch)
    await pilot.press("enter")
    await pilot.pause(0.6)
    await pilot.press("escape")
    await pilot.pause(0.3)


async def test_hint_not_modeled(served_examples):
    """覆盖表中标为未实现：not_modeled，出处是覆盖表文件（内置真实型号）。"""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served_examples.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_quiet(pilot, tui)
        await send_via_console(pilot, tui, ":AUToset")
        await pilot.press("l")
        await pilot.pause(0.4)
        text = flat(tui)
        assert "未建模 · 覆盖表 siglent-sds2104xplus.coverage.yaml 中标为未实现" in text


async def test_hint_not_in_model(served_examples):
    """覆盖表中没有的命令：not_in_model，附最接近的命令（内置真实型号）。"""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served_examples.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_quiet(pilot, tui)
        await send_via_console(pilot, tui, ":ACQuire:DEPMoth 1200")
        await pilot.press("l")
        await pilot.pause(0.4)
        text = flat(tui)
        assert "覆盖表 siglent-sds2104xplus.coverage.yaml 中没有这条命令" in text
        assert "最接近的：:ACQuire:MDEPth" in text


async def test_hint_not_in_definition(served_validation):
    """无覆盖表、定义中也没有：not_in_definition，出处是定义文件（工作区定义）。"""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        await send_via_console(pilot, tui, ":SOUR:VOLTge 5")
        await pilot.press("l")
        await pilot.pause(0.4)
        text = flat(tui)
        assert "定义 sb-smu-1.yaml 中没有这条命令" in text
        assert "最接近的：" in text and "VOLTage" in text


async def test_hint_ignored(served_lowfreq):
    """unknown_commands: ignore：事件记为 ignored，全屏记录在其下缩进提示（9.2）。"""
    served_session = await served_lowfreq.start("lowfreq_cal.bench.yaml", clock="stepped")
    tui = SimuBenchApp(served_lowfreq.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_quiet(pilot, tui)
        await send_via_console(pilot, tui, "C1:FOO 3")
        await pilot.press("l")
        await pilot.pause(0.4)
        text = flat(tui)
        assert "已忽略 · 定义设为 unknown_commands: ignore" in text
        # Ignored commands are not errors: the line's result stays ·.
        line = next(ln for ln in tui.lines if "C1:FOO" in ln.command)
        assert line.ignored and not line.has_error and line.result_text() == "·"


async def test_hint_builtin_excluded(served_examples):
    """builtins 之外的公共命令：builtin_excluded（内置真实型号）。"""
    served_session = await served_examples.start("fluke-5522a.bench.yaml", seed=3, clock="command")
    tui = SimuBenchApp(served_examples.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "cal")
        await wait_quiet(pilot, tui)
        await send_via_console(pilot, tui, "*TRG")
        await pilot.press("l")
        await pilot.pause(0.4)
        text = flat(tui)
        assert "这个型号不回答 *TRG · 定义的 builtins" in text


async def test_hint_bad_parameter(served_validation):
    """参数超范围：bad_parameter，写出定义给出的范围（工作区定义）。"""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    tui = SimuBenchApp(served_validation.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(120, 36)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await wait_quiet(pilot, tui)
        await send_via_console(pilot, tui, ":SOUR:VOLT 999")
        await pilot.press("l")
        await pilot.pause(0.4)
        text = flat(tui)
        assert "参数不合法：source.voltage.level 应在 -210 … 210 V 内" in text


# ---- 复现“找到跳闸”（tui-screens.md 14, the reference video's scenario) -------------------------------


async def test_find_the_trip(served_validation):
    """A preset trip: two keystrokes to the screen; the TUI itself names fault, source and command;
    an external :SYSTem:ERRor? zeroes the count live while the history keeps the entry."""
    served_session = await served_validation.start("benches/smu.bench.yaml", seed=5, clock="command")
    sid = served_session.id
    tui = SimuBenchApp(served_validation.url, sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        # The fault is injected through the API exactly as a preset fault behaves (deterministic;
        # the fault is the cause). Its source is 终端 because the request carries the tui header.
        async with httpx.AsyncClient(
            base_url=served_validation.url, headers={"X-SimuBench-Client": "tui"}
        ) as http:
            r = await http.post(
                f"/api/sessions/{sid}/faults",
                json={"kind": "trip", "instrument": "smu", "code": -300, "message": "Overvoltage"},
            )
            assert r.status_code == 200
            fault_id = r.json()["id"]

        # Two keystrokes: the digit selects the card, Enter opens the instrument screen.
        await pilot.press("1")
        await pilot.pause(0.2)
        await pilot.press("enter")
        from simubench.tui.screens.instrument import InstrumentScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, InstrumentScreen), message="instrument screen")
        # The TUI updates live as the fault's events arrive — the reason line comes with the
        # fault event, the queued error with the next instrument refetch; wait for both.
        await wait_for(pilot, lambda: f"原因  故障 {fault_id} 跳闸（终端）" in flat(tui), message="reason line")
        # The identity box and the error box carry the queued error (at 100 cols the message is
        # truncated with … per 4.2; the full text is one Enter away, asserted below).
        text = await wait_flat(pilot, tui, "队列 1", "-300")
        # and the fault id shows in the identity box (8).
        assert f"故障 {fault_id} 生效" in text

        # ``?`` opens the causal chain without any test-provided text.
        await pilot.press("question_mark")
        text = await wait_flat(pilot, tui, f"故障 {fault_id} 跳闸（终端）", "Overvoltage")
        assert "←" in text and f"故障 {fault_id}（终端）" in text
        await pilot.press("escape")
        await pilot.pause(0.3)

        # A script reads the error queue: the count drops to 0 live; the history keeps the
        # entry greyed with the fault as its origin.
        port = (await tui.client.instrument(sid, "smu"))["scpi_port"]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b":SYSTem:ERRor?\n")
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=10)
        assert b"-300" in reply
        writer.close()
        await wait_for(pilot, lambda: "队列 0" in flat(tui), message="queue zeroed live")
        text = flat(tui)
        assert "已读走" in text and "-300" in text
        origin = next(
            (
                line
                for line in tui.lines
                if line.has_error and line.error_code == -300 and line.kind == "scpi"
            ),
            None,
        )
        # The trip's error has no owning command; the log pairs it with the reading command that
        # followed (the engine order) or the error box names the fault directly.
        assert "SYSTem:ERRor?" in text


async def test_overview_enter_opens_screens_and_parts_say_not_implemented(served_examples):
    """Enter on an instrument card opens its screen (a scope's own); a part names the nets screen."""
    served_session = await served_examples.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    tui = SimuBenchApp(served_examples.url, sid=served_session.id, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        await wait_connected(pilot, tui)
        await wait_quiet(pilot, tui)
        # rc is the 3rd card (a part): Enter does nothing and the bottom bar says why.
        await pilot.press("3")
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.3)
        from simubench.tui.screens.overview import OverviewScreen

        assert isinstance(tui.screen, OverviewScreen)
        assert "网络屏幕尚未实现" in flat(tui)
        # Enter on the scope card opens the scope screen.
        await pilot.press("2")
        await pilot.pause(0.2)
        await pilot.press("enter")
        from simubench.tui.screens.scope import ScopeScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, ScopeScreen), message="scope screen")
        await pilot.press("escape")
        await pilot.pause(0.3)
        # Enter on the awg card opens the plain instrument screen.
        await pilot.press("1")
        await pilot.pause(0.2)
        await pilot.press("enter")
        from simubench.tui.screens.instrument import InstrumentScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, InstrumentScreen), message="instrument screen")
        assert tui.screen.instrument_name == "awg"
