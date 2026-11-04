"""The port-reference grammar (issue 593): one split, one strict parse, one hint ladder (issue 598)."""

from __future__ import annotations

import pytest

from simubench.engine.portref import PortRef, closest, ladder, parse, split

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


def test_closest_suggests_the_nearest_known_name():
    assert closest("scop", ["awg", "scope", "rc"]) == "scope"
    assert closest("scope", ["awg", "scope", "rc"]) == "scope"  # an exact member is itself
    assert closest("zzz", ["awg", "scope", "rc"]) is None  # nothing close enough


def test_ladder_suggests_a_name_for_an_unknown_owner():
    ports_of = {"awg": ["IN", "OUT"]}.get
    assert ladder("awgg.OUT1", ["awg", "rc"], ports_of) == ("awg", None)


def test_ladder_suggests_a_port_for_an_unknown_port():
    ports_of = {"awg": ["IN", "OUT"]}.get
    assert ladder("awg.INX", ["awg", "rc"], ports_of) == ("awg", "IN")


def test_ladder_hints_are_none_when_nothing_is_close():
    ports_of = {"awg": ["IN", "OUT"]}.get
    assert ladder("scop.CH1", ["awg", "rc"], ports_of) == (None, None)
    assert ladder("noport", ["awg", "rc"], ports_of) == (None, None)  # an owner-less ref matches whole
