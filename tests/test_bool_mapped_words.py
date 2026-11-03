"""A bool setting mapped to a word parameter's words (#429): the Trueform has one switch per
modulation (AM:STATe, FM:STATe, ...), so ``awg_modulator.type`` reads a chain of bool maps."""

from __future__ import annotations

from pathlib import Path

import yaml

from simubench.engine.validate_definition import validate_definition_data

ROOT = Path(__file__).resolve().parents[1]
DEFS = ROOT / "validation" / "instruments"


def _awg(type_value) -> dict:
    d = yaml.safe_load((DEFS / "sb-awg-2.yaml").read_text("utf-8"))
    d["channel_state"]["fm_on"] = {"type": "bool", "default": False}
    for c in d["channel_components"]:
        if c["name"] == "mod":
            c["params"]["type"] = type_value
    return d


def _param_errors(d: dict) -> list[str]:
    return [x.code for x in validate_definition_data(d)[1].diagnostics if x.code == "E-DEF-PARAM-VALUE"]


def test_a_bool_mapped_to_words_on_both_values_is_accepted():
    assert _param_errors(_awg({"from": "$ch.fm_on", "map": {"True": "FM", "False": "AM"}})) == []
    assert _param_errors(_awg({"from": "$ch.fm_on", "map": {"1": "FM", "0": "AM"}})) == []
    # a nested mapping as a value resolves at run time from its own setting
    nested = {"from": "$ch.fm_on", "map": {"True": "FM", "False": {"from": "$ch.mod_type", "map": {}}}}
    assert _param_errors(_awg(nested)) == []


def test_a_bool_still_needs_both_values_and_known_words():
    assert _param_errors(_awg("$ch.fm_on")) == ["E-DEF-PARAM-VALUE"]
    assert _param_errors(_awg({"from": "$ch.fm_on", "map": {"True": "FM"}})) == ["E-DEF-PARAM-VALUE"]
    assert _param_errors(_awg({"from": "$ch.fm_on", "map": {"True": "FM", "False": "XX"}})) == [
        "E-DEF-PARAM-VALUE"
    ]


def test_a_bool_setting_reads_through_its_string_keys():
    """At run time too (the validator alone accepted the map, #429): False/True against "False"/"True"
    or "0"/"1", and a nested mapping as the value."""
    from simubench.engine.component import map_setting

    assert map_setting({"map": {"True": "FM", "False": "AM"}}, True) == "FM"
    assert map_setting({"map": {"True": "FM", "False": "AM"}}, False) == "AM"
    assert map_setting({"map": {"1": "FM", "0": "AM"}}, True) == "FM"
    nested = {"from": "$ch.pm_on", "map": {"True": "PM"}}
    assert map_setting({"map": {"True": "FM", "False": nested}}, False) == nested
