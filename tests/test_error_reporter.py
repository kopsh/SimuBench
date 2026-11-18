"""ErrorReporter directly (#1380): what an error looks like on the queue, in the ``SYST:ERR?``
reply and in the instrument event — the definition's own error table (a real model's manual
texts, D42), the -113 classification of a header the definition does not have, and the
bad-parameter naming (tui-design.md 1.1). On the shared fake host (helpers.FakeHost, no bench).
Unit tier: one behaviour per test."""

from __future__ import annotations

import pytest

from helpers import FakeHost, seam_instrument
from simubench.engine.error_reporter import _Ignored
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.errors import ScpiError
from simubench.scpi.parse import parse_unit, resolve_tokens

pytestmark = pytest.mark.unit

_FORMAT = {"number": "nr3", "digits": 6, "sign": True, "exponent_digits": 2, "error_code": "signed"}

DATA = {
    "schema": "simubench.instrument/2",
    "id": "reporter-demo",
    "manufacturer": "ACME",
    "model": "REP",
    "class": "multimeter",
    "format": _FORMAT,
    "state": {
        "out.frequency": {"type": "float", "unit": "Hz", "min": 1.0, "max": 100000.0, "default": 1000.0}
    },
    "commands": [{"header": ":FREQuency", "state": "out.frequency"}],
    "errors": {-222: "Range error", -113: "Syntax error; not implemented in this firmware", 0: "No events"},
}

REAL = {**DATA, "reference": {"title": "ACME REP Manual", "version": "2026-11"}}

DEFINITION = InstrumentDefinition.model_validate(DATA)


def _inst(**over):
    definition = over.pop("definition", DEFINITION)
    return seam_instrument(definition, **over)


def _unit(message: str):
    u = parse_unit(message.encode())
    return u, resolve_tokens(u, ())


def test_a_pushed_error_latches_the_esr_and_reaches_the_event():
    inst = _inst()
    inst.push_error(-222, "overload")
    assert [(e.code, e.message) for e in inst.errors.peek_all()] == [(-222, "Range error")]
    assert inst.esr == 1 << 4  # an execution error's ESR bit
    events = [e for e in inst.session.events if e["kind"] == "error"]
    assert events == [{"kind": "error", "code": -222, "message": "Range error (overload)"}]


def test_a_standard_error_keeps_its_standard_text():
    definition = InstrumentDefinition.model_validate({k: v for k, v in DATA.items() if k != "errors"})
    inst = _inst(definition=definition)
    inst.push_error(-222, "overload")
    assert [e.message for e in inst.errors.peek_all()] == ["Data out of range; overload"]
    events = [e for e in inst.session.events if e["kind"] == "error"]
    assert events[0]["message"] == "Data out of range; overload"  # the detail is already in it


def test_the_queue_holds_the_definition_s_limit():
    from simubench.scpi.errors import ErrorQueue

    queue = ErrorQueue(2, DATA["errors"].get(-350))
    queue.push(ScpiError(-222))
    queue.push(ScpiError(-113))
    queue.push(ScpiError(-104))
    # the last slot reports the overflow; the error that would have taken it is dropped
    assert [e.code for e in queue.peek_all()] == [-222, -350]


def test_the_error_reply_carries_the_table_s_no_error_text():
    inst = _inst()
    reply = inst._errs._error_reply(ScpiError(0))
    assert reply == b'+0,"No events"'  # error_code "signed" signs the plain zero


def test_a_plain_format_leaves_the_zero_unsigned():
    definition = InstrumentDefinition.model_validate({**DATA, "format": {**_FORMAT, "error_code": "plain"}})
    inst = _inst(definition=definition)
    assert inst._errs._error_reply(ScpiError(0)) == b'0,"No events"'


def test_an_undefined_header_is_classified_against_the_definition():
    inst = _inst()
    err = inst._errs._undefined(*_unit(":NOSUCH?"))
    assert err.code == -113
    assert err.reason == "not_in_definition"
    assert err.origin == inst.source_file
    assert inst._errs._undefined_text(":NOSUCH?") is None  # not a real model: the standard text


def test_a_real_model_s_undefined_header_names_the_definition():
    inst = _inst(definition=InstrumentDefinition.model_validate(REAL))
    err = inst._errs._undefined(*_unit(":NOSUCH?"))
    assert err.text == "Syntax error; not implemented in this firmware;not implemented in this definition"


def test_an_ignored_unknown_header_raises_to_be_swallowed():
    definition = InstrumentDefinition.model_validate({**DATA, "unknown_commands": "ignore"})
    inst = _inst(definition=definition)
    with pytest.raises(_Ignored):
        inst._errs._undefined(*_unit(":NOSUCH?"))


def test_a_bad_parameter_is_named_with_its_declared_range():
    inst = _inst()
    err = inst._errs._bad_parameter(ScpiError(-222), "out.frequency", DEFINITION.state["out.frequency"])
    assert err.reason == "bad_parameter"
    assert err.param == "out.frequency"
    assert err.range == "1 … 100000 Hz"
    assert err.origin == inst.source_file


def test_a_bad_parameter_keeps_a_declared_category():
    inst = _inst()
    declared = ScpiError(-224, "ACV: rear terminals only; not implemented in this definition")
    assert inst._errs._bad_parameter(declared, "out.function", None) is declared
    classified = ScpiError(-222, "too high", reason="bad_parameter")
    assert inst._errs._bad_parameter(classified, "out.frequency", None) is classified


def test_an_unnamed_parameter_error_is_classified():
    inst = _inst()
    err = inst._errs._parameter_error(ScpiError(-104, "junk"))
    assert err.reason == "bad_parameter"
    kept = ScpiError(-222, "too high", reason="bad_parameter")
    assert inst._errs._parameter_error(kept) is kept
    device = ScpiError(-350, "queue overflow")
    assert inst._errs._parameter_error(device) is device  # device errors never come through here


def test_a_component_error_is_queued_through_the_facade():
    inst = _inst()
    inst.push_error(-241, "option not installed")
    assert [(e.code, e.message) for e in inst.errors.peek_all()] == [
        (-241, "Hardware missing; option not installed")
    ]
    assert inst.esr == 1 << 4  # -241 is an execution error


def test_a_host_without_a_member_is_named_at_construction():
    host = FakeHost()
    del host.seed
    with pytest.raises(ValueError) as caught:
        seam_instrument(DEFINITION, host=host)
    assert "seed" in str(caught.value)
