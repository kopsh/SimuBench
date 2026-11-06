"""The pure part of editing a field where it stands (tui-screens.md 5.3): what typing and
cycling do to the editor's state, and how a typed number is judged."""

from __future__ import annotations

import pytest

from simubench.tui import controls as C

VOLTS = {"unit": "V", "min": -210, "max": 210}


@pytest.mark.unit
def test_typing_builds_the_buffer_and_backspace_takes_it_back():
    edit = C.FieldEdit("number")
    for ch in "7.5V":
        edit.type(ch)
    assert edit.buffer == "7.5V"
    edit.backspace()
    assert edit.buffer == "7.5"
    edit.backspace(), edit.backspace(), edit.backspace(), edit.backspace()
    assert edit.buffer == ""


@pytest.mark.unit
def test_only_printable_characters_are_typed():
    edit = C.FieldEdit("number")
    edit.type("\x1b")
    edit.type("")
    assert edit.buffer == ""


@pytest.mark.unit
def test_a_select_does_not_take_typed_characters():
    edit = C.FieldEdit.for_select(["A", "B"], "A")
    edit.type("x")
    assert edit.buffer == ""


@pytest.mark.unit
def test_cycling_wraps_both_ways_and_starts_at_the_current_option():
    edit = C.FieldEdit.for_select(["VOLT", "CURR", "RES"], "CURR")
    assert (edit.index, edit.start, edit.choice) == (1, 1, "CURR")
    edit.cycle(1), edit.cycle(1)
    assert edit.choice == "VOLT"  # wrapped
    edit.cycle(-1)
    assert edit.choice == "RES"


@pytest.mark.unit
def test_an_unknown_current_value_starts_on_the_first_option():
    assert C.FieldEdit.for_select(["A", "B"], "Z").choice == "A"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "value"), [("5", 5.0), ("7.5V", 7.5), ("200 mV", 0.2), ("-12", -12.0), ("1e1", 10.0)]
)
def test_a_typed_number_is_converted_to_the_settings_unit(text, value):
    assert C.parse_input(text, VOLTS) == pytest.approx(value)


@pytest.mark.unit
def test_out_of_range_is_told_from_not_a_number():
    with pytest.raises(ValueError, match="range"):
        C.parse_input("999", VOLTS)
    with pytest.raises(ValueError, match="range"):
        C.parse_input("-211", VOLTS)
    with pytest.raises(ValueError, match="number"):
        C.parse_input("abc", VOLTS)
    with pytest.raises(ValueError, match="number"):
        C.parse_input("5 A", VOLTS)  # a wrong unit


@pytest.mark.unit
def test_parse_input_number_itself():
    """The renamed helper (issue 641 → issue 691): bare numbers, prefixed ones, and its errors."""
    assert C.parse_input_number("5") == 5.0  # bare: the state's unit
    assert C.parse_input_number("5000 mV", "V") == pytest.approx(5.0)
    assert C.parse_input_number(" 7.5V ", "V") == pytest.approx(7.5)  # plain unit, whitespace
    with pytest.raises(ValueError, match="not a number"):
        C.parse_input_number("abc", "V")
    with pytest.raises(ValueError, match="no unit"):
        C.parse_input_number("5 mV", None)  # a suffix needs the state's unit to convert into
