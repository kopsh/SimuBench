"""Case expectations judge a reply by its meaning, never by a regular expression (D49, #926):
numbers by value ± tolerance, text by equality or its start, lists by fields, blocks by their
parts; ``format`` checks how a value is written only where the manual fixes it."""

from __future__ import annotations

import pytest

from helpers import raises_with
from simubench.library.coverage import Step, _check
from simubench.library.expectations import Block, Fields, FieldSpec, matches_format

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "fmt", "ok"),
    [
        ("+1.00000000E+01", "±d.ddddddddE±dd", True),
        ("1.00000000E+01", "±d.ddddddddE±dd", False),  # the sign is required
        ("+1.0000000E+01", "±d.ddddddddE±dd", False),  # one digit short
        ("-1.234e-05", "~d.ddde±dd", True),
        ("1.234e-05", "~d.ddde±dd", True),  # the sign may be left out
        ("2024,01,31", "dddd,dd,dd", True),
        ("2024,1,31", "dddd,dd,dd", False),
        ("+1.23456E-07", "+d.dddddE-0d", True),  # other characters, digits included, are themselves
        ("+1.23456E-17", "+d.dddddE-0d", False),
        ("42", "unsigned", True),
        ("-42", "unsigned", False),
        ("-42", "integer", True),
        ("-12.50", "nr2", True),
        ("12", "nr2", False),
        ("+9.91E+37", "nr3", True),
        ("-1.5e-3", "nr3", True),
        ("1.5", "nr3", False),
        ("1e5", "number", True),
        ("abc", "number", False),
    ],
)
def test_a_format_says_how_a_value_is_written(text, fmt, ok):
    assert matches_format(text, fmt) is ok


def test_a_field_is_judged_by_value_within_its_tolerance():
    spec = FieldSpec(value=1.0e-3, tolerance=5.0e-8)
    assert spec.judge("+1.000049E-03", 0.0) and spec.judge("+9.999501E-04", 0.0)
    assert not spec.judge("+1.000051E-03", 0.0)
    assert not spec.judge("+5.000000E-01", 0.0)  # #926: a wrong magnitude fails however written


def test_a_field_reads_its_number_between_prefix_and_unit():
    spec = FieldSpec(prefix='"VOLT:AC ', value=10.0)
    assert spec.judge('"VOLT:AC +1.00000000E+01', 0.0) and not spec.judge('"VOLT +1.0E+01', 0.0)
    level = FieldSpec(value=2.05, tolerance=0.05, unit="V")
    assert (
        level.judge("2.06E+0 V", 0.0) and not level.judge("2.06E+0 HZ", 0.0) and not level.judge("2.2 V", 0.0)
    )


def test_a_field_range_and_text_checks():
    assert FieldSpec(min=-1000.0, max=-10.0).judge("-5.0E+01", 0.0)
    assert not FieldSpec(min=-1000.0, max=-10.0).judge("-5.0E+00", 0.0)
    assert FieldSpec(one_of=["2048", "2080"]).judge("2080", 0.0) and not FieldSpec(one_of=["2048"]).judge(
        "2080", 0.0
    )
    assert FieldSpec(length=14).judge("SDS2PAAX123456", 0.0) and not FieldSpec(length=14).judge("X", 0.0)
    assert FieldSpec(nonempty=True).judge("x", 0.0) and not FieldSpec(nonempty=True).judge("", 0.0)
    assert FieldSpec(suffix='"', nonempty=True).judge('3.0E-06"', 0.0) and not FieldSpec(suffix='"').judge(
        "3", 0.0
    )


def test_fields_split_a_reply_and_count_repeats():
    fields = Fields(fields=["+1.0E+000", FieldSpec(value=1e-3, tolerance=1e-9, count=2)])
    assert fields.judge("+1.0E+000,+1.0000000001E-003,+9.9999999995E-004", 0.0)
    assert not fields.judge("+1.0E+000,+1.0E-003", 0.0)  # one field short
    assert not fields.judge("+1.0E+000,+1.0E-003,+1.0E-003,+1.0E-003", 0.0)  # one too many


def test_fields_with_their_own_separator_and_frame():
    fields = Fields(
        prefix='C1:INSP " ', suffix=' "', separator=" ", fields=[FieldSpec(format="nr3", count=3)]
    )
    assert fields.judge('C1:INSP " 1.5e-03 -2.0e-03 4.0e+00 "', 0.0)
    assert not fields.judge('C1:INSP " 1.5e-03 -2.0e-03 "', 0.0)
    assert not fields.judge('C1:INSP "1.5e-03 -2.0e-03 4.0e+00"', 0.0)


def test_a_definite_block_by_header_length_and_terminator():
    block = Block(header="#41000", bytes=1000, end="\n")
    assert block.judge("#41000" + "x" * 1000 + "\n", 0.0)
    assert not block.judge("#41000" + "x" * 1000, 0.0)  # no terminator
    assert not block.judge("#3999" + "x" * 999 + "\n", 0.0)  # another header and length


def test_an_indefinite_block_tries_each_terminator():
    block = Block(indefinite=True, bytes=4)
    assert block.judge("#0abcd\n", 0.0) and block.judge("#0abc\n", 0.0)  # the data may end in a newline
    assert not block.judge("#0abcdef\n", 0.0)


def test_a_block_of_ascii_numbers_is_judged_by_fields():
    block = Block(
        header="#231",
        end="",
        fields=[FieldSpec(value=0.909095, tolerance=5e-6), FieldSpec(value=1.81815, tolerance=5e-5)],
    )
    assert block.judge("#231+9.09090909E-01,+1.81818182E+00", 0.0)
    assert not block.judge("#231+9.09090909E-01,+1.91818182E+00", 0.0)


def test_expect_values_take_one_tolerance_per_value_and_a_format():
    step = Step(
        query=":FETC:ARR:VOLT?",
        expect_values=[0.0, 0.5, 1.0],
        tolerance=[1e-5, 1e-4, 1e-4],
        format="±d.ddddddE±dd",
    )
    assert _check(step, "+1.234567E-07,+5.000012E-01,+9.999950E-01")[1]
    assert not _check(step, "+1.234567E-07,+5.002000E-01,+9.999950E-01")[1]  # outside its band
    assert not _check(step, "1.234567E-07,+5.000012E-01,+9.999950E-01")[1]  # written without its sign


def test_a_prefix_and_a_format_expectation():
    assert _check(
        Step(query=":SYST:ERR?", expect_prefix='-222,"Data out of range'), '-222,"Data out of range;x"'
    )[1]
    assert not _check(Step(query=":SYST:ERR?", expect_prefix="-222,"), '-224,"Illegal"')[1]
    assert _check(Step(query=":SYST:DATE?", expect_format="dddd,dd,dd"), "2024,01,31")[1]


def test_a_regex_expectation_is_refused_with_its_replacements():
    with raises_with(ValueError, "expect_match", "no longer accepted", "expect_prefix", "expect_fields"):
        Step.model_validate({"query": "*IDN?", "expect_match": "ACME,.*"})


def test_a_list_of_tolerances_must_match_the_values():
    with raises_with(ValueError, "one per value"):
        Step(query="X?", expect_values=[1.0, 2.0], tolerance=[0.1])


def test_one_expectation_per_query():
    with raises_with(ValueError, "give one of"):
        Step(query="X?", expect="1", expect_prefix="1")
