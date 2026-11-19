"""fit_value: one judgement of "does this value fit the setting" for the static validators and
the engine (issue 625). The four bodies the validators used to carry (bench state options,
`_check_write`, `_panel_value`, the quantities' `sets`) decided by their own rules — a
definition writing the manual's short form (`VOLT` for the option `VOLTage`) was refused
statically while the word was legal over SCPI, and a special value over the maximum was refused
on the panel while the engine accepted it. They now all ask fit_value, whose rule is the
engine's own (`Instrument._validate`, plus the parse layer for words); the last part locks that
agreement the way tests/test_command_equivalence.py locks routing: static verdict and run-time
verdict must match, value for value, over the values of the kind the parse layer produces.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from helpers import FakeHost
from simubench.engine.instrument import Instrument
from simubench.engine.settings import validate_value
from simubench.engine.validate import validate_data
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition, InstrumentInstance
from simubench.models.definition import InstrumentDefinition, StateSpec
from simubench.scpi import values as V
from simubench.scpi.errors import ScpiError

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module", autouse=True)
def _warm_component_registry():
    """The validators touch the lazy component-registry import (D47); pay it once per module so
    a test's own work — under a millisecond — stays inside the D49 unit budget."""
    from simubench.engine.component import component_types

    component_types()


def spec(**kw: Any) -> StateSpec:
    return StateSpec.model_validate(kw)


ENUM = spec(type="enum", options=["VOLTage", "VOLTage:AC", "CURRent"], default="VOLTage")
NUMBER = spec(type="float", min=0.0, max=10.0, special={"OVLD": 9.9e37}, default=1.0)


# ---- the rule itself ------------------------------------------------------------------------


def test_an_enum_takes_the_long_form_and_the_option_itself():
    assert V.fit_value(ENUM, "VOLTage") == (None, None)
    assert V.fit_value(ENUM, "voltage") == (None, "VOLTage")
    assert V.fit_value(ENUM, '"VOLTage"') == (None, "VOLTage")  # a string parameter's quotes


def test_an_enum_takes_the_short_form_of_every_node_of_a_compound():
    assert V.fit_value(ENUM, "VOLT:AC") == (None, "VOLTage:AC")
    assert V.fit_value(ENUM, "volt:ac") == (None, "VOLTage:AC")
    assert V.fit_value(ENUM, "VOLT") == (None, "VOLTage")  # the first node's short form alone


def test_an_enum_refuses_what_no_option_names():
    problem, fix = V.fit_value(ENUM, "POWer")
    assert problem == "不接受 'POWer'"
    assert fix == "可选值：VOLTage, VOLTage:AC, CURRent"
    assert V.fit_value(ENUM, 3)[0] is not None  # not a word at all
    assert V.fit_value(ENUM, "VOLT:CURR")[0] is not None  # nodes must all match


def test_a_special_value_is_exempt_from_the_range_on_both_bounds():
    assert V.fit_value(NUMBER, 9.9e37) == (None, None)  # over the maximum, as OVLD
    assert V.fit_value(spec(type="float", min=5.0, max=10.0, special={"AUTO": 0.0}), 0.0) == (
        None,
        None,
    )  # under the minimum, as AUTO


def test_a_number_out_of_range_is_reported_with_the_bounds():
    assert V.fit_value(NUMBER, 11.0) == ("11.0 超出范围 [0.0, 10.0]", None)
    assert V.fit_value(NUMBER, -0.1)[0] is not None
    assert V.fit_value(NUMBER, math.nan)[0] is not None  # the engine refuses NaN with -222


def test_a_number_at_its_bound_fits_as_the_engine_tolerates():
    # the same relative tolerance the engine applies (#407): a bound's float representation
    # error must not refuse a value the run time accepts
    assert V.fit_value(NUMBER, 10.0) == (None, None)
    assert V.fit_value(NUMBER, 10.0 + 10.0 * 1e-13) == (None, None)


def test_a_bool_and_a_string_are_not_numbers():
    assert V.fit_value(NUMBER, True) == ("True 不是数值", None)
    assert V.fit_value(NUMBER, "5") == ("'5' 不是数值", None)
    assert V.fit_value(spec(type="bool"), True) == (None, None)
    assert V.fit_value(spec(type="bool"), "ON")[0] is not None
    assert V.fit_value(spec(type="string"), "text") == (None, None)


# ---- the false positives, locked from the definition side ------------------------------------

BASE = {
    "schema": "simubench.instrument/2",
    "id": "sb-fit",
    "manufacturer": "SimuBench",
    "model": "SB-FIT",
    "class": "multimeter",
    "description": "测试用：值-规格判定同源。",
    "state": {
        "func": {"type": "enum", "options": ["VOLTage", "VOLTage:AC", "CURRent"], "default": "VOLTage"},
        "level": {"type": "float", "min": 0.0, "max": 10.0, "special": {"OVLD": 9.9e37}, "default": 1.0},
    },
    "components": [],
    "commands": [
        {
            "header": "CONF",
            "query": False,
            "writes": {"func": "VOLT"},
            "params": [{"name": "level", "type": "float", "unit": "V", "state": "level", "optional": True}],
        },
    ],
}


def _writes(writes: dict) -> dict:
    return {**BASE, "commands": [{**BASE["commands"][0], "writes": writes}]}


def test_a_write_constant_in_the_short_form_fits():
    # the manual's word, not the option's long form: the engine stores the option it names
    assert [d.code for d in validate_definition_data(_writes({"func": "VOLT"}))[1].diagnostics] == []


def test_a_write_constant_in_a_compound_short_form_fits():
    assert [d.code for d in validate_definition_data(_writes({"func": "volt:ac"}))[1].diagnostics] == []


def test_a_write_constant_outside_the_options_is_reported_in_chinese():
    [d] = validate_definition_data(_writes({"func": "POWer"}))[1].diagnostics
    assert d.code == "E-DEF-WRITE"
    assert d.message == "'func' 不接受 'POWer'"
    assert d.suggestion == "可选值：VOLTage, VOLTage:AC, CURRent"


def test_a_write_constant_over_the_maximum_with_a_special_value_fits():
    assert [d.code for d in validate_definition_data(_writes({"level": 9.9e37}))[1].diagnostics] == []


def test_a_quantity_set_in_the_short_form_fits():
    data = {
        **BASE,
        "commands": [
            {
                "header": "OUT",
                "query": False,
                "params": [
                    {
                        "name": "q",
                        "type": "quantities",
                        "units": {"V": {"state": "level", "sets": {"func": "VOLT"}}},
                    }
                ],
            }
        ],
    }
    assert [d.code for d in validate_definition_data(data)[1].diagnostics] == []


def test_a_quantity_set_outside_the_options_is_still_reported():
    data = {
        **BASE,
        "commands": [
            {
                "header": "OUT",
                "query": False,
                "params": [
                    {
                        "name": "q",
                        "type": "quantities",
                        "units": {"V": {"state": "level", "sets": {"func": "POWer"}}},
                    }
                ],
            }
        ],
    }
    [d] = validate_definition_data(data)[1].diagnostics
    assert d.code == "E-DEF-QUANTITY" and d.message == "'func' 不接受 'POWer'"


def test_a_panel_key_with_a_special_value_over_the_maximum_fits():
    data = {
        **BASE,
        "display": {
            "panel": {
                "groups": [
                    {
                        "label": "量程",
                        "controls": [
                            {
                                "state": "level",
                                "control": "buttons",
                                "keys": [{"value": 9.9e37, "label": "OVLD"}],
                            }
                        ],
                    }
                ]
            }
        },
    }
    assert [d.code for d in validate_definition_data(data)[1].diagnostics] == []


def test_a_bench_state_in_a_compound_short_form_fits():
    bench = {
        "schema": "simubench.bench/1",
        "name": "fit",
        "instruments": {"x": {"model": "SB-FIT", "state": {"func": "volt:ac"}}},
    }
    _, report = validate_data(bench, lambda _model: InstrumentDefinition.model_validate(BASE))
    assert [d.code for d in report.diagnostics] == []


def test_a_bench_state_outside_the_options_is_still_reported():
    bench = {
        "schema": "simubench.bench/1",
        "name": "fit",
        "instruments": {"x": {"model": "SB-FIT", "state": {"func": "POWer"}}},
    }
    _, report = validate_data(bench, lambda _model: InstrumentDefinition.model_validate(BASE))
    [d] = report.diagnostics
    assert d.code == "E-STATE-OPTION" and d.message == "func 不接受 'POWer'"


# ---- the engine decides the same --------------------------------------------------------------


_DEFINITION = InstrumentDefinition.model_validate(BASE)
_INSTRUMENT = Instrument("x", _DEFINITION, InstrumentInstance(model="SB-FIT"), FakeHost())


def _runtime_verdict(st: StateSpec, value: Any) -> int | None:
    """The error code the engine raises for the value, or None when it accepts it."""
    try:
        validate_value(st, value)
    except ScpiError as e:
        return e.code
    return None


def test_the_engine_accepts_the_same_words_the_static_side_does():
    # a value written outside the parse layer (a definition's constants, the UI's configure)
    # names the option in any word form, and the option itself is stored
    assert validate_value(ENUM, "VOLT") == "VOLTage"
    assert validate_value(ENUM, "volt:ac") == "VOLTage:AC"
    assert validate_value(ENUM, "VOLTage:AC") == "VOLTage:AC"
    with pytest.raises(ScpiError) as e:
        validate_value(ENUM, "POWer")
    assert e.value.code == -224 and e.value.message == "Illegal parameter value"


def test_the_engine_keeps_the_range_and_special_semantics():
    assert validate_value(NUMBER, 9.9e37) == 9.9e37  # a special value skips the range
    with pytest.raises(ScpiError) as e:
        validate_value(NUMBER, 11.0)
    assert e.value.code == -222


ENUM_LIST = spec(type="enum_list", options=["VOLT", "CURR"], default=["VOLT"])
INT_LIST = spec(type="int_list", min=0, max=10, default=[1])


@pytest.mark.parametrize(
    ("st", "value", "code"),
    [
        (NUMBER, 10**400, -222),  # too large for a float: out of range like infinity (#1500)
        (NUMBER, -(10**400), -222),
        (INT_LIST, [1, math.inf], -222),
        (ENUM_LIST, 5, -104),  # a number where a list of options belongs
        (INT_LIST, "abc", -104),  # a string where a list of integers belongs
        (INT_LIST, None, -104),
    ],
)
def test_a_value_of_the_wrong_type_or_size_is_a_scpi_error(st, value, code):
    """#1500: any value JSON can carry is a SCPI error, never an OverflowError or TypeError."""
    assert _runtime_verdict(st, value) == code


def test_fit_value_and_the_engine_agree_on_enum_values():
    """Every word (and every non-word) gets the same verdict on both sides."""
    for value in ["VOLT", "volt:ac", "VOLTage:AC", "VOLT:CURR", "POWer", '"VOLTage"', 3, None, True]:
        problem, _fix = V.fit_value(ENUM, value)
        assert (problem is None) == (_runtime_verdict(ENUM, value) is None), value


def test_fit_value_and_the_engine_agree_on_numeric_values():
    """The values of the kind the parse layer produces — numbers: the range, the specials and
    the tolerance are decided alike. (The engine's float() coercion of strings and bools is a
    defence for the parse layer's own typed results, which the static side never sees.)"""
    st = NUMBER
    for value in [0.0, 1.0, 5.0, 10.0, 11.0, -1.0, 9.9e37, 10.0 + 10.0 * 1e-13, math.nan]:
        problem, _fix = V.fit_value(st, value)
        assert (problem is None) == (_runtime_verdict(st, value) is None), value
    st = spec(type="int", min=1, max=6)
    for value in [1, 6, 7, 0]:
        problem, _fix = V.fit_value(st, value)
        assert (problem is None) == (_runtime_verdict(st, value) is None), value


def test_fit_value_and_the_engine_agree_on_bool_values():
    st = spec(type="bool")
    for value in [True, False]:
        problem, _fix = V.fit_value(st, value)
        assert (problem is None) == (_runtime_verdict(st, value) is None), value


def test_a_session_writes_the_short_form_constant_into_the_state():
    """End to end on the engine: the command with `writes: {func: VOLT}` runs, and the state
    holds the option — no error queued."""
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "fit", "instruments": {"x": {"model": "SB-FIT"}}}
    )
    from simubench.engine.session import Session

    s = Session(bench, lambda _id: _DEFINITION, clock="stepped", seed=1)

    def run(cmd: str) -> str:
        s.write("x", cmd.encode() + b"\n")
        out = s.read("x")
        return out.decode().strip() if out is not None else ""

    run("CONF 2.0")
    assert s.instrument("x").get_state("func") == "VOLTage"
    assert run("SYST:ERR?") == '0,"No error"'
