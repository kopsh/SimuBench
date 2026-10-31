"""Channel list parameters ``(@1:3)`` (#183): a command with one expands over the channels —
a write happens once per channel (each channel's own range applies), a query answers the channels
in list order, comma-joined; a stored list (``int_list`` state) reads back as ``(@101:110)``;
an operation with one runs once per channel and joins the replies. Malformed lists are errors."""

from __future__ import annotations

import pytest

from simubench.engine.session import Session
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition
from simubench.scpi.values import ScpiError, format_channel_list, parse_channel_list

# ---- parsing and formatting ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "channels"),
    [
        ("(@1)", [1]),
        ("(@1:3)", [1, 2, 3]),
        ("(@101,102,110)", [101, 102, 110]),
        ("(@101:103,110)", [101, 102, 103, 110]),
        ("(@ 2 , 3 )", [2, 3]),  # spaces around numbers and separators
    ],
)
def test_channel_lists_expand_in_list_order(text, channels):
    assert parse_channel_list(text.encode()) == channels


def test_a_repeated_channel_is_an_error():
    with pytest.raises(ScpiError) as e:
        parse_channel_list(b"(@1,2,1)")
    assert e.value.code == -108


def test_a_descending_range_is_an_error():
    with pytest.raises(ScpiError) as e:
        parse_channel_list(b"(@3:1)")
    assert e.value.code == -222


@pytest.mark.parametrize("text", ["(@)", "(1:3)", "(@1:3", "1:3", "(@a)"])
def test_a_malformed_list_is_an_error(text):
    with pytest.raises(ScpiError) as e:
        parse_channel_list(text.encode())
    assert e.value.code in (-102, -108)


@pytest.mark.parametrize(
    ("channels", "ranges", "text"),
    [
        ([101, 102, 103, 110], True, "(@101:103,110)"),
        ([1, 2, 3], True, "(@1:3)"),
        ([1, 3], True, "(@1,3)"),
        ([2, 1], True, "(@2,1)"),
        ([1, 2, 3], False, "(@1,2,3)"),
        ([], True, "(@)"),
    ],
)
def test_a_stored_list_reads_back_collapsed(channels, ranges, text):
    assert format_channel_list(channels, ranges) == text.encode()


# ---- a definition with channel lists ---------------------------------------------------------------

PSU = {
    "schema": "simubench.instrument/1",
    "id": "psu-demo",
    "manufacturer": "ACME",
    "model": "PSU3",
    "class": "power_supply",
    "channels": {"count": 3, "overrides": {1: {"volt": {"max": 6.0}}}},
    "channel_state": {"volt": {"type": "float", "unit": "V", "min": 0.0, "max": 25.0, "default": 0.0}},
    "state": {"scan.list": {"type": "int_list", "min": 1, "max": 320, "default": []}},
    "components": [{"type": "system", "name": "sys"}],
    "commands": [
        {
            "header": "VOLTage",
            "params": [
                {"name": "level", "type": "float", "unit": "V", "state": "ch.volt"},
                {"name": "channels", "type": "channel_list", "optional": True, "default": [1]},
            ],
        },
        {"header": "ROUTe:SCAN", "state": "scan.list"},
        {
            "header": "ERRor",
            "op": "sys.next_error",
            "set": False,
            "reply": '{code},"{text}"',
            "params": [{"name": "channels", "type": "channel_list", "optional": True, "default": [1]}],
        },
    ],
}


def open_psu():
    definition = InstrumentDefinition.model_validate(PSU)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": "psu-demo"}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)

    def query(cmd: str) -> str:
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    return session, query


def test_a_write_expands_over_the_channels_with_each_channels_own_range():
    session, query = open_psu()
    query("VOLT 5,(@1:3)")
    assert session.instruments["x"].get_state("ch1.volt") == 5.0
    assert session.instruments["x"].get_state("ch3.volt") == 5.0
    # channel 1's own override (6 V max) applies to the same write
    query("VOLT 20,(@1)")
    assert query("SYST:ERR?").startswith("-222")
    assert session.instruments["x"].get_state("ch1.volt") == 5.0
    query("VOLT 20,(@2)")
    assert session.instruments["x"].get_state("ch2.volt") == 20.0


def test_a_query_answers_the_channels_in_list_order():
    session, query = open_psu()
    session.instruments["x"].set_state("ch1.volt", 1.0)
    session.instruments["x"].set_state("ch2.volt", 2.0)
    session.instruments["x"].set_state("ch3.volt", 3.0)
    assert query("VOLT? (@3,1)") == "+3.000000E+00,+1.000000E+00"
    # the list may be left out where the definition says so: it defaults to channel 1
    assert query("VOLT?") == "+1.000000E+00"


def test_a_channel_outside_the_instrument_is_an_error():
    _, query = open_psu()
    query("VOLT 1,(@4)")
    assert query("SYST:ERR?").startswith("-114")
    query("VOLT? (@0)")
    assert query("SYST:ERR?").startswith("-114")


def test_a_stored_list_saves_and_reads_back_as_a_list():
    session, query = open_psu()
    query("ROUT:SCAN (@101:103,110)")
    assert session.instruments["x"].get_state("scan.list") == [101, 102, 103, 110]
    assert query("ROUT:SCAN?") == "(@101:103,110)"
    query("ROUT:SCAN (@5)")
    assert query("ROUT:SCAN?") == "(@5)"
    # the state's own range applies to every channel number in the list
    query("ROUT:SCAN (@400)")
    assert query("SYST:ERR?").startswith("-222")
    assert query("ROUT:SCAN?") == "(@5)"


def test_an_operation_with_a_list_runs_once_per_channel_and_joins():
    _, query = open_psu()
    query("VOLT 99,(@2)")  # queue one error
    assert query("ERROR? (@1:3)") == '-222,"Data out of range",0,"No error",0,"No error"'
    assert query("ERROR?") == '0,"No error"'
