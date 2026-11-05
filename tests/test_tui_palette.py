"""The command palette (Ctrl+K) and the settings search (/): tui-design.md 4.

The command list is built from the real bindings and state variables, so these tests check the
generation (fast, no app boot) and then the whole way through a booted app: open, search, run."""

from __future__ import annotations

import copy

import pytest
from textual.command import CommandPalette

from simubench.tui import palette as P
from tui_mock import load, unit_app

SMU = load("unit_smu.json")


def fresh():
    return copy.deepcopy(SMU)


# ---- generation (unit tier) --------------------------------------------------------------------


@pytest.mark.unit
def test_every_key_command_has_a_title_and_a_key():
    tui, _ = unit_app(fresh())
    commands = P.key_commands(tui)
    assert commands, "the app binds keys"
    for c in commands:
        assert c.title and not c.title.startswith("help.actions."), c
        assert c.help, c  # the key shown beside the title


@pytest.mark.unit
def test_the_palette_and_help_keys_are_not_listed_in_themselves():
    tui, _ = unit_app(fresh())
    titles = {c.title for c in P.key_commands(tui)}
    assert "Command palette" not in titles and "This help" not in titles


@pytest.mark.unit
def test_an_open_command_per_instrument_and_a_set_command_per_setting():
    tui, _ = unit_app(fresh())
    tui.session_info = fresh()["session"]
    names = [i["name"] for i in tui.session_info["instruments"]]
    opens = [c.title for c in P.instrument_commands(tui)]
    assert opens == [f"Open {n}" for n in names]
    sets = P.setting_commands(tui)
    assert any(c.title == "Set smu · Voltage level" for c in sets)
    assert all(c.help for c in sets)  # path and current value


@pytest.mark.unit
def test_a_direct_set_needs_a_value_and_checks_that_a_number_is_one():
    tui, _ = unit_app(fresh())
    tui.session_info = fresh()["session"]
    assert P.direct_sets(tui, "smu voltage") == [] or all(
        "=" in c.title for _t, c in P.direct_sets(tui, "smu voltage")
    )
    hits = [c.title for _t, c in P.direct_sets(tui, "smu voltage 5")]
    assert "Set smu · Voltage level = 5" in hits
    # Not a number: no "= abc" line for a numeric setting.
    assert not [
        c for _t, c in P.direct_sets(tui, "smu voltage abc") if c.title.endswith("Voltage level = abc")
    ]
    # Out of range still shows, so the field can say by how much.
    assert [c for _t, c in P.direct_sets(tui, "smu voltage 999") if c.title.endswith("Voltage level = 999")]


# ---- through a booted app (tui_boot tier) -------------------------------------------------------


async def _settle(pilot, tui):
    from test_tui_t2 import settle

    await settle(pilot, tui)


@pytest.mark.tui_boot
async def test_ctrl_k_opens_the_palette_and_a_search_opens_the_instrument():
    tui, _fake = unit_app(fresh())
    async with tui.run_test(size=(100, 32)) as pilot:
        await _settle(pilot, tui)
        await pilot.press("ctrl+k")
        await pilot.pause(0.3)
        assert isinstance(tui.screen, CommandPalette)
        for ch in "open smu":
            await pilot.press(ch)
        await pilot.pause(0.5)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert type(tui.screen).__name__ == "InstrumentScreen"
        assert tui.screen.instrument_name == "smu"


@pytest.mark.tui_boot
async def test_setting_a_variable_by_typing_it_writes_through_the_field():
    tui, fake = unit_app(fresh())
    async with tui.run_test(size=(100, 32)) as pilot:
        await _settle(pilot, tui)
        await pilot.press("ctrl+k")
        await pilot.pause(0.3)
        for ch in "smu voltage level 7.5":
            await pilot.press(ch)
        await pilot.pause(0.6)
        await pilot.press("enter")
        for _ in range(60):
            await pilot.pause(0.05)
            if ("smu", "source.voltage.level", 7.5) in fake.configures:
                break
        assert ("smu", "source.voltage.level", 7.5) in fake.configures


@pytest.mark.tui_boot
async def test_a_value_out_of_range_is_not_written():
    tui, fake = unit_app(fresh())
    async with tui.run_test(size=(100, 32)) as pilot:
        await _settle(pilot, tui)
        await pilot.press("ctrl+k")
        await pilot.pause(0.3)
        for ch in "smu voltage level 999":
            await pilot.press(ch)
        await pilot.pause(0.6)
        await pilot.press("enter")
        await pilot.pause(0.8)
        assert fake.configures == []


@pytest.mark.tui_boot
async def test_slash_searches_the_screens_fields_and_enter_starts_editing():
    tui, fake = unit_app(fresh())
    async with tui.run_test(size=(100, 32)) as pilot:
        await _settle(pilot, tui)
        from test_tui_t2 import open_instrument

        await open_instrument(pilot, tui, "smu")
        await pilot.pause(0.3)
        await pilot.press("slash")
        await pilot.pause(0.3)
        assert isinstance(tui.screen, CommandPalette)
        for ch in "voltage":
            await pilot.press(ch)
        await pilot.pause(0.5)
        await pilot.press("enter")
        await pilot.pause(0.5)
        button = tui.screen.focused
        assert button is not None and button.control.label.startswith("Voltage")
        assert button.edit is not None  # editing where it stands
        assert fake.configures == []


@pytest.mark.tui_boot
async def test_switching_an_output_on_from_the_palette_still_asks_first():
    fixture = fresh()
    infos = [*fixture["session"]["instruments"], *fixture["instruments"].values()]
    for info in infos:
        for sv in info.get("state", []):
            if sv["path"] == "output.enabled":
                sv["value"] = False  # off now, so "on" is a switch-on
    tui, fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await _settle(pilot, tui)
        await pilot.press("ctrl+k")
        await pilot.pause(0.3)
        for ch in "smu output on":
            await pilot.press(ch)
        await pilot.pause(0.6)
        await pilot.press("enter")
        await pilot.pause(0.8)
        assert type(tui.screen).__name__ == "ConfirmScreen"  # the dangerous confirm is not bypassed
        assert fake.configures == []
