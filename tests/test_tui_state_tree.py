"""The state tree (``t``, tui-screens.md 5.6): the pure rows, then the screen — editing where a
variable stands, folds that survive a data refresh, and the hidden values behind ``h``."""

from __future__ import annotations

import copy

import pytest

from simubench.tui import tree as T
from tui_mock import ev, feed, load, unit_app

SMU = load("unit_smu.json")


def var(path, type_="float", readonly=False, value=1.0):
    return {
        "path": path,
        "type": type_,
        "readonly": readonly,
        "value": value,
        "label_en": path,
        "label": path,
    }


INFO = {
    "state": [
        var("source.level"),
        var("source.function", "enum", value="VOLT"),
        var("sense.nplc"),
        var("sweep.list", "string", readonly=True),
        var("sweep.start"),
        var("sweep.stop"),
    ]
}


# ---- the rows (unit tier) ----------------------------------------------------------------------


@pytest.mark.unit
def test_variables_are_grouped_by_the_first_path_segment_in_definition_order():
    groups = T.groups_of(INFO)
    assert list(groups) == ["source", "sense", "sweep"]
    assert [sv["path"] for sv in groups["sweep"]] == ["sweep.list", "sweep.start", "sweep.stop"]


@pytest.mark.unit
def test_only_whole_groups_start_open_and_the_first_always_does():
    groups = T.groups_of(INFO)  # sizes with headers: 3, 2, 4
    assert T.default_open(groups, 100) == {"source", "sense", "sweep"}
    assert T.default_open(groups, 5) == {"source", "sense"}  # sweep (4 rows) does not fit: folded whole
    assert T.default_open(groups, 1) == {"source"}  # too small for anything, the first still opens


@pytest.mark.unit
def test_a_folded_group_shows_its_header_only():
    rows = T.tree_rows(T.groups_of(INFO), {"source"}, "en")
    assert [(r.kind, r.group) for r in rows if r.kind == "group"] == [
        ("group", "source"),
        ("group", "sense"),
        ("group", "sweep"),
    ]
    assert [r.path for r in rows if r.kind == "var"] == ["source.level", "source.function"]


@pytest.mark.unit
def test_writable_follows_the_settable_kinds_and_the_readonly_flag():
    assert T.writable(var("a")) and T.writable(var("a", "enum")) and T.writable(var("a", "bool"))
    assert not T.writable(var("a", readonly=True)) and not T.writable(var("a", "string"))


@pytest.mark.unit
def test_hidden_rows_say_so_when_values_are_not_revealed_and_carry_the_text_tag():
    info = {"deviations": [{"param": "dac.gain", "limit": 0.001, "unit": None, "value": None}]}
    rows = T.hidden_rows("smu", info, [], revealed=False, lang="en")
    assert rows[0].kind == "note" and "reveal_true_values" in rows[0].text
    assert rows[1].text.startswith("◆ hidden") and "—" in rows[1].text  # no colour needed to tell it apart


@pytest.mark.unit
def test_revealed_deviations_show_their_values_and_hidden_faults_of_this_instrument_only():
    info = {"deviations": [{"param": "dac.gain", "limit": 0.001, "unit": None, "value": 1.0004}]}
    faults = [
        {
            "id": "f1",
            "status": "active",
            "spec": {"kind": "hidden", "component": "smu/dac", "param": "gain", "value": 2.0},
        },
        {
            "id": "f2",
            "status": "active",
            "spec": {"kind": "hidden", "component": "dmm/adc", "param": "x", "value": 1},
        },
        {
            "id": "f3",
            "status": "ended",
            "spec": {"kind": "hidden", "component": "smu/dac", "param": "o", "value": 1},
        },
    ]
    rows = T.hidden_rows("smu", info, faults, revealed=True, lang="en")
    texts = [r.text for r in rows]
    assert any("dac.gain" in x and "1.0004" in x for x in texts)
    assert any("smu/dac.gain = 2" in x and "f1" in x for x in texts)
    assert not any("dmm" in x or "f3" in x for x in texts)


# ---- the screen (tui_boot tier) ------------------------------------------------------------------


async def _open_tree(pilot, tui):
    from test_tui_t2 import open_instrument, settle

    await settle(pilot, tui)
    await open_instrument(pilot, tui, "smu")
    await pilot.pause(0.3)
    await pilot.press("t")
    await pilot.pause(0.6)
    assert type(tui.screen).__name__ == "StateTreeScreen"


def _button(tui, path):
    from simubench.tui.screens.editing import ControlButton

    return next(b for b in tui.screen.query(ControlButton) if b.control.path == path)


@pytest.mark.tui_boot
async def test_t_opens_the_tree_and_esc_returns_to_the_instrument():
    tui, _ = unit_app(copy.deepcopy(SMU))
    async with tui.run_test(size=(100, 32)) as pilot:
        await _open_tree(pilot, tui)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert type(tui.screen).__name__ == "InstrumentScreen"


@pytest.mark.tui_boot
async def test_a_variable_is_edited_in_the_tree_through_the_fields_own_path():
    tui, fake = unit_app(copy.deepcopy(SMU))
    async with tui.run_test(size=(100, 32)) as pilot:
        await _open_tree(pilot, tui)
        _button(tui, "source.voltage.level").focus()
        await pilot.press("enter")
        for ch in "7.5":
            await pilot.press(ch)
        await pilot.press("enter")
        for _ in range(60):
            await pilot.pause(0.05)
            if ("smu", "source.voltage.level", 7.5) in fake.configures:
                break
        assert ("smu", "source.voltage.level", 7.5) in fake.configures


@pytest.mark.tui_boot
async def test_an_out_of_range_number_is_not_written_from_the_tree():
    tui, fake = unit_app(copy.deepcopy(SMU))
    async with tui.run_test(size=(100, 32)) as pilot:
        await _open_tree(pilot, tui)
        _button(tui, "source.voltage.level").focus()
        await pilot.press("enter")
        for ch in "999":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert fake.configures == []


@pytest.mark.tui_boot
async def test_a_fold_survives_a_data_refresh_and_a_leave_and_return():
    from simubench.tui.screens.statetree import GroupRow

    tui, _ = unit_app(copy.deepcopy(SMU))
    async with tui.run_test(size=(100, 32)) as pilot:
        await _open_tree(pilot, tui)
        header = next(b for b in tui.screen.query(GroupRow) if b.group == "source")
        header.focus()
        await pilot.press("enter")  # fold it
        await pilot.pause(0.4)
        assert not [
            b for b in tui.screen.query("ControlButton") if (b.control.path or "").startswith("source.")
        ]
        await feed(
            tui,
            ev(
                1,
                1_000_000,
                "state",
                instrument="smu",
                path="output.enabled",
                source="scpi",
                old=True,
                new=False,
            ),
        )
        await pilot.pause(0.6)
        assert not [
            b for b in tui.screen.query("ControlButton") if (b.control.path or "").startswith("source.")
        ]
        await pilot.press("escape")
        await pilot.pause(0.3)
        await pilot.press("t")
        await pilot.pause(0.6)
        assert not [
            b for b in tui.screen.query("ControlButton") if (b.control.path or "").startswith("source.")
        ]


@pytest.mark.tui_boot
async def test_h_adds_and_removes_the_hidden_section():
    tui, _ = unit_app(copy.deepcopy(SMU))
    async with tui.run_test(size=(100, 32)) as pilot:
        await _open_tree(pilot, tui)
        text = lambda: " ".join(str(w.render()) for w in tui.screen.query("Static"))  # noqa: E731
        assert "hidden" not in text().replace("h hidden", "")
        await pilot.press("h")
        await pilot.pause(0.4)
        assert "reveal_true_values" in text()
        await pilot.press("h")
        await pilot.pause(0.4)
        assert "reveal_true_values" not in text()


@pytest.mark.tui_boot
async def test_a_dangerous_switch_still_asks_in_the_tree():
    fixture = copy.deepcopy(SMU)
    for info in [*fixture["session"]["instruments"], *fixture["instruments"].values()]:
        for sv in info.get("state", []):
            if sv["path"] == "output.enabled":
                sv["value"] = False
    tui, fake = unit_app(fixture)
    async with tui.run_test(size=(100, 32)) as pilot:
        await _open_tree(pilot, tui)
        _button(tui, "output.enabled").focus()
        await pilot.press("space")
        await pilot.pause(0.5)
        assert type(tui.screen).__name__ == "ConfirmScreen" and fake.configures == []


@pytest.mark.tui_boot
async def test_every_setting_the_palette_lists_can_be_reached():
    """A setting the instrument screen does not show opens the state tree on it instead of failing."""
    from simubench.tui import palette as P

    tui, _ = unit_app(copy.deepcopy(SMU))
    async with tui.run_test(size=(100, 32)) as pilot:
        from test_tui_t2 import open_instrument, settle

        await settle(pilot, tui)
        await open_instrument(pilot, tui, "dmm")
        await pilot.pause(0.4)
        shown = {b.control.path for b in tui.screen.query("ControlButton")}
        missing = [
            sv["path"]
            for sv in P.settable(next(i for i in tui.session_info["instruments"] if i["name"] == "dmm"))
            if sv["path"] not in shown
        ]
        assert missing, "the fixture should have a setting the instrument screen leaves out"
        tui.jump_to_field("dmm", missing[0], None)
        await pilot.pause(0.8)
        assert type(tui.screen).__name__ == "StateTreeScreen"
        assert tui.screen.focused is not None and tui.screen.focused.control.path == missing[0]
        assert tui.screen.focused.edit is not None  # and it is being edited
