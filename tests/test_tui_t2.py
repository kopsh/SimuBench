"""Unit tests for the T2 screens (tui-screens.md 5, 6, 9, 10, 12): fast, mocked, no network.

The app under test is the real ``SimuBenchApp`` driven by Textual's Pilot, but its client is the
mock's ``FakeClient`` answering from payloads dumped out of a real session, and its WS stream is
replaced — events are injected with :func:`tui_mock.feed`. Each test runs in milliseconds. The
real-server scenarios (the spec's acceptance: 找到跳闸, remote takeover, the ``/screen``
contract) live in ``test_tui_e2e.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from simubench.tui.app import SimuBenchApp
from tui_mock import ev, feed, fresh, load, settle, unit_app, wait_flat, wait_for

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

SMU_BENCH = load("unit_smu.json")
SCOPE_BENCH = load("unit_scope_rc.json")


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
    """Two keystrokes from the overview: the digit key, then Enter (tui-screens.md 2.3).

    A data refresh between the keystrokes can rebuild the grid and drop the focus (the rebuild
    restores it only after the next refresh), so the pair is retried until the screen is on top
    -- the same race focus_card already retried for the digit key alone."""
    from simubench.tui.screens.instrument import InstrumentScreen
    from simubench.tui.screens.scope import ScopeScreen

    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await focus_card(pilot, tui, name, timeout=max(0.5, end - asyncio.get_running_loop().time()))
        await pilot.press("enter")
        await pilot.pause(0.05)
        screen = tui.screen
        if isinstance(screen, InstrumentScreen | ScopeScreen) and screen.instrument_name == name:
            return
    raise AssertionError(f"{name}'s screen never opened (on {type(tui.screen).__name__})")


def state_of(tui: SimuBenchApp, name: str, path: str) -> object:
    info = tui.client._fixture["instruments"][name]
    return next(sv for sv in info["state"] if sv["path"] == path)["value"]


# ---- the instrument screen per class (tui-screens.md 5) ---------------------------------------------


async def test_smu_screen_renders_identity_columns_and_reason():
    """The smu template: identity box, 状态/电压/电流 columns, buttons, wiring, load, log."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        text = await wait_flat(pilot, tui, "SIMUBENCH,SB-SMU-1", "HI → net-0", "r1 · DUT")
        # The identity box (5.1 #1).
        assert "Definition sb-smu-1" in text
        assert "TCPIP0::127.0.0.1" in text and "TCPIP0::192.168.1.60" in text and "Real " in text
        # The columns with the setting next to the limit (5.2), and the CC reason line (5.4).
        assert "Status" in text and "Voltage" in text and "Current" in text
        assert "Set" in text and "Limit   10.000 mA ▲" in text
        assert "reason" in text and "CC" in text
        # The buttons (5.3) and the per-instrument log (5.1 #5).
        # (At 100 × 32 the English rows push the button row below the channel area's fold; it
        # scrolls into view with Tab, so the buttons are asserted as widgets.)
        from simubench.tui.screens.instrument import ControlButton

        labels = [b.control.label for b in tui.screen.query(ControlButton) if b.box_id == "ch:main"]
        assert labels[:3] == ["Output", "Source function", "Voltage level"]
        assert "Log · smu" in text
        # Wiring and load boxes beside the channel box (5.1 #2).
        assert "1.0000 kΩ" in text
        # The queue carries the -222 from the dumped session.
        assert "queue 1" in text and "read away: none" in text


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
        assert "SINE" in text  # ch1.function as the instrument spells it
        assert "Log · awg" in text


async def test_multimeter_screen_shows_panel_readouts():
    """The multimeter template: the panel's readouts, numbers bold, error box, log (5.2)."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "dmm")
        text = await wait_flat(pilot, tui, "DC voltage")
        assert "AC voltage" in text
        assert "Log · dmm" in text


# ---- fields are edited where they stand (tui-screens.md 5.3) -----------------------------------------


def _field(tui: SimuBenchApp, path: str):
    from simubench.tui.screens.instrument import ControlButton

    return next(b for b in tui.screen.query(ControlButton) if b.control.path == path)


async def test_a_setting_is_a_field_that_shows_its_value():
    """A setting reads ``label value`` — the value is where the label is, not on another block."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        await pilot.pause(0.3)
        assert str(_field(tui, "source.voltage.level")._text) == "Voltage level 15.000 V"
        assert str(_field(tui, "source.function")._text) == "Source function VOLTage"


async def test_edit_number_in_place_converts_units_and_writes():
    """⏎ turns a number field into an editor: type, ⏎ writes; ``7.5V`` converts."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        button = _field(tui, "source.voltage.level")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert button.edit is not None  # editing where it stands: no dialog
        assert type(tui.screen).__name__ == "InstrumentScreen"
        text = await wait_flat(pilot, tui, "-210.000 V … 210.000 V")  # the range, under the field
        assert "accepts" in text  # the formats the field takes (it wraps in a narrow box)
        for ch in "7.5V":
            await pilot.press(ch)
        assert str(button._text).startswith("Voltage level 7.5V")
        await pilot.press("enter")
        await wait_for(
            pilot, lambda: ("smu", "source.voltage.level", 7.5) in fake.configures, message="write"
        )
        assert state_of(tui, "smu", "source.voltage.level") == 7.5
        assert button.edit is None


async def test_an_out_of_range_number_stays_in_the_field_and_writes_nothing():
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        button = _field(tui, "source.voltage.level")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        for ch in "999":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert "out of range" in _flat(tui)
        assert button.edit is not None  # still editing: correct the number
        await pilot.press("backspace", "backspace", "backspace")
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert button.edit is None and type(tui.screen).__name__ == "InstrumentScreen"
        assert fake.configures == []


async def test_escape_cancels_an_edit_and_the_screens_keys_stay_out_while_typing():
    """While a field is edited ``e``, ``w``, ``L`` … are typed or ignored, never the screen's keys."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        button = _field(tui, "source.voltage.level")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.press("5", "e", "w")  # 'e' is an exponent here, 'w' must not open the reason dialog
        assert button.edit.buffer == "5ew"
        assert type(tui.screen).__name__ == "InstrumentScreen"
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert button.edit is None and fake.configures == []


async def test_select_field_cycles_with_arrows_and_writes_on_enter():
    """An enum: ⏎ opens it in place, ←/→ cycle the options, ⏎ writes (tui-screens.md 5.3)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        button = _field(tui, "source.function")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert "◂ VOLTage ▸" in str(button._text)
        await pilot.press("right")
        assert "◂ CURRent ▸" in str(button._text)
        await pilot.press("enter")
        await wait_for(
            pilot, lambda: fake.configures and fake.configures[-1][1] == "source.function", message="write"
        )
        assert fake.configures[-1][2] == "CURRent"
        assert state_of(tui, "smu", "source.function") == "CURRent"


async def test_cancelling_a_select_writes_nothing():
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        button = _field(tui, "source.function")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("enter", "right", "escape")
        await pilot.pause(0.1)
        assert button.edit is None and fake.configures == []
        assert str(button._text) == "Source function VOLTage"


def _flat(tui: SimuBenchApp) -> str:
    from test_tui import flat

    return flat(tui)


async def test_brackets_step_through_the_bench_and_esc_still_goes_back_to_the_overview():
    """``]`` / ``[`` swap the screen for the next / previous instrument; Esc returns to the overview."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "awg")
        names = [i["name"] for i in tui.session_info["instruments"]]
        assert names[:2] == ["awg", "scope"]
        await pilot.press("right_square_bracket")
        await wait_for(
            pilot,
            lambda: type(tui.screen).__name__ == "ScopeScreen" and tui.screen.instrument_name == "scope",
            message="the scope screen",
        )
        await pilot.press("left_square_bracket")
        await wait_for(
            pilot,
            lambda: type(tui.screen).__name__ == "InstrumentScreen" and tui.screen.instrument_name == "awg",
            message="back to the awg",
        )
        await pilot.press("escape")
        await wait_for(pilot, lambda: type(tui.screen).__name__ == "OverviewScreen", message="the overview")


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
        await wait_flat(pilot, tui, "press e to enter a value")
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
                "Turn the output on?" in text
                and "output.enabled → On" in text
                and "Connected scripts will see the output change" in text
                and "HI to dmm.IN and r1.A" in text
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
        assert line.source == "Terminal" and line.result_text().startswith("→ +9.9")


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
        # issue 680: the settings are fields now — `label value` on the buttons, the wiring
        # (with the probe) stays in the text block beside them; the value keeps its cell and a
        # too-long label is ellipsised (`…ition type`). One render carries the visible part.
        text = await wait_flat(
            pilot, tui, "CH1 50.0 mV/div", "← rc.OUT · 10x probe", "ition type NORMal", "Timebase 1.0000 ms"
        )
        assert "1.000 ms/div" in text and "Running" in text  # the wave box's title
        assert "⠂" in text or "⠁" in text  # a braille trace rendered from the binned frame
        from simubench.tui.screens.scope import ControlButton

        by_path = {b.control.path: b for b in tui.screen.query(ControlButton) if b.control.path}
        assert "Probe ratio" in by_path["ch1.probe"].field_text(tui.screen.info, "en").plain
        assert "coupling DC" in by_path["trigger.coupling"].field_text(tui.screen.info, "en").plain
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
        await wait_flat(pilot, tui, "no signal", "not wired")


async def test_screen_points_requested_at_dot_width():
    """The scope screen requests /screen at twice its character width (6.1)."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_flat(pilot, tui, "CH1 50.0 mV/div")
        # The screen fetched its frame at its own dot width: the wave box's inner width (100 − 31)
        # × 2 — 26 for the settings column, 1 margin, 4 for the box's border and padding.
        await wait_for(pilot, lambda: ("scope", 138) in fake.screen_requests, message="points request")


async def test_wave_rows_fit_the_wave_box():
    """A trace row wider than the box wraps and doubles every row (the grid and the trace with it)."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_flat(pilot, tui, "CH1 50.0 mV/div")
        await pilot.pause(0.5)
        cols, _rows = tui.screen._wave_size()
        assert cols == tui.screen._wave_static.size.width


async def test_a_refetched_scope_frame_keeps_its_dot_width():
    """A stopped scope's frame is fetched once at the screen's dot width, and a later fetch (an event,
    a reconnect) asks for the same binning — a raw frame has no min/max and the trace draws nothing."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        await wait_for(pilot, lambda: ("scope", 138) in fake.screen_requests, message="points request")
        await tui._fetch_instruments({"scope"})
        assert fake.screen_requests[-1] == ("scope", 138)


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
        text = await wait_flat(pilot, tui, "reason", "CC", "▲")  # the 5.4 reason line
        assert "current reached its limit" in text
        await pilot.press("w")
        text = await wait_flat(pilot, tui, "constant current (CC)")
        assert "source.voltage.level" in text and "15.000 V" in text
        assert "← 00:00:00.020" in text and "Script" in text and ":SOUR:VOLT 15" in text
        assert "compliance.current.limit" in text and "bench file" in text
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
        await pilot.press("w")
        text = await wait_flat(pilot, tui, "Reason · smu error -222")
        assert ":SOUR:VOLT 999" in text and "Script" in text
        assert "Bad parameter" in text and "source.voltage.level" in text and "-210 … 210 V" in text


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
        text = await wait_flat(pilot, tui, "queue 0")
        assert "read away" in text and "-222" in text and ":SOUR:VOLT 999" in text


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
            "message": "Undefined header;not implemented in this definition",
            "reason": "not_modeled",
            "origin": "siglent-sds2104xplus.coverage.yaml",
        },
        "Not modeled · listed as not implemented in siglent-sds2104xplus.coverage.yaml",
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
        "Not in the coverage table siglent.coverage.yaml",
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
        "Not in the definition sb-smu-1.yaml",
    ),
    (
        "ignored",
        {"reason": "ignored", "origin": "lfcal-reference-scope.yaml"},
        "Ignored · the definition sets unknown_commands: ignore",
    ),
    (
        "error",
        {
            "code": -113,
            "message": "Undefined header",
            "reason": "builtin_excluded",
            "origin": "fluke-5522a.yaml",
        },
        "This model does not answer",
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
        "Bad parameter: source.voltage.level must be within -210 … 210 V",
    ),
]


@pytest.mark.parametrize(("kind", "fields", "wording"), HINTS, ids=[h[1]["reason"] for h in HINTS])
async def test_hint_wording_per_classification(kind, fields, wording):
    """The log renders the 9.2 wording under the line, with the origin file."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(120, 36)) as pilot:
        await settle(pilot, tui)
        await feed(tui, *hint_events(1, kind, **fields))
        await open_instrument(pilot, tui, "smu")
        await pilot.press("l")
        text = await wait_flat(pilot, tui, wording)
        if fields.get("closest"):
            assert "closest:" in text
        if fields.get("origin"):
            origin = Path(fields["origin"]).name
            if fields["reason"] in ("not_modeled", "not_in_model", "not_in_definition"):
                assert origin in text


async def test_overview_enter_opens_screens_and_parts_open_nets():
    """Enter opens an instrument's screen (the scope's own); a part's Enter opens the nets screen."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        # rc is the 3rd card (a part): Enter opens the nets screen on its first net (2.3).
        await focus_card(pilot, tui, "rc")
        await pilot.press("enter")
        from simubench.tui.screens.nets import NetsScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, NetsScreen), message="nets screen")
        assert tui.screen.selected == "net-0"
        await pilot.press("escape")
        await pilot.pause(0.1)
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


async def test_question_mark_opens_the_key_help_of_the_screen():
    """``?`` lists the keys of the screen in front, read from its bindings; Esc closes it."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "awg")
        await pilot.press("question_mark")
        text = await wait_flat(pilot, tui, "Keys · awg", "Why: the chain behind this state", "SCPI console")
        assert "Esc or ? closes" in text
        await pilot.press("escape")
        await wait_for(pilot, lambda: type(tui.screen).__name__ == "InstrumentScreen", message="help closed")


async def test_the_attention_strip_lists_what_is_wrong_and_p_jumps_to_it():
    """One strip above the cards names every alert; ``p`` focuses the next card that has one.
    The dumped scope carries a -113 in its queue."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        text = await wait_flat(pilot, tui, "ATTENTION", "scope -113 Undefined header")
        assert "p problem" in text
        # A rebuild of the cards (new data) can land under the key; pressing again is what a user does.
        for _ in range(30):
            await pilot.press("p")
            await pilot.pause(0.1)
            if getattr(tui.screen.focused, "card", None) and tui.screen.focused.card.name == "scope":
                break
        assert tui.screen.focused.card.name == "scope"


async def test_the_strip_is_quiet_when_all_is_well_and_w_says_so():
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        for inst in tui.session_info["instruments"]:
            inst["errors"] = []
        tui.bump()
        await wait_flat(pilot, tui, "All clear")
        await pilot.press("1")
        await pilot.press("w")
        await wait_flat(pilot, tui, "Nothing to explain on this card")


async def test_the_virtual_time_stays_on_a_narrow_top_bar_and_the_bench_name_gives_way():
    """At 100 columns the bar has no room for a long bench name and the clock: the name is cut."""
    fixture = fresh(SCOPE_BENCH)
    fixture["session"]["bench"] = {
        **fixture["session"].get("bench", {}),
        "name": "A very long bench name indeed",
    }
    tui, _fake = unit_app(fixture)
    tui._bench_name = "A very long bench name indeed " * 2
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        text = await wait_flat(pilot, tui, "SimuBench")
        first = text.split("\n")[0]
        assert "t = 00:00:00" in first and "…" in first, first


async def test_scope_fields_edit_in_place_like_the_instrument_screen():
    """The scope screen edits through the shared layer (#635, issue 680): a typed number with a
    unit converts, out of range is refused without writing, a select cycles, +/- step 1-2-5."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        from simubench.tui.screens.scope import ControlButton

        def button(path):
            return next(b for b in tui.screen.query(ControlButton) if b.control.path == path)

        # a number with a unit: 20 ms converts to the state's seconds
        tb = button("timebase.scale")
        tb.focus()
        await pilot.pause(0.1)
        await pilot.press("e")
        await pilot.pause(0.1)
        for ch in "20ms":
            await pilot.press(ch)
        await pilot.press("enter")
        await wait_for(
            pilot,
            lambda: ("scope", "timebase.scale", 0.02) in fake.configures,
            message="the typed 20 ms landed",
        )
        # out of range (the spec tops out at 1000 s): refused, nothing written
        tb.focus()
        await pilot.pause(0.1)
        await pilot.press("e")
        await pilot.pause(0.1)
        for ch in "2000s":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert ("scope", "timebase.scale", 2000.0) not in fake.configures
        assert tb.edit is not None  # the field stays in the edit for the user to correct (5.3)
        await pilot.press("escape")  # cancel: the value stays as it was
        await pilot.pause(0.2)
        assert tb.edit is None
        # a select cycles in place: trigger.coupling DC -> the next option, committed
        coupling = button("trigger.coupling")
        coupling.focus()
        await pilot.pause(0.1)
        await pilot.press("e")  # into the select editor
        await pilot.pause(0.2)
        await pilot.press("right")  # cycle DC -> the next option
        await pilot.pause(0.1)
        await pilot.press("enter")  # commit
        await wait_for(
            pilot,
            lambda: len([1 for _, p, _ in fake.configures if p == "trigger.coupling"]) == 1,
            message="the cycled option wrote",
        )
        written = [v for _, p, v in fake.configures if p == "trigger.coupling"]
        assert written == ["AC"]  # DC cycled to the option after it, not just anything written
        # + / - step 1-2-5 on a .scale path: ch1.scale 50 mV -> 0.1 -> back to 50 mV
        scale = button("ch1.scale")
        scale.focus()
        await pilot.pause(0.1)
        await pilot.press("plus")
        await wait_for(
            pilot,
            lambda: any(p == "ch1.scale" and v == 0.1 for _, p, v in fake.configures),
            message="the 1-2-5 step up wrote 0.1",
        )
        await pilot.press("minus")
        await wait_for(
            pilot,
            lambda: any(p == "ch1.scale" and v == 0.05 for _, p, v in fake.configures),
            message="the 1-2-5 step down wrote 0.05",
        )


async def test_the_scope_screen_shows_the_focus_without_colour(monkeypatch):
    """`NO_COLOR` on the scope page (issue 680's acceptance, #755): the focused field stays
    distinguishable — the mono theme's focus rule is bold+underline, not a colour."""
    monkeypatch.setenv("NO_COLOR", "1")
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        from simubench.tui.screens.scope import ControlButton

        focused = next(b for b in tui.screen.query(ControlButton) if b.control.path == "timebase.scale")
        focused.focus()
        await pilot.pause(0.2)
        assert focused.has_focus and tui.theme == "simubench-mono"
        assert focused.styles.text_style.bold and focused.styles.text_style.underline
        others = [b for b in tui.screen.query(ControlButton) if b is not focused]
        assert not any(b.styles.text_style.bold or b.styles.text_style.underline for b in others)


async def test_the_settings_column_keeps_every_value_readable_at_80x24():
    """80×24 (issue 680, #755): the settings column ellipsises the label from the left and never
    the value — every field's value shows in full at the narrow size too."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(80, 24)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        from simubench.tui.screens.scope import ControlButton

        def value_of(text):
            """The bold span is the value (field_text assembles label + bold value)."""
            return "".join(
                text.plain[s:e]
                for s, e, style in text.spans
                if style == "bold" or getattr(style, "bold", False)
            )

        width = tui.screen._field_width()
        seen = 0
        for b in tui.screen.query(ControlButton):
            if not b.is_field:
                continue
            full = b.field_text(tui.screen.info, tui.lang)
            shown = b.field_text(tui.screen.info, tui.lang, width)
            assert value_of(full) and value_of(full) == value_of(shown)
            assert shown.plain == b._text.plain  # what the column actually renders
            seen += 1
        assert seen > 10  # the scope page's fields are all under the assertion


async def test_scope_screen_toggles_run_through_the_base_orchestration():
    """The scope screen inherits the instrument screen's toggle path (#635, issue 669): a plain
    switch writes at once, an electrical switch turning on confirms first — same as the SMU's."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        from simubench.tui.screens.scope import ControlButton

        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "ch1.display")
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("space")  # on → off, a plain switch: no confirmation
        await wait_for(
            pilot, lambda: ("scope", "ch1.display", False) in fake.configures, message="plain toggle"
        )
        # The same screen, now electrical: turning it on asks first (10.1). The screen builds its
        # buttons once, so the flag travels on the control the existing button carries.
        button = next(b for b in tui.screen.query(ControlButton) if b.control.path == "ch1.display")
        button.control.electrical = True
        button.focus()
        await pilot.pause(0.1)
        await pilot.press("space")
        from simubench.tui.screens.states import ConfirmScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="confirm dialog")
        await pilot.press("escape")  # cancel: nothing written
        await wait_for(pilot, lambda: not isinstance(tui.screen, ConfirmScreen), message="dialog closed")
        assert ("scope", "ch1.display", True) not in fake.configures


async def test_the_scope_screen_takes_the_field_search_and_jump():
    """`/` opens the settings search and `go_to_field` focuses a field on this screen (#678,
    issue 680): the scope screen inherits the instrument screen's palette integration."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "scope")
        assert tui.screen.go_to_field("timebase.scale", edit=False)
        await pilot.pause(0.2)
        from simubench.tui.screens.scope import ControlButton

        assert tui.screen.focused.control.path == "timebase.scale"
        assert any(b.control.path == "timebase.scale" for b in tui.screen.query(ControlButton))
        # a path this screen does not show lands in the state tree, as on the instrument screen
        assert tui.screen.go_to_field("no.such.path", edit=False) is False


async def test_a_failed_write_flashes_the_error_under_the_control():
    """A write the server refuses flashes ``✗ …`` under the focused control (5.3)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_instrument(pilot, tui, "smu")
        from simubench.tui.screens.instrument import ControlButton

        async def refused(sid, name, path, value):
            return {"ok": False, "error": {"code": -222, "message": "out of range"}}

        fake.configure = refused
        autozero = next(b for b in tui.screen.query(ControlButton) if b.control.path == "sense.autozero")
        flashes: list[str] = []
        autozero.focus()
        await pilot.pause(0.1)
        tui.screen._flash_target = lambda button: (
            tui.screen._flash_static if hasattr(tui.screen, "_flash_static") else None
        )
        # The flash goes to the screen's flash target, per screen; spy on the update itself.
        tui.screen._flash = lambda button, text: flashes.append(text)  # type: ignore[method-assign]
        tui.screen._toggle_button(autozero)
        await wait_for(pilot, lambda: bool(flashes), message="the flash")
        assert "-222" in flashes[0] and "out of range" in flashes[0]


def test_the_embedded_log_rows_budget():
    """`_log_rows` keeps the channel area visible at 80×24 and caps the log at 6 rows (#454)."""
    from types import SimpleNamespace

    from simubench.tui.screens.editing import ControlEditingScreen

    for height, want in ((24, 3), (29, 3), (32, 6), (40, 6)):
        sham = SimpleNamespace(app=SimpleNamespace(size=SimpleNamespace(height=height)))
        assert ControlEditingScreen._log_rows(sham) == want, height
