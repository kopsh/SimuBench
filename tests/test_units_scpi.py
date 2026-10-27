import math

import pytest

from simubench.scpi import values as V
from simubench.scpi.errors import ErrorQueue, ScpiError
from simubench.scpi.parse import Pattern, parse_unit, split_units
from simubench.units import UnitError, format_nr3, parse_number


@pytest.mark.parametrize(
    ("text", "unit", "value"),
    [
        ("1.5", "HZ", 1.5),
        ("1.5MHZ", "HZ", 1.5e6),
        ("20mV", "V", 20e-3),
        ("20MV", "V", 20e-3),  # SCPI: M is milli
        ("10KHZ", "HZ", 1e4),
        ("5MS", "S", 5e-3),
        ("2UA", "A", 2e-6),
        ("3MA", "A", 3e-3),
        ("1MOHM", "OHM", 1e6),
        ("-2.5e-3", None, -2.5e-3),
    ],
)
def test_parse_number(text, unit, value):
    assert parse_number(text, unit) == pytest.approx(value)


def test_parse_number_rejects_wrong_suffix():
    with pytest.raises(UnitError):
        parse_number("5HZ", "V")


def test_nr3():
    assert format_nr3(1000.0) == "+1.000000E+03"
    assert format_nr3(-6.103516e-06) == "-6.103516E-06"
    assert format_nr3(math.nan) == "+9.91000E+37"


def test_split_units_respects_strings_and_blocks():
    msg = b':LIST "1;2";:DATA #15a;b,c;*OPC?\n'
    assert split_units(msg) == [b':LIST "1;2"', b":DATA #15a;b,c", b"*OPC?"]


def test_parse_unit_header_and_params():
    u = parse_unit(b":CHAN2:SCAL 0.05")
    assert u.absolute and not u.query
    assert [(t.keyword, t.suffix) for t in u.tokens] == [("CHAN", 2), ("SCAL", None)]
    assert u.params == (b"0.05",)
    q = parse_unit(b"*IDN?")
    assert q.common and q.query


@pytest.mark.parametrize(
    ("header", "caps"),
    [
        (":SOUR2:FREQ", {"ch": 2}),
        (":FREQ", {"ch": None}),
        (":SOURCE1:FREQUENCY:CW", {"ch": 1}),
        (":frequency", {"ch": None}),
    ],
)
def test_pattern_optional_nodes_and_suffix(header, caps):
    p = Pattern.compile("[:SOURce<ch>]:FREQuency[:CW]")
    assert p.match(parse_unit(header.encode()).tokens) == caps


def test_pattern_rejects_partial_keyword():
    p = Pattern.compile(":TIMebase:SCALe")
    assert p.match(parse_unit(b":TIMEB:SCAL").tokens) is None
    assert p.match(parse_unit(b":TIM:SCAL").tokens) == {}


def test_values():
    assert V.parse_bool(b"ON") is True and V.parse_bool(b"0") is False
    assert V.parse_enum(b"sin", ["SINusoid", "SQUare"]) == "SINusoid"
    assert V.parse_float_list([b'"1e9,2.4e9"']) == [1e9, 2.4e9]
    assert V.parse_float_list([b"1e9", b"2.4e9"]) == [1e9, 2.4e9]  # unquoted lists accepted (D13)
    assert V.format_block(b"ab") == b"#12ab"
    assert V.parse_block(b"#12ab") == b"ab"
    with pytest.raises(ScpiError) as e:
        V.parse_float(b"abc")
    assert e.value.code == -104


def test_error_queue_overflow():
    q = ErrorQueue(capacity=3)
    for _ in range(5):
        q.push(ScpiError(-222))
    assert [q.pop().code for _ in range(4)] == [-222, -222, -350, 0]


def test_error_family_message_for_unlisted_codes():
    from simubench.scpi.errors import ScpiError

    assert ScpiError(-319).message == "System error"
    assert ScpiError(-229).message == "Parameter error"
    assert ScpiError(-313).message == "Calibration memory lost"
