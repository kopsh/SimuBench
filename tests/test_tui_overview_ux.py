"""The status-first overview: a light and the key readings on each card, one attention strip for
every fault, limit and error, ``p`` to jump to the next problem and ``w`` to explain the focused
card (tui-screens.md 4)."""

from __future__ import annotations

import pytest

from simubench.tui.cards import Alert, CardData, instrument_card, status_light

IDN = "SIMUBENCH,SB-SMU-1,SBSMU000001,1.0.0"


def smu_info(errors=None, output=False):
    return {
        "name": "smu",
        "model": "SB-SMU-1",
        "idn": IDN,
        "display": {"template": "smu"},
        "state": [
            {"path": "output.enabled", "value": output, "type": "bool"},
            {"path": "source.function", "value": "VOLTage", "type": "enum"},
            {"path": "source.voltage.level", "value": 5.0, "type": "float"},
        ],
        "errors": errors or [],
        "ports": [],
    }


def card_of(info, readings=None, faults=None):
    return instrument_card(info, {}, readings, lambda net: None, None, "en", faults)


@pytest.mark.unit
def test_the_card_no_longer_spends_a_line_on_the_idn_string():
    card = card_of(smu_info())
    assert all(IDN not in "".join(text for text, _ in line) for line in card.lines)


@pytest.mark.unit
def test_a_quiet_idle_card_has_a_muted_open_light():
    card = card_of(smu_info())
    assert card.alerts == []
    assert card.title[0] == ("○ ", "muted")


@pytest.mark.unit
def test_an_output_that_is_on_lights_green():
    assert card_of(smu_info(output=True)).title[0] == ("● ", "ok")


@pytest.mark.unit
def test_the_limit_is_an_amber_triangle_and_names_itself():
    readings = {"values": {"core.limited": True, "core.regulation": "CC", "core.limit": 0.01}}
    card = card_of(smu_info(output=True), readings)
    assert card.title[0] == ("▲ ", "focus")
    assert [a.kind for a in card.alerts] == ["limit"]
    assert card.alerts[0].text.startswith("CC limited by current")


@pytest.mark.unit
def test_an_error_in_the_queue_is_red_and_counts_the_rest():
    errors = [{"code": -113, "message": "Undefined header"}, {"code": -222, "message": "Data out of range"}]
    card = card_of(smu_info(errors=errors))
    assert card.title[0] == ("● ", "bad")
    assert card.alerts == [Alert("error", "-222 Data out of range (+1)")]


@pytest.mark.unit
def test_a_fault_comes_before_the_limit_and_the_error():
    faults = [{"id": "f1", "status": "active", "spec": {"kind": "timeout", "instrument": "smu"}}]
    readings = {"values": {"core.limited": True, "core.regulation": "CV", "core.limit": 5.0}}
    card = card_of(smu_info(errors=[{"code": -300, "message": "x"}]), readings, faults)
    assert [a.kind for a in card.alerts] == ["fault", "limit", "error"]
    assert card.alerts[0].text == "fault f1 · timeout"


@pytest.mark.unit
def test_a_fault_on_another_instrument_is_not_this_cards_problem():
    faults = [{"id": "f1", "status": "active", "spec": {"kind": "timeout", "instrument": "dmm"}}]
    assert card_of(smu_info(), None, faults).alerts == []


@pytest.mark.unit
def test_alert_tones():
    assert Alert("limit", "x").tone == "focus"
    assert Alert("fault", "x").tone == Alert("error", "x").tone == "bad"


@pytest.mark.unit
def test_status_light_prefers_the_worst_news():
    card = CardData(name="x", alerts=[Alert("limit", "a"), Alert("error", "b")])
    assert status_light(card, True) == ("●", "bad")


# ---- the log drawer (tui-screens.md 4.5) ------------------------------------------------------------

from tui_mock import ev, feed, load, unit_app  # noqa: E402


def _box(tui):
    return tui.screen._log_box


@pytest.mark.tui_boot
async def test_the_log_is_a_shut_drawer_at_first_and_L_opens_and_shuts_it():
    from test_tui_t2 import settle

    tui, _fake = unit_app(load("unit_smu.json"))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        assert _box(tui).styles.height.value == 3 + 2  # a few rows, the cards keep the height
        await pilot.press("L")
        await pilot.pause(0.3)
        assert _box(tui).styles.height.value > 3 + 2
        await pilot.press("L")
        await pilot.pause(0.3)
        assert _box(tui).styles.height.value == 3 + 2


@pytest.mark.tui_boot
async def test_a_new_error_marks_the_shut_drawers_title_without_opening_it():
    from test_tui_t2 import settle, wait_for

    tui, _fake = unit_app(load("unit_smu.json"))
    async with tui.run_test(size=(100, 32)) as pilot:
        await settle(pilot, tui)
        assert "new" not in (_box(tui).border_title or "")
        await feed(
            tui,
            ev(1, 1_000_000, "scpi", instrument="smu", dir="write", client="pyvisa", text=":BAD"),
            ev(2, 1_000_000, "error", instrument="smu", code=-113, message="Undefined header"),
        )
        await wait_for(pilot, lambda: "✗ 1 new" in (_box(tui).border_title or ""), message="the title mark")
        assert _box(tui).styles.height.value == 3 + 2  # it did not open by itself
        await pilot.press("L")  # opening it is the user having seen the error
        await pilot.pause(0.3)
        assert "new" not in (_box(tui).border_title or "")


@pytest.mark.tui_boot
async def test_the_drawer_does_not_eat_the_last_card_row_at_80_by_24():
    from test_tui_t2 import settle

    tui, _fake = unit_app(load("unit_smu.json"))
    async with tui.run_test(size=(80, 24)) as pilot:
        await settle(pilot, tui)
        grid = tui.screen._grid
        assert grid.region.height >= 8  # the cards keep a real block above the 5-row drawer
