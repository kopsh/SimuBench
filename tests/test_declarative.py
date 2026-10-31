"""Declarative commands and replies (instrument-authoring.md 2, issue 42): the dialect, reply
templates, ``pairs`` parameters, multiplier suffixes and component parameter mappings."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from simubench.engine.session import Session
from simubench.engine.validate_definition import validate_definition_data
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

DEMO = {
    "schema": "simubench.instrument/1",
    "id": "demo",
    "manufacturer": "ACME",
    "model": "DEMO",
    "class": "oscilloscope",
    "unknown_commands": "ignore",
    "dialect": {"echo_header": "$comm.header", "multipliers": {"K": 1e3, "M": 1e6, "MA": 1e6}},
    "format": {"number": "engineering", "boolean": "word"},
    "channels": {"count": 2},
    "state": {
        "comm.header": {"type": "enum", "options": ["OFF", "SHORT", "LONG"], "default": "SHORT"},
        "memory.size": {"type": "int", "min": 500, "max": 50_000_000, "default": 10_000},
        "trigger.type": {"type": "enum", "options": ["EDGE"], "default": "EDGE"},
        "trigger.source": {"type": "enum", "options": ["C1", "C2"], "default": "C1"},
        "trigger.hold": {"type": "enum", "options": ["OFF", "TI"], "default": "OFF"},
        "timebase.scale": {"type": "float", "unit": "S", "min": 1e-9, "max": 10.0, "default": 1e-3},
    },
    "channel_state": {
        "scale": {"type": "float", "unit": "V", "min": 1e-3, "max": 10.0, "default": 0.05},
        "display": {"type": "bool", "default": False},
        "wave": {"type": "enum", "options": ["SQUare", "SINe", "TRIangle"], "default": "SQUare"},
        "duty": {"type": "float", "min": 1e-6, "max": 1.0, "default": 0.5},
    },
    "channel_components": [
        {
            "name": "gen",
            "type": "function_generator_core",
            "params": {
                "function": {
                    "from": "$ch.wave",
                    "map": {"SQUare": "SQUare", "SINe": "SINusoid", "TRIangle": "RAMP"},
                },
                "symmetry": {"from": "$ch.wave", "map": {"TRIangle": 50.0}, "default": 100.0},
                "duty": {"from": "$ch.duty", "scale": 100.0},
                "offset": {"from": "$ch.scale", "scale": 2.0, "offset": 0.5},
            },
        }
    ],
    "commands": [
        {"header": "Comm_HeaDeR", "state": "comm.header"},
        {"header": "C<ch>:Volt_DIV", "state": "ch.scale", "reply": "{header} {value} V"},
        {"header": "C<ch>:TRAce", "state": "ch.display"},
        {"header": "C<ch>:WAVe", "state": "ch.wave"},
        {"header": "C<ch>:DUTY", "state": "ch.duty"},
        {"header": "Time_DIV", "state": "timebase.scale", "reply": "{header} {value} S"},
        {"header": "Memory_SIZe", "state": "memory.size", "format": {"integer": "engineering"}},
        {
            "header": "TRig_SElect",
            "params": [
                {"name": "type", "type": "enum", "state": "trigger.type"},
                {
                    "name": "rest",
                    "type": "pairs",
                    "optional": True,
                    "keys": {"SR": "trigger.source", "HT": "trigger.hold", "HV": None},
                },
            ],
        },
        {
            "header": "TimeBase_SUmmary",
            "set": False,
            "params": [{"name": "x", "type": "pairs", "keys": {"MS": "memory.size"}}],
            "reply": "{header} {timebase.scale} S,{memory.size}",
        },
    ],
}


def open_demo(data: dict = DEMO):
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": "demo"}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)
    inst = session.instruments["x"]

    def query(cmd: str) -> str:
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    def write(cmd: str) -> None:
        session.write("x", cmd.encode() + b"\n")

    return session, inst, query, write


def error(query) -> str:
    return query("SYST:ERR?")


# ---- dialect: header echo, engineering numbers, multipliers --------------------------------------


def test_the_header_echo_follows_a_setting():
    _, _, query, write = open_demo()
    assert query("C2:VDIV?") == "C2:VDIV 50E-3 V"
    write("CHDR LONG")
    assert query("C2:VDIV?") == "C2:VOLT_DIV 50E-3 V"
    assert query("TDIV?") == "TIME_DIV 1E-3 S"
    write("CHDR OFF")
    assert query("C2:VDIV?") == "50E-3 V"  # the header and the space after it go
    assert query("C1:TRA?") == "OFF"
    assert query("CHDR?") == "OFF"


def test_engineering_numbers_and_words():
    _, _, query, write = open_demo()
    write("C1:VDIV 200 mV")
    write("C1:TRA ON")
    assert query("C1:VDIV?") == "C1:VDIV 200E-3 V"
    assert query("C1:TRA?") == "C1:TRA ON"
    assert query("MSIZ?") == "MSIZ 10E+3"  # the command's own format: integers in engineering notation


@pytest.mark.parametrize(
    ("text", "points"), [("30000", 30000), ("10K", 10000), ("2.5MA", 2_500_000), ("1M", 10**6)]
)
def test_multiplier_suffixes_on_numbers_without_a_unit(text, points):
    _, inst, query, write = open_demo()
    write(f"MSIZ {text}")
    assert inst.get_state("memory.size") == points
    assert error(query) == '0,"No error"'


def test_a_unit_keeps_scpi_suffixes():
    _, inst, _, write = open_demo()
    write("C1:VDIV 2 MV")  # a setting with a unit: M is milli, as in SCPI
    assert inst.get_state("ch1.scale") == pytest.approx(2e-3)


def test_scpi_definitions_reply_as_before():
    """The dialect's defaults are plain SCPI: no header in replies."""
    data = copy.deepcopy(DEMO)
    del data["dialect"], data["format"]
    _, _, query, _ = open_demo(data)
    assert query("C1:VDIV?") == "+5.000000E-02 V"  # a reply template still applies, without {header}
    assert query("C1:TRA?") == "0"


def test_an_empty_value_leaves_one_blank_in_the_template():
    from simubench.api import open_bench

    session = open_bench(
        Path(__file__).resolve().parents[1] / "examples" / "lowfreq-cal" / "lowfreq_cal.bench.yaml"
    )
    try:
        session.write("scope", b"WAVEFORM_SETUP SP,0,NP,10,FP,20000000,SN,0\n")  # beyond the record
        session.write("scope", b'C1:INSPECT? "DATA_ARRAY_1",FLOAT\n')
        assert session.read("scope") == b'C1:INSP " "\n'
    finally:
        session.close()


# ---- settings: ratios ------------------------------------------------------------------------------


def test_a_ratio_is_read_as_written():
    """A duty setting is in the unit its definition declares: 0.001 is 0.1 % of a ratio, never 0.1 % read
    as 10 % or 40 read as 0.4 (#144)."""
    _, inst, query, write = open_demo()
    write("C1:DUTY 0.001")
    assert inst.get_state("ch1.duty") == pytest.approx(0.001)
    write("C1:DUTY 40")
    assert error(query).startswith("-222,")
    assert inst.get_state("ch1.duty") == pytest.approx(0.001)


def test_the_retired_ratio_flag_is_refused_with_a_way_out():
    data = copy.deepcopy(DEMO)
    data["channel_state"]["duty"]["ratio"] = True
    with pytest.raises(ValidationError, match="duty_unit"):
        InstrumentDefinition.model_validate(data)


# ---- several settings in one command ----------------------------------------------------------------


def test_pairs_set_several_settings_and_answer_in_the_same_form():
    _, inst, query, write = open_demo()
    assert query("TRSE?") == "TRSE EDGE,SR,C1,HT,OFF"
    write("TRSE Edge,SR,C2,HV,5E-9,HT,TI")  # HV is accepted and ignored
    assert (inst.get_state("trigger.source"), inst.get_state("trigger.hold")) == ("C2", "TI")
    assert error(query) == '0,"No error"'
    write("TRSE EDGE")  # the pairs are optional
    assert error(query) == '0,"No error"'


def test_pairs_change_nothing_when_one_value_is_wrong():
    _, inst, query, write = open_demo()
    write("TRSE EDGE,SR,C2,HT,XX")
    assert error(query).startswith("-224,")
    assert inst.get_state("trigger.source") == "C1"


@pytest.mark.parametrize(
    ("cmd", "code"),
    [("TRSE", "-109"), ("TRSE GLIT", "-224"), ("TRSE EDGE,SR", "-109"), ("TRSE EDGE,QQ,1", "-224")],
)
def test_pairs_errors(cmd, code):
    _, _, query, write = open_demo()
    write(cmd)
    assert error(query).startswith(code + ",")


def test_reply_templates_name_other_settings():
    _, _, query, write = open_demo()
    write("MSIZ 20K")
    assert (
        query("TBSU?") == "TBSU 1E-3 S,20000"
    )  # no format of its own: integers as the definition writes them


# ---- component parameters from settings -----------------------------------------------------------


def test_parameter_mappings():
    _, inst, _, write = open_demo()
    gen = inst.components["ch1.gen"]
    assert (gen.param("function"), gen.param("symmetry"), gen.param("duty")) == ("SQUare", 100.0, 50.0)
    write("C1:WAVE TRIangle")
    write("C1:DUTY 0.2")
    write("C1:VDIV 0.1")
    assert (gen.param("function"), gen.param("symmetry")) == ("RAMP", 50.0)
    assert gen.param("duty") == pytest.approx(20.0)
    assert gen.param("offset") == pytest.approx(0.1 * 2 + 0.5)
    assert gen.bound_paths()["function"] == "ch1.wave"  # a mapped setting notifies the component


def test_amplitude_units_of_the_function_generator():
    data = copy.deepcopy(DEMO)
    data["channel_components"][0]["params"].update({"amplitude": 1.0, "amplitude_unit": "VRMS"})
    _, inst, _, write = open_demo(data)
    gen = inst.components["ch1.gen"]
    write("C1:WAVE SINe")
    assert gen.vpp("SINusoid") == pytest.approx(2 * math.sqrt(2))
    offset = gen.param("offset")
    assert gen.output("out").peak() == pytest.approx(math.sqrt(2) + offset, rel=1e-6)


# ---- the file format and its static checks ------------------------------------------------------------


def test_a_command_is_a_setting_an_operation_or_bound_parameters():
    with pytest.raises(ValidationError, match="give one of state, op"):
        InstrumentDefinition.model_validate({**DEMO, "commands": [{"header": "X"}]})
    mixed = {
        "header": "X",
        "params": [{"name": "a", "type": "int", "state": "memory.size"}, {"name": "b", "type": "int"}],
    }
    with pytest.raises(ValidationError, match="every parameter"):
        InstrumentDefinition.model_validate({**DEMO, "commands": [mixed]})
    with pytest.raises(ValidationError, match="keys go with type pairs"):
        InstrumentDefinition.model_validate(
            {**DEMO, "commands": [{"header": "X", "params": [{"name": "a", "type": "pairs"}]}]}
        )


def codes(data: dict) -> list[tuple[str, str]]:
    _, report = validate_definition_data(data)
    return [(d.code, d.pointer) for d in report.diagnostics]


def test_the_demo_definition_is_clean():
    assert codes(DEMO) == []


def test_static_checks_of_the_new_fields():
    bad = copy.deepcopy(DEMO)
    trse = bad["commands"][7]
    trse["params"][0]["state"] = "trigger.typ"
    trse["params"][1]["keys"]["SR"] = "trigger.sauce"
    bad["commands"][1]["reply"] = "{header} {ch.scal} V"
    bad["commands"][8]["reply"] = "{header} {value}"
    bad["channel_components"][0]["params"]["duty"]["from"] = "$ch.dutty"
    bad["channel_components"][0]["params"]["function"]["map"]["SINus"] = "SINusoid"
    bad["dialect"]["echo_header"] = "$memory.size"
    assert sorted(codes(bad)) == sorted(
        [
            ("E-DEF-STATE-UNKNOWN", "/channel_components/0/params/duty/from"),
            ("W-DEF-MAP-KEY", "/channel_components/0/params/function/map/SINus"),
            ("E-DEF-REPLY", "/commands/1/reply"),
            ("E-DEF-STATE-UNKNOWN", "/commands/7/params/0/state"),
            ("E-DEF-STATE-UNKNOWN", "/commands/7/params/1/keys/SR"),
            ("E-DEF-REPLY", "/commands/8/reply"),
            ("E-DEF-DIALECT", "/dialect/echo_header"),
        ]
    )


# ---- commands told apart by a parameter, index parameters (issue 48) -------------------------------

AUTHORING = {
    **DEMO,
    "state": {
        **DEMO["state"],
        **{
            f"slot.p{i}.kind": {"type": "enum", "options": ["NULL", "AMPL", "FREQ"], "default": "NULL"}
            for i in (1, 2)
        },
        **{f"slot.p{i}.source": {"type": "enum", "options": ["C1", "C2"], "default": ""} for i in (1, 2)},
    },
    "components": [{"name": "sys", "type": "system"}],
    "commands": [
        *DEMO["commands"],
        {"header": "*CLS", "op": "sys.clear_status"},
        {
            "header": "SLOT",
            "params": [
                {"name": "n", "type": "int", "index": True, "min": 1, "max": 2},
                {"name": "kind", "type": "enum", "state": "slot.p{n}.kind"},
                {"name": "source", "type": "enum", "state": "slot.p{n}.source", "optional": True},
            ],
        },
        {
            "header": "ACTion",
            "when": {"what": "PRESET"},
            "op": "sys.reset",
            "query": False,
            "params": [{"name": "what", "type": "string"}],
        },
        {
            "header": "ACTion",
            "when": {"what": "CLEAR"},
            "op": "sys.clear_status",
            "query": False,
            "params": [{"name": "what", "type": "string"}],
        },
    ],
}


def test_the_authoring_definition_is_clean():
    assert codes(AUTHORING) == []


def test_an_index_picks_the_settings_and_a_query_takes_it():
    _, inst, query, write = open_demo(AUTHORING)
    assert query("SLOT? 1") == "SLOT 1,NULL"  # the source is not set: left out
    write("SLOT 2,AMPL,C2")
    assert query("SLOT? 2") == "SLOT 2,AMPL,C2"
    assert inst.get_state("slot.p2.kind") == "AMPL" and inst.get_state("slot.p1.kind") == "NULL"
    write("SLOT 3,AMPL")
    assert error(query).startswith("-222,")
    write("SLOT 1,VOLT")
    assert error(query).startswith("-224,")


def test_commands_on_one_header_told_apart_by_a_parameter():
    _, inst, query, write = open_demo(AUTHORING)
    write("MSIZ 20K")
    write("ACTION PRESET")
    assert inst.get_state("memory.size") == 10_000  # reset
    write("C9:VDIV 1")  # an error to clear
    write("ACT CLEAR")
    assert error(query) == '0,"No error"'
    write("ACT FOO")
    assert error(query).startswith("-224,")
    assert query("*CLS?") == "0"  # the system component's clear_reply


def test_static_checks_of_index_states_when_and_reply_fields():
    bad = copy.deepcopy(AUTHORING)
    slot = next(c for c in bad["commands"] if c["header"] == "SLOT")
    slot["params"][0]["max"] = 3  # slot.p3 does not exist
    i = bad["commands"].index(slot)
    bad["commands"].append({"header": "*OPC", "op": "sys.reset", "reply": "{unit}"})  # reset gives no {unit}
    actions = [k for k, c in enumerate(bad["commands"]) if c["header"] == "ACTion"]
    bad["commands"][actions[1]]["when"] = {"what": "PRESET"}  # the same value: never runs
    assert sorted(codes(bad)) == sorted(
        [
            ("E-DEF-STATE-UNKNOWN", f"/commands/{i}/params/1/state"),
            ("E-DEF-STATE-UNKNOWN", f"/commands/{i}/params/2/state"),
            ("E-DEF-HEADER-DUPLICATE", f"/commands/{actions[1]}/header"),
            ("E-DEF-REPLY", f"/commands/{len(bad['commands']) - 1}/reply"),
        ]
    )
    with pytest.raises(ValidationError, match="when names"):
        InstrumentDefinition.model_validate(
            {**DEMO, "commands": [{"header": "X", "op": "a.b", "when": {"y": "1"}}]}
        )
