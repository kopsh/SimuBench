"""Interaction tests for the T3 screens (tui-screens.md 8, 11.1–11.3): nets, faults, the clock.

The app is the real one over the mock's FakeClient (``tests/tui_mock.py``) — the tui_boot tier:
each test boots a Textual app, and the real-server acceptance scenarios (the lost-reply fault, the
RC sweep) live in ``test_tui_t3_e2e.py``.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from simubench.tui.app import SimuBenchApp
from tui_mock import load, unit_app

SCOPE_BENCH = load("unit_scope_rc.json")
SMU_BENCH = load("unit_smu.json")


def fresh(fixture: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(fixture)


async def settle(pilot, tui: SimuBenchApp, deadline: float = 20.0) -> None:
    end = asyncio.get_running_loop().time() + deadline
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        if tui.session_info and tui.data_version > 0:
            return
    raise AssertionError("the unit app never settled")


async def wait_for(pilot, predicate, timeout: float = 20.0, message: str = "condition") -> None:
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


def _flat(tui: SimuBenchApp) -> str:
    from test_tui import flat

    return flat(tui)


async def open_nets(pilot, tui: SimuBenchApp) -> None:
    from simubench.tui.screens.nets import NetsScreen

    await pilot.press("n")
    await wait_for(pilot, lambda: isinstance(tui.screen, NetsScreen), message="nets screen")


# ---- the nets screen (tui-screens.md 11.1) --------------------------------------------------------------


async def test_nets_screen_lists_levels_and_probe():
    """`n`: the left list shows every net's endpoints and level; the right quotes the probe."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_nets(pilot, tui)
        from simubench.tui.fmt import fmt

        rms_text = fmt(SCOPE_BENCH["probe_net-0"]["rms"], "V")
        line = SCOPE_BENCH["probe_net-0"]["lines"][0]
        # The probe arrives asynchronously after the list; wait for the level line and the
        # strongest line it quotes — the braille waveform renders in the same pass.
        text = await wait_for_text(pilot, tui, "net-0", "DC ", fmt(float(line["freq_hz"]), "Hz"))
        assert rms_text in text
        assert "net-1" in text and "net-2" in text  # the other rows
        assert "awg.CH1" in text and "●" in text  # endpoints and the signal mark


async def wait_for_text(pilot, tui, *needles: str, timeout: float = 20.0) -> str:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        await pilot.pause(0.05)
        text = _flat(tui)
        if all(needle in text for needle in needles):
            return text
    raise AssertionError(f"{needles} never appeared on {type(tui.screen).__name__}:\n{_flat(tui)}")


async def test_nets_screen_selection_and_driver():
    """↑↓ selects; ⏎ opens the driving instrument (the largest source contribution)."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_nets(pilot, tui)
        await wait_for_text(pilot, tui, "net-0")
        await pilot.press("down")
        await pilot.pause(0.3)
        await pilot.press("enter")
        from simubench.tui.screens.instrument import InstrumentScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, InstrumentScreen), message="driver screen")
        assert tui.screen.instrument_name == "awg"


async def test_nets_screen_silence_and_reason_dialog():
    """A silent net names its silence causes; `?` quotes the probe's own breakdown (D37)."""
    fixture = fresh(SCOPE_BENCH)
    probe = fixture["probe_net-0"]
    probe["silence"] = [{"kind": "source_off", "source": "awg.CH1"}]
    probe["rms"] = probe["peak"] = 0.0
    probe["sources"] = []
    probe["lines"] = []
    tui, _fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_nets(pilot, tui)
        text = await wait_for_text(pilot, tui, "Unconnected ports", "awg.CH1's output is off")
        await pilot.press("w")
        text = await wait_for_text(pilot, tui, "Reason", "output is off")
        assert "net-0" in text


async def test_overview_part_enter_opens_nets_on_its_first_net():
    """Enter on a part (2.3): the nets screen, selected on the part's first net."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from test_tui_t2 import focus_card

        await focus_card(pilot, tui, "rc")
        await pilot.press("enter")
        from simubench.tui.screens.nets import NetsScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, NetsScreen), message="nets screen")
        assert tui.screen.selected == "net-0"  # rc's first port's net


# ---- the fault dialog (tui-screens.md 8) -----------------------------------------------------------------


async def test_fault_dialog_injects_and_lists():
    """`f` opens the dialog; injecting POSTs the Fault and the log records the source 终端."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await pilot.press("f")
        from simubench.tui.screens.faultdialog import FaultDialog

        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        text = _flat(tui)
        assert (
            "Timeout" in text and "Protection trip" in text and "Parameter drift" in text
        )  # the three groups
        assert "Inject" in text
        # Inject the default (timeout, count empty = until cleared).
        from textual.widgets import Button

        dlg = tui.screen
        dlg.query_one("#fd-inject", Button).press()
        await wait_for(pilot, lambda: bool(fake.fault_list), message="injected")
        spec = fake.fault_list[0]["spec"]
        assert spec["kind"] in (
            "timeout",
            "disconnect",
            "garble",
            "latency",
            "busy",
            "error",
            "trip",
            "drift",
            "hidden",
        )
        assert spec.get("instrument") is None  # the session-level dialog has no preselection
        # The dialog closed; the injected fault shows in the app's list for the next dialog.
        assert tui.faults and tui.faults[0]["id"] == fake.fault_list[0]["id"]


async def test_fault_dialog_revoke_with_confirm():
    """Delete revokes the last pending/active fault, after a confirm (8)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await pilot.press("f")
        from simubench.tui.screens.faultdialog import FaultDialog
        from simubench.tui.screens.states import ConfirmScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        from textual.widgets import Button

        tui.screen.query_one("#fd-inject", Button).press()  # inject the default timeout
        await wait_for(pilot, lambda: bool(fake.fault_list), message="injected")
        await pilot.press("f")
        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog again")
        await wait_for(pilot, lambda: "1 fault(s) in effect" in _flat(tui), message="active fault listed")
        await pilot.press("delete")
        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="revoke confirm")
        await pilot.press("y")
        await wait_for(pilot, lambda: not fake.fault_list, message="revoked")


# ---- the clock advance (tui-screens.md 11.2) --------------------------------------------------------------


async def test_advance_dialog_steps_the_clock():
    """`a` opens the input; the typed duration steps the clock and becomes the next default."""
    fixture = fresh(SMU_BENCH)
    fixture["clock"] = {"mode": "stepped", "now_ns": 0}
    tui, fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await pilot.press("a")
        from simubench.tui.screens.clockdialog import AdvanceDialog

        await wait_for(pilot, lambda: isinstance(tui.screen, AdvanceDialog), message="advance dialog")

        dlg = tui.screen
        assert isinstance(dlg, AdvanceDialog)
        dlg.query_one("#fd-advance-input").value = ""  # ctrl+a is line-home in an Input, not select-all
        for ch in "10ms":
            await pilot.press(ch)
        await pilot.press("enter")
        await wait_for(pilot, lambda: bool(fake.advances), message="advanced")
        assert fake.advances == [(0.01, False)]
        assert tui._advance_default == "10 ms"


async def test_advance_with_scripts_confirms_and_forces():
    """Scripts connected: the advance confirms first and carries force (D32)."""
    fixture = fresh(SMU_BENCH)
    fixture["clock"] = {"mode": "stepped", "now_ns": 0}
    fixture["session"]["clients"] = [
        {"client": "socket:127.0.0.1:53122", "instrument": None, "locked": False}
    ]
    tui, fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await pilot.press("a")
        from simubench.tui.screens.clockdialog import AdvanceDialog
        from simubench.tui.screens.states import ConfirmScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, AdvanceDialog), message="advance dialog")
        await pilot.press("enter")
        await wait_for(pilot, lambda: isinstance(tui.screen, ConfirmScreen), message="force confirm")
        await pilot.press("y")
        await wait_for(pilot, lambda: bool(fake.advances), message="advanced")
        assert fake.advances == [(1.0, True)]


async def test_advance_on_wall_clock_says_so():
    """The wall clock walks on its own: the bottom bar says so and nothing is sent (11.2)."""
    fixture = fresh(SMU_BENCH)
    fixture["clock"] = {"mode": "wall", "now_ns": 0}
    tui, fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await pilot.press("a")
        await pilot.pause(0.3)
        assert fake.advances == []
        from test_tui import flat

        assert "Wall clock" in flat(tui)


# ---- the reason dialog on the nets screen uses the probe's own breakdown (11.1, D37) ---------------------


async def test_nets_reason_quotes_sources_and_lines():
    """`?` on a live net: the probe's sources (电动势 × 传递) and lines, no recomputation."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_nets(pilot, tui)
        from simubench.tui.fmt import fmt

        rms_text = fmt(SCOPE_BENCH["probe_net-0"]["rms"], "V")
        await wait_for_text(pilot, tui, "net-0", rms_text)
        await pilot.press("w")
        text = await wait_for_text(pilot, tui, "Why · net-0")
        assert rms_text in text
        assert "awg" in text  # the source contribution names its source
        line = SCOPE_BENCH["probe_net-0"]["lines"][0]
        assert fmt(float(line["freq_hz"]), "Hz") in text  # the spectral line
        assert "net-0" in text
