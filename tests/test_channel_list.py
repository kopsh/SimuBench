"""Channel list parameters ``(@1:3)`` (#183): a command with one expands over the channels —
a write happens once per channel (each channel's own range applies), a query answers the channels
in list order, comma-joined; a stored list (``int_list`` state) reads back as ``(@101:110)``;
an operation with one runs once per channel and joins the replies. Malformed lists are errors.
A definition's ``channels.numbering`` (#207) says which numbers the bus uses: a 3-slot DAQ's
101–120, 201–220, … instead of 1..count."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

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


def test_a_declared_descending_range_and_repeat_expand_in_written_order():
    """#260: the B2900 manual lists (@2:1); a definition may declare it (and repeats)."""
    assert parse_channel_list(b"(@3:1)", descending=True) == [3, 2, 1]
    assert parse_channel_list(b"(@1,2,1)", repeated=True) == [1, 2, 1]
    with pytest.raises(ScpiError) as e:
        parse_channel_list(b"(@1,2,1)", descending=True)  # repeats stay an error unless declared
    assert e.value.code == -108


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


def open_definition(data: dict) -> tuple[Session, Callable[[str], str]]:
    definition = InstrumentDefinition.model_validate(data)
    bench = BenchDefinition.model_validate(
        {"schema": "simubench.bench/1", "name": "d", "instruments": {"x": {"model": definition.id}}}
    )
    session = Session(bench, lambda _id: definition, clock="stepped", seed=1)

    def query(cmd: str) -> str:
        session.write("x", cmd.encode() + b"\n")
        out = session.read("x")
        return out.decode().strip() if out is not None else ""

    return session, query


def open_psu():
    return open_definition(PSU)


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


def test_a_definition_declares_descending_ranges_for_writes_and_queries():
    """#260: with channels.lists.descending, (@2:1) writes and answers channel 2 then 1; the
    default definition still refuses it (-222)."""
    import copy

    data = copy.deepcopy(PSU)
    data["channels"]["lists"] = {"descending": True, "repeated": True}
    session, query = open_definition(data)
    query("VOLT 2,(@3:2)")
    assert session.instruments["x"].get_state("ch2.volt") == 2.0
    assert session.instruments["x"].get_state("ch3.volt") == 2.0
    session.instruments["x"].set_state("ch1.volt", 1.0)
    assert query("VOLT? (@2:1)") == "+2.000000E+00,+1.000000E+00"
    assert query("VOLT? (@1,2,1)") == "+1.000000E+00,+2.000000E+00,+1.000000E+00"
    assert query("SYST:ERR?") == '0,"No error"'

    _, query = open_psu()
    assert query("VOLT? (@2:1)") == ""
    assert query("SYST:ERR?").startswith("-222")


def test_a_query_form_returns_each_channels_limit_or_default():
    """``VOLT? MAX,(@2:3)`` answers the range limit per channel, not the setting (#297)."""
    session, query = open_psu()
    session.instruments["x"].set_state("ch1.volt", 6.0)
    # the list may be left out: channel 1 answers with its own override's max
    assert query("VOLT? MAX") == "+6.000000E+00"
    assert query("VOLT? MIN") == "+0.000000E+00"
    assert query("VOLT? MAX,(@2:3)") == "+2.500000E+01,+2.500000E+01"
    assert query("VOLT? DEF,(@1)") == "+0.000000E+00"
    # the long forms work too, and the plain query still answers the current setting
    assert query("VOLT? MAXimum,(@2)") == "+2.500000E+01"
    assert query("VOLT? (@1)") == "+6.000000E+00"


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


# ---- a definition whose channels are numbered 101, 102, 201, 202 (#207) ---------------------------

DAQ = {
    "schema": "simubench.instrument/1",
    "id": "daq-demo",
    "manufacturer": "ACME",
    "model": "DAQ4",
    "class": "daq",
    "channels": {
        "count": 4,
        "numbering": [{"first": 101, "count": 2}, {"first": 201, "count": 2}],
    },
    "channel_state": {
        "volt": {"type": "float", "unit": "V", "min": 0.0, "max": 25.0, "default": 0.0},
        "on": {"type": "bool", "default": False},
    },
    "state": {"scan.list": {"type": "int_list", "min": 1, "max": 320, "default": []}},
    "components": [{"type": "system", "name": "sys"}],
    "commands": [
        {
            "header": "VOLTage",
            "params": [
                {"name": "level", "type": "float", "unit": "V", "state": "ch.volt"},
                {"name": "channels", "type": "channel_list", "optional": True, "default": [101]},
            ],
        },
        {"header": "OUTP<ch>", "state": "ch.on"},
        {"header": "ROUTe:SCAN", "state": "scan.list"},
    ],
}


def test_a_write_and_a_query_use_the_declared_numbers():
    session, query = open_definition(DAQ)
    query("VOLT 5,(@101:102)")
    assert session.instruments["x"].get_state("ch1.volt") == 5.0
    assert session.instruments["x"].get_state("ch2.volt") == 5.0
    query("VOLT 7,(@201)")
    assert session.instruments["x"].get_state("ch3.volt") == 7.0
    # the list is answered in list order; the state keeps the channel indexes
    assert query("VOLT? (@201,101)") == "+7.000000E+00,+5.000000E+00"


def test_a_default_channel_list_uses_the_declared_numbers():
    session, query = open_definition(DAQ)
    query("VOLT 5")
    assert session.instruments["x"].get_state("ch1.volt") == 5.0
    assert query("VOLT?") == "+5.000000E+00"


def test_a_range_may_span_slots_and_skips_numbers_the_numbering_does_not_declare():
    _, query = open_definition(DAQ)
    for number, level in ((101, 1.0), (102, 2.0), (201, 3.0), (202, 4.0)):
        query(f"VOLT {level},(@{number})")
    # 121-199 are no channel numbers: the range keeps only the declared channels, as the 34970A
    assert query("VOLT? (@101:202)") == "+1.000000E+00,+2.000000E+00,+3.000000E+00,+4.000000E+00"
    # both ends of a range must be declared channels
    query("VOLT 1,(@101:205)")
    assert query("SYST:ERR?").startswith("-114")
    query("VOLT 1,(@121:130)")
    assert query("SYST:ERR?").startswith("-114")


def test_a_number_the_numbering_does_not_declare_is_an_error():
    _, query = open_definition(DAQ)
    query("VOLT 1,(@103)")
    assert query("SYST:ERR?").startswith("-114")
    query("VOLT 1,(@1)")  # a channel index, not a bus number
    assert query("SYST:ERR?").startswith("-114")
    query("VOLT? (@3:2)")
    assert query("SYST:ERR?").startswith("-222")


def test_a_suffix_uses_the_declared_number():
    session, query = open_definition(DAQ)
    query("OUTP101 ON")
    assert session.instruments["x"].get_state("ch1.on") is True
    query("OUTP1 ON")  # a channel index, not a bus number
    assert query("SYST:ERR?").startswith("-114")


def test_a_stored_list_uses_the_declared_numbers():
    session, query = open_definition(DAQ)
    query("ROUT:SCAN (@101:102,201)")
    assert session.instruments["x"].get_state("scan.list") == [101, 102, 201]
    assert query("ROUT:SCAN?") == "(@101:102,201)"
    # the gaps of a spanning range are not stored
    query("ROUT:SCAN (@101:202)")
    assert session.instruments["x"].get_state("scan.list") == [101, 102, 201, 202]
    assert query("ROUT:SCAN?") == "(@101:102,201:202)"
    query("ROUT:SCAN (@103)")
    assert query("SYST:ERR?").startswith("-114")
    assert query("ROUT:SCAN?") == "(@101:102,201:202)"


# ---- the numbering declaration itself -------------------------------------------------------------


def test_numbering_must_cover_every_channel():
    data = dict(DAQ, channels={"count": 4, "numbering": [{"first": 101, "count": 2}]})
    with pytest.raises(ValidationError, match=r"covers 2 channels but channels\.count is 4"):
        InstrumentDefinition.model_validate(data)


def test_numbering_must_not_repeat_a_channel_number():
    data = dict(
        DAQ,
        channels={"count": 4, "numbering": [{"first": 101, "count": 2}, {"first": 102, "count": 2}]},
    )
    with pytest.raises(ValidationError, match="repeats channel numbers"):
        InstrumentDefinition.model_validate(data)


# ---- a channel list with the command's constants and a whole-list setting (#300) -------------------

DAQ_CONF = {
    "schema": "simubench.instrument/1",
    "id": "daq-conf",
    "manufacturer": "ACME",
    "model": "DAQ",
    "class": "data_acquisition",
    "channels": {"count": 2, "numbering": [{"first": 101, "count": 2}]},
    "state": {
        "sense.function": {"type": "enum", "options": ["DC", "AC"], "default": "DC"},
        "sense.range": {"type": "float", "min": 0, "max": 1000, "default": 0.0},
        "sense.range_auto": {"type": "bool", "default": True},
        "scan.list": {"type": "int_list", "default": []},
    },
    "components": [{"type": "system", "name": "sys"}],
    "commands": [
        {
            "header": ":CONFigure[:VOLTage]:AC",
            "query": False,
            "writes": {"sense.function": "AC"},
            "params": [
                {
                    "name": "range",
                    "type": "float",
                    "state": "sense.range",
                    "optional": True,
                    "sets": {"sense.range_auto": False},
                },
                {"name": "channels", "type": "channel_list", "state": "scan.list"},
            ],
        },
        {"header": "ROUTe:SCAN", "state": "scan.list"},
        {"header": "FUNCtion", "state": "sense.function"},
    ],
}


def test_a_list_command_writes_its_constants_sets_and_the_whole_list():
    """The issue's reproduction: CONF:VOLT:AC 10,(@101) selects AC, sets the range and leaves
    autorange, and writes the list into the scan list; the range may be left out."""
    session, query = open_definition(DAQ_CONF)
    inst = session.instruments["x"]
    query("CONF:VOLT:AC 10,(@101)")
    assert inst.get_state("sense.function") == "AC"
    assert inst.get_state("sense.range") == 10.0 and inst.get_state("sense.range_auto") is False
    assert query("ROUT:SCAN?") == "(@101)"
    inst.set_state("sense.function", "DC")
    query("CONF:VOLT:AC (@101:102)")  # the optional range left out: the list is not taken for it
    assert inst.get_state("sense.function") == "AC"
    assert query("ROUT:SCAN?") == "(@101:102)"
    assert query("SYST:ERR?") == '0,"No error"'


def test_a_declared_empty_list_clears_a_stored_list():
    """#418: the DAQ970A clears its scan list with ROUT:SCAN (@); a definition declares it."""
    import copy

    assert parse_channel_list(b"(@)", empty=True) == []
    data = copy.deepcopy(PSU)
    data["channels"]["lists"] = {"empty": True}
    session, query = open_definition(data)
    query("ROUT:SCAN (@101:103)")
    query("ROUT:SCAN (@)")
    assert session.instruments["x"].get_state("scan.list") == []
    assert query("ROUT:SCAN?") == "(@)"
    assert query("SYST:ERR?") == '0,"No error"'

    _, query = open_psu()  # the default still refuses it
    query("ROUT:SCAN (@)")
    assert query("SYST:ERR?").startswith("-108")


def _psu_with_empty_lists():
    import copy

    data = copy.deepcopy(PSU)
    data["channels"]["lists"] = {"empty": True}
    data["commands"].append(
        {
            "header": "MEASure:VOLTage",
            "op": "sys.next_error",
            "set": False,
            "params": [{"name": "channels", "type": "channel_list"}],
        }
    )
    return open_definition(data)


def test_an_empty_list_on_a_per_channel_write_is_refused_and_changes_nothing():
    """#420: `empty` admits (@) only where a list setting is written whole; VOLT 5,(@) is -108
    and leaves every channel as it was."""
    session, query = _psu_with_empty_lists()
    query("VOLT 2,(@1:3)")
    query("VOLT 5,(@)")
    assert query("SYST:ERR?").startswith("-108")
    inst = session.instruments["x"]
    assert [inst.get_state(f"ch{n}.volt") for n in (1, 2, 3)] == [2.0, 2.0, 2.0]


def test_an_empty_list_on_a_query_or_an_operation_is_an_error_not_silence():
    """#420: VOLT? (@) and MEAS? (@) answer -108 — a driver must not wait for a reply that never
    comes — while ROUT:SCAN (@) still clears the stored list."""
    _, query = _psu_with_empty_lists()
    assert query("VOLT? (@)") == ""
    assert query("SYST:ERR?").startswith("-108")
    assert query("MEAS:VOLT? (@)") == ""
    assert query("SYST:ERR?").startswith("-108")
    query("ROUT:SCAN (@1:2)")
    query("ROUT:SCAN (@)")
    assert query("ROUT:SCAN?") == "(@)"
    assert query("SYST:ERR?") == '0,"No error"'
