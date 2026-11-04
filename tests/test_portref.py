"""The port-reference grammar (issue 593): one split, one strict parse, everywhere."""

from __future__ import annotations

import pytest

from simubench.engine.portref import PortRef, parse, split

pytestmark = pytest.mark.unit


def test_split_takes_the_last_segment_as_the_port():
    assert split("awg.OUT1") == PortRef("awg", "OUT1")
    assert str(split("awg.OUT1")) == "awg.OUT1"


def test_split_owner_may_itself_be_dotted():
    assert split("ch1.mux.out") == PortRef("ch1.mux", "out")


def test_split_keeps_the_halves_of_a_malformed_reference():
    assert split("noport") == PortRef("", "noport")
    assert split(".out") == PortRef("", "out")
    assert split("a.") == PortRef("a", "")
    assert split("") == PortRef("", "")


def test_parse_requires_both_halves():
    assert parse("awg.OUT1") == PortRef("awg", "OUT1")
    for bad in ("noport", ".out", "a.", ""):
        assert parse(bad) is None
