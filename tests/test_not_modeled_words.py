"""Numeric keywords and words a model does not implement (#360).

INFinity and NINFinity are the long forms of INF and NINF: both forms parse alike, so the same word
gets the same error code. A definition lists the words the real instrument takes but the model does
not implement in ``not_modeled``; writing one (short or long form) is refused with -224 and the
error event says it is not modeled, with the definition's reason.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from simubench.api import open_bench
from simubench.scpi import values as V

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("word", "value"),
    [("INF", math.inf), ("INFinity", math.inf), ("NINF", -math.inf), ("ninfinity", -math.inf)],
)
def test_the_short_and_long_forms_of_infinity_parse_alike(word, value):
    assert V.parse_float(word.encode()) == value


def test_matches_word_takes_the_short_and_the_long_form():
    assert V.matches_word("inf", "INFinity") and V.matches_word("INFINITY", "INFinity")
    assert not V.matches_word("INFI", "INFinity")


def _errors(session, instrument: str, command: str) -> list[dict]:
    before = len(session.history)
    session.write(instrument, (command + "\n").encode())
    return [
        e for i in range(before, len(session.history)) if (e := session.history[i]).get("kind") == "error"
    ]


@pytest.mark.parametrize(
    ("bench", "instrument", "header"),
    [
        ("keysight_34970a.bench.yaml", "daq", "TRIG:COUN"),
        ("keysight-34465a.bench.yaml", "dut", "TRIG:COUN"),
    ],
)
def test_a_not_modeled_word_is_one_error_in_both_forms(bench, instrument, header):
    s = open_bench(ROOT / "examples" / "benches" / bench, idle="off")
    try:
        for word in ("INF", "INFinity", "infinity"):
            [err] = _errors(s, instrument, f"{header} {word}")
            assert err["code"] == -224 and err["reason"] == "not_modeled"
            assert "not modeled" in err["message"]
        # A number outside the range is still a range error, and the setting is unchanged.
        [err] = _errors(s, instrument, f"{header} 2e9")
        assert err["code"] == -222
    finally:
        s.close()


def test_without_a_declaration_infinity_is_a_number_out_of_range():
    """A bounded setting that does not list INFinity gets the number: -222 for both forms (before
    #360 the long form was a -104 data type error)."""
    s = open_bench(ROOT / "examples" / "benches" / "keysight_34970a.bench.yaml", idle="off")
    try:
        for word in ("INF", "INFinity"):
            [err] = _errors(s, "daq", f"SENS:VOLT:DC:NPLC {word},(@101)")
            assert err["code"] == -222
    finally:
        s.close()


def test_quantities_units_honor_not_modeled():
    """#557: ``not_modeled`` covers quantities units — an unmodeled unit is refused with -224
    before the command's unknown-unit code, a truly unknown unit keeps that code, and a modeled
    unit is unaffected (the 5730A's OUT takes V/A/OHM/HZ since #550, declares DBM unmodeled)."""
    s = open_bench(ROOT / "examples" / "benches" / "fluke-5730a.bench.yaml", idle="off")
    try:

        def q(cmd):
            s.write("cal", (cmd + "\n").encode())
            out = s.read("cal")
            return out.decode().strip() if out else ""

        s.write("cal", b"OUT 1 DBM\n")
        s.read("cal")
        assert q("FAULT?") == "-224"  # DBM: declared not modeled
        s.write("cal", b"OUT 1 XYZ\n")
        s.read("cal")
        assert q("FAULT?") == "2206"  # truly unknown: the command's own code (plain format, no sign)
        q("OUT 1 V")
        assert q("FAULT?") == "0"  # a modeled unit is unaffected
    finally:
        s.close()
