"""Interaction tests for the T3 screens (tui-screens.md 8, 11.1–11.3): nets, faults, the clock.

The app is the real one over the mock's FakeClient (``tests/tui_mock.py``) — the tui_boot tier:
each test boots a Textual app, and the real-server acceptance scenarios (the lost-reply fault, the
RC sweep) live in ``test_tui_e2e.py``.
"""

from __future__ import annotations

import pytest

from simubench.tui.app import SimuBenchApp
from tui_mock import flat, fresh, load, settle, unit_app, wait_flat, wait_for

pytestmark = (
    pytest.mark.tui_boot
)  # boots a Textual app or a real server: runs on main, not in the fast checks

SCOPE_BENCH = load("unit_scope_rc.json")
SMU_BENCH = load("unit_smu.json")


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
        text = await wait_flat(pilot, tui, "net-0", "DC ", fmt(float(line["freq_hz"]), "Hz"))
        assert rms_text in text
        assert "net-1" in text and "net-2" in text  # the other rows
        assert "awg.CH1" in text and "●" in text  # endpoints and the signal mark


async def test_nets_screen_selection_and_driver():
    """↑↓ selects; ⏎ opens the driving instrument (the largest source contribution)."""
    tui, _fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_nets(pilot, tui)
        await wait_flat(pilot, tui, "net-0")
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
        text = await wait_flat(pilot, tui, "Unconnected ports", "awg.CH1's output is off")
        await pilot.press("w")
        text = await wait_flat(pilot, tui, "Reason", "output is off")
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
    """`f` on the focused card opens the dialog for that instrument (8); injecting POSTs the
    Fault and the log records the source 终端."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from test_tui_t2 import focus_card

        await focus_card(pilot, tui, "smu")
        await pilot.press("f")
        from simubench.tui.screens.faultdialog import FaultDialog

        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        text = flat(tui)
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
        assert spec["instrument"] == "smu"  # the dialog acts on the focused card's instrument
        # The dialog closed; the injected fault shows in the app's list for the next dialog.
        assert tui.faults and tui.faults[0]["id"] == fake.fault_list[0]["id"]


async def test_fault_key_without_focus_hints_the_bottom_bar():
    """``f`` with no focused card opens nothing and flashes the bottom bar's remedy (#791), the
    way ``w`` does when the card has nothing to explain — no silent keystroke."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from simubench.tui.screens.faultdialog import FaultDialog

        tui.screen.set_focus(None)  # no card focused: the digit keys were never pressed
        await pilot.press("f")
        text = await wait_flat(pilot, tui, "Press 1–9 to focus an instrument first")
        assert not isinstance(tui.screen, FaultDialog)
        assert "Inject a fault" not in text  # the dialog never composed
        assert not fake.fault_list


async def test_a_data_refresh_does_not_take_back_a_focus_the_user_cleared():
    """#827: the overview's rebuild put the focus back on the last card even when nothing was
    focused, so ``f`` after clearing the focus sometimes opened the dialog (a late refresh won the
    race). A rebuild restores the focus only while a card has it."""
    tui, _ = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from simubench.tui.screens.overview import Card

        screen = tui.screen
        cards = list(screen.query(Card))
        assert cards, "the overview has its cards"
        screen.set_focus(cards[0])  # what the digit keys do
        await pilot.pause()
        screen.set_focus(None)
        tui.data_version += 1  # new data: the screen rebuilds its cards
        screen.refresh_content()
        await pilot.pause()
        await pilot.pause()
        assert screen.query(Card) and screen.focused is None


async def test_a_data_refresh_keeps_the_focus_on_the_same_card():
    """#852: a refresh (one, or several in a row) leaves the focus on the very card the user had
    focused — the same widget, still in the DOM — and a cleared focus stays cleared across
    refreshes that queue up before it (#849)."""
    tui, _ = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from simubench.tui.screens.overview import Card

        screen = tui.screen
        card = next(iter(screen.query(Card)))
        name = card.card.name
        screen.set_focus(card)
        await pilot.pause()
        for refreshes in (1, 3):
            for _ in range(refreshes):
                tui.data_version += 1
                screen.refresh_content()
            await pilot.pause()
            await pilot.pause()
            assert screen.focused is card and card in screen.query(Card)
            assert screen.focused.card.name == name
        for _ in range(2):  # clear while refreshes are queued: nothing revives the focus
            tui.data_version += 1
            screen.refresh_content()
        screen.set_focus(None)
        tui.data_version += 1
        screen.refresh_content()
        await pilot.pause()
        await pilot.pause()
        assert screen.query(Card) and screen.focused is None


async def test_a_rebuild_puts_the_focus_back_on_the_same_named_card():
    """#852: when the cards must be rebuilt (a different layout), the focus is on the new card of
    the same name, and a cleared focus still stays cleared (#849)."""
    tui, _ = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from simubench.tui.screens.overview import Card

        screen = tui.screen

        async def rebuild(old: list) -> None:
            screen._layout = None  # a changed layout: the in-place update does not apply
            tui.data_version += 1
            screen.refresh_content()
            await wait_for(
                pilot,
                lambda: bool(screen.query(Card)) and not any(c in screen.query(Card) for c in old),
                message="the cards are rebuilt",
            )
            await pilot.pause()
            await pilot.pause()

        old = next(iter(screen.query(Card)))
        screen.set_focus(old)
        await pilot.pause()
        await rebuild([old])
        focused = screen.focused
        assert isinstance(focused, Card) and focused is not old
        assert focused in screen.query(Card) and focused.card.name == old.card.name
        screen.set_focus(None)
        await rebuild(list(screen.query(Card)))
        assert screen.focused is None


async def test_fault_dialog_injects_an_error_with_count():
    """error + count (issue 697): the code and the times reach the spec the TUI POSTs."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from test_tui_t2 import focus_card

        await focus_card(pilot, tui, "smu")
        await pilot.press("f")
        from textual.widgets import Button, Input, RadioButton

        from simubench.tui.screens.faultdialog import FaultDialog

        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        error_radio = next(r for r in tui.screen.query(RadioButton) if r.name == "error")
        error_radio.value = True
        # the error kind's rows replace the timeout kind's (both have a count): wait for them
        await wait_for(pilot, lambda: bool(tui.screen.query("#fd-f-code")), message="error fields")
        tui.screen.query_one("#fd-f-code", Input).value = "-313"
        tui.screen.query_one("#fd-f-count", Input).value = "3"
        tui.screen.query_one("#fd-inject", Button).press()
        await wait_for(pilot, lambda: bool(fake.fault_list), message="injected")
        spec = fake.fault_list[0]["spec"]
        assert spec["kind"] == "error" and spec["code"] == -313 and spec["count"] == 3
        assert spec["instrument"] == "smu"
        assert fake.fault_list[0]["source"] == "tui"


async def test_fault_dialog_revoke_with_confirm():
    """Delete revokes the last pending/active fault, after a confirm (8)."""
    tui, fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from test_tui_t2 import focus_card

        await focus_card(pilot, tui, "smu")
        await pilot.press("f")
        from simubench.tui.screens.faultdialog import FaultDialog
        from simubench.tui.screens.states import ConfirmScreen

        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        from textual.widgets import Button

        tui.screen.query_one("#fd-inject", Button).press()  # inject the default timeout
        await wait_for(pilot, lambda: bool(fake.fault_list), message="injected")
        await focus_card(pilot, tui, "smu")  # the dismissal may leave the overview unfocused
        await pilot.press("f")
        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog again")
        await wait_for(pilot, lambda: "1 fault(s) in effect" in flat(tui), message="active fault listed")
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
        await wait_flat(pilot, tui, "net-0", rms_text)
        await pilot.press("w")
        text = await wait_flat(pilot, tui, "Why · net-0")
        assert rms_text in text
        assert "awg" in text  # the source contribution names its source
        line = SCOPE_BENCH["probe_net-0"]["lines"][0]
        assert fmt(float(line["freq_hz"]), "Hz") in text  # the spectral line
        assert "net-0" in text


# ---- the fault dialog's subject (#789) -----------------------------------------------------------------


async def test_nets_screen_fault_dialog_lists_only_component_kinds_and_injects_on_the_net():
    """``f`` on the nets screen (8): the dialog lists only the component kinds, the component is
    the part on the selected net (preselected), the inject carries it, and the dialog closes."""
    tui, fake = unit_app(fresh(SCOPE_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        await open_nets(pilot, tui)
        from textual.widgets import Button, Input, RadioSet

        from simubench.tui.screens.faultdialog import FaultDialog, net_components
        from simubench.tui.screens.nets import NetsScreen

        net = tui.screen.selected
        assert net_components(tui.session_info, net) == ["rc"]  # rc has a port on net-0
        assert isinstance(tui.screen, NetsScreen)
        await pilot.press("f")
        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        dlg = tui.screen
        assert [r.id for r in dlg.query(RadioSet)] == ["fd-group-component"]  # no comm/instrument kinds
        await pilot.pause(0.3)
        assert dlg.query_one("#fd-f-component", Input).value == "rc"  # preselected
        dlg.query_one("#fd-f-param", Input).value = "r"
        dlg.query_one("#fd-f-drift_per_s", Input).value = "1"
        dlg.query_one("#fd-inject", Button).press()
        await wait_for(pilot, lambda: bool(fake.fault_list), message="injected")
        spec = fake.fault_list[0]["spec"]
        assert spec["kind"] == "drift" and spec["component"] == "rc" and "instrument" not in spec
        await wait_for(pilot, lambda: not isinstance(tui.screen, FaultDialog), message="dialog closed")


async def test_fault_dialog_active_list_is_the_subjects_own():
    """``#fd-active`` lists the dialog subject's faults only (8): an instrument's own and its
    components', a net's components — not the whole session's."""
    tui, _fake = unit_app(fresh(SMU_BENCH))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        from simubench.tui.screens.faultdialog import FaultDialog, faults_of_subject
        from test_tui_t2 import focus_card

        def fault(fid: str, **spec) -> dict:
            return {"id": fid, "status": "active", "description": fid, "spec": spec}

        tui.faults = [
            fault("f-1", kind="timeout", instrument="smu"),
            fault("f-2", kind="timeout", instrument="other"),
            fault("f-3", kind="hidden", component="smu/meter"),
            fault("f-4", kind="drift", component="rc"),
            {**fault("f-5", kind="timeout", instrument="smu"), "status": "ended"},
        ]
        assert [f["id"] for f in faults_of_subject(tui.faults, "smu", None)] == ["f-1", "f-3"]
        assert [f["id"] for f in faults_of_subject(tui.faults, None, ["rc"])] == ["f-4"]
        await focus_card(pilot, tui, "smu")
        await pilot.press("f")
        await wait_for(pilot, lambda: isinstance(tui.screen, FaultDialog), message="fault dialog")
        text = flat(tui.screen)
        assert "f-1" in text and "f-3" in text
        assert "f-2" not in text and "f-4" not in text and "f-5" not in text
