"""CommandRunner directly (#1380): one unit's dispatch to its command, the setting and
channel-list commands, and the parse layer — units, the limits now (MIN/MAX/DEF, #663/#721),
special words and keyword tables. The instrument is built on the shared fake host
(helpers.FakeHost, the InstrumentHost seam's stub): no bench, no circuit, no component registry —
writes go through the write transaction, refusals come back as ScpiError, and a write's
``state_changed`` lands in the host's events. Unit tier: one behaviour per test."""

from __future__ import annotations

import pytest

from helpers import dispatch_unit, seam_instrument
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.errors import ScpiError

pytestmark = pytest.mark.unit

_FORMAT = {"number": "nr3", "digits": 6, "sign": True, "exponent_digits": 2}

DEFINITION = InstrumentDefinition.model_validate(
    {
        "schema": "simubench.instrument/1",
        "id": "runner-demo",
        "manufacturer": "ACME",
        "model": "RUN",
        "class": "multimeter",
        "format": _FORMAT,
        "options": [{"id": "P03"}],
        "state": {
            "out.frequency": {"type": "float", "unit": "Hz", "min": 1.0, "max": 100000.0, "default": 1000.0},
            "out.mode": {"type": "string", "default": ""},
            "out.level": {
                "type": "float",
                "unit": "V",
                "min": 0.0,
                "max": 10.0,
                "default": 1.0,
                "special": {"AUTO": -1.0},
            },
            "out.range": {"type": "float", "unit": "V", "min": 0.0, "max": 100.0, "default": 1.0},
            "out.function": {"type": "enum", "options": ["DCV", "ACV"], "default": "DCV"},
            "out.pll": {"type": "bool", "default": False},
        },
        "commands": [
            {"header": ":FREQuency", "state": "out.frequency"},
            {"header": ":LEVel", "state": "out.level"},
            {"header": ":RANGe", "state": "out.range", "factor": 10.0},
            {"header": ":FUNCtion", "state": "out.function"},
            {
                "header": ":CONFigure",
                "params": [
                    {
                        "name": "word",
                        "type": "float",
                        "state": "out.level",
                        "keywords": {"DCV": {"out.function": "DCV"}},
                    }
                ],
            },
            {
                "header": ":VOLT",
                "params": [
                    {
                        "name": "level",
                        "type": "float",
                        "state": "out.level",
                        "sets": {"out.pll": True},
                    }
                ],
            },
            {"header": ":LIMit<n>", "state": "out.frequency", "suffixes": {"n": [1, 2]}},
            {"header": ":APERture", "state": "out.frequency", "option": "P03"},
            {"header": ":INPut", "state": "out.level", "query": False},
            {
                "header": ":VOLT:MODE",
                "state": "out.mode",
                "params": [{"name": "mode", "type": "string"}],
                "when": {"mode": "FIXED"},
            },
        ],
    }
)

CHANNELS = InstrumentDefinition.model_validate(
    {
        "schema": "simubench.instrument/1",
        "id": "runner-channels",
        "manufacturer": "ACME",
        "model": "RUN2",
        "class": "multimeter",
        "format": _FORMAT,
        "channels": {"count": 2},
        "channel_state": {"level": {"type": "float", "unit": "V", "min": 0.0, "max": 10.0, "default": 1.0}},
        "commands": [
            {
                "header": ":VOLTage",
                "params": [
                    {"name": "level", "type": "float", "state": "ch.level"},
                    {"name": "channels", "type": "channel_list"},
                ],
            }
        ],
    }
)


def _errs(queue):
    return [(e.code, e.message) for e in queue.peek_all()]


# ---- dispatch --------------------------------------------------------------------------------------


def test_a_setting_writes_and_the_query_answers():
    inst = seam_instrument(DEFINITION)
    reply, ns = dispatch_unit(inst, ":FREQuency 2 kHz")
    assert reply is None and ns == 0
    assert inst.state["out.frequency"] == 2000.0
    assert inst.session.events == [
        {
            "kind": "state",
            "path": "out.frequency",
            "old": 1000.0,
            "new": 2000.0,
            "electrical": False,
            "source": "scpi",
        }
    ]
    reply, _ = dispatch_unit(inst, ":FREQuency?")
    assert reply == b"+2.000000E+03"


def test_an_enum_setting_writes_and_reads_the_model_s_word():
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":FUNCtion ACV")
    assert inst.state["out.function"] == "ACV"
    reply, _ = dispatch_unit(inst, ":FUNCtion?")
    assert reply == b"ACV"


def test_a_header_used_the_wrong_way_is_refused():
    inst = seam_instrument(DEFINITION)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":INPut?")
    assert caught.value.code == -113
    assert "not a query" in str(caught.value)


def test_an_unknown_header_is_not_in_this_definition():
    inst = seam_instrument(DEFINITION)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":NOSUCH?")
    assert caught.value.code == -113
    assert caught.value.reason == "not_in_definition"
    assert _errs(inst.errors) == []  # a direct dispatch raises; receive() is what queues it


def test_a_refused_dispatch_reaches_the_queue_classified_through_receive():
    inst = seam_instrument(DEFINITION)
    inst.receive(b":NOSUCH?\n")
    assert _errs(inst.errors)[0][0] == -113
    events = [e for e in inst.session.events if e["kind"] == "error"]
    assert events[0]["code"] == -113 and events[0]["reason"] == "not_in_definition"


def test_a_suffix_outside_the_declaration_is_refused():
    inst = seam_instrument(DEFINITION)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":LIM3 5")
    assert caught.value.code == -114


def test_an_option_command_without_the_option_is_hardware_missing():
    inst = seam_instrument(DEFINITION)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":APERture 100")
    assert caught.value.code == -241 and "P03" in str(caught.value)


def test_the_installed_option_s_command_runs():
    inst = seam_instrument(DEFINITION, options=["P03"])
    dispatch_unit(inst, ":APERture 100")
    assert inst.state["out.frequency"] == 100.0


def test_a_when_refuses_the_values_it_does_not_name():
    inst = seam_instrument(DEFINITION)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":VOLT:MODE SWEEP")
    assert caught.value.code == -224
    dispatch_unit(inst, ":VOLT:MODE FIXED")
    assert inst.state["out.mode"] == "FIXED"


# ---- the limits now ---------------------------------------------------------------------------------


def test_min_max_write_the_dynamic_limit_and_def_the_default():
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":FREQuency MIN")
    assert inst.state["out.frequency"] == 1.0
    dispatch_unit(inst, ":FREQuency MAX")
    assert inst.state["out.frequency"] == 100000.0
    dispatch_unit(inst, ":FREQuency DEF")
    assert inst.state["out.frequency"] == 1000.0


def test_a_query_with_a_form_word_answers_the_limit():
    inst = seam_instrument(DEFINITION)
    reply, _ = dispatch_unit(inst, ":FREQuency? MAX")
    assert reply == b"+1.000000E+05"


def test_an_out_of_range_write_is_refused_with_the_parameter_named():
    inst = seam_instrument(DEFINITION)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":FREQuency 1e6")
    assert caught.value.code == -222
    assert caught.value.param == "out.frequency"
    assert caught.value.range == "1 … 100000 Hz"


def test_a_special_word_is_a_mode_value_not_an_extreme():
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":LEVel AUTO")
    assert inst.state["out.level"] == -1.0  # outside the declared 0 … 10: a mode, not clamped
    dispatch_unit(inst, ":LEVel MIN")
    assert inst.state["out.level"] == 0.0


def test_the_factor_scales_the_command_number():
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":RANGe 50")
    assert inst.state["out.range"] == 5.0
    reply, _ = dispatch_unit(inst, ":RANGe?")
    assert reply == b"+5.000000E+01"


# ---- keyword tables ---------------------------------------------------------------------------------


def test_a_keyword_writes_its_own_settings():
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":CONFigure DCV")
    assert inst.state["out.function"] == "DCV"
    assert _errs(inst.errors) == []


def test_sets_go_with_a_value_and_def_keeps_the_commands_own():
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":VOLT 5")
    assert inst.state["out.level"] == 5.0 and inst.state["out.pll"] is True
    inst = seam_instrument(DEFINITION)
    dispatch_unit(inst, ":VOLT AUTO")
    assert inst.state["out.level"] == -1.0 and inst.state["out.pll"] is False  # #325: AUTO keeps it


# ---- channel lists ----------------------------------------------------------------------------------


def test_a_channel_list_writes_per_channel_and_the_query_joins_in_list_order():
    inst = seam_instrument(CHANNELS)
    dispatch_unit(inst, ":VOLTage 5,(@1,2)")
    assert inst.state["ch1.level"] == 5.0 and inst.state["ch2.level"] == 5.0
    reply, _ = dispatch_unit(inst, ":VOLTage? (@2,1)")
    assert reply == b"+5.000000E+00,+5.000000E+00"


def test_a_channel_list_refuses_what_the_rules_say():
    inst = seam_instrument(CHANNELS)
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":VOLTage 5,(@1,1)")
    assert caught.value.code == -108  # repeated: the definition does not declare it
    with pytest.raises(ScpiError) as caught:
        dispatch_unit(inst, ":VOLTage 5,(@2:1)")
    assert caught.value.code == -222  # descending: ditto


# ---- the parse layer --------------------------------------------------------------------------------


def test_parse_applies_the_unit_and_the_number_forms():
    runner = seam_instrument(DEFINITION).runner
    assert runner.parse_setting("out.frequency", b"2 kHz") == 2000.0
    assert runner.parse_setting("out.frequency", b"1e3") == 1000.0
    assert runner.parse_setting("out.frequency", b"+2.5") == 2.5


def test_parse_refuses_a_word_the_definition_does_not_model():
    runner = seam_instrument(DEFINITION).runner
    st = DEFINITION.state["out.function"].model_copy(update={"not_modeled": {"ACV": "rear terminals only"}})
    with pytest.raises(ScpiError) as caught:
        runner._parse_state(st, b"ACV", "out.function")
    assert caught.value.code == -224
    assert "not implemented in this definition" in str(caught.value)


def test_format_speaks_the_definition_s_form():
    runner = seam_instrument(DEFINITION).runner
    assert runner.format_setting("out.frequency") == b"+1.000000E+03"
    assert runner.format_setting("out.function") == b"DCV"


def test_format_number_uses_the_definition_s_number_form():
    inst = seam_instrument(DEFINITION)
    assert inst.format_number(2000.0) == b"+2.000000E+03"
