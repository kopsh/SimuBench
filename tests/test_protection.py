"""The protection latch primitive (#631): declared thresholds, the latch and its kind, the clear
semantics, the status flags and the trip/kind replies — tested directly on a minimal carrier.

The carriers only add their increments (the supply's OCP delay and output-state restore, the
load's open input, the calibrator's standby-and-error); the latch itself lives once in
``library/components/protection.py`` and these tests keep its semantics.
"""

from __future__ import annotations

from typing import Any

import pytest

from simubench.library.components.protection import ProtectionLatch, TripThreshold

pytestmark = pytest.mark.unit


class FakeLatch(ProtectionLatch):
    """A minimal carrier: OVP / OCP / OPP declared, the hooks counted, a stub session."""

    thresholds = (
        TripThreshold("OVP", "ovp", "v", flag="OV"),
        TripThreshold("OCP", "ocp", "i"),
        TripThreshold("OPP", "opp", "p"),
    )

    def __init__(self, session: Any = None, **thresholds: float) -> None:
        self._init_latch()
        self.session = session
        self._thresholds = thresholds
        self.trips = 0  # the trip action ran
        self.clears = 0  # the clear action ran

    def param(self, key: str) -> Any:
        return self._thresholds.get(key, 0.0)

    def _on_trip(self) -> None:
        self.trips += 1

    def _on_clear(self) -> None:
        self.clears += 1


def test_a_crossing_latches_the_first_declared_kind():
    latch = FakeLatch(ovp=4.0, ocp=1.5)
    assert latch.check(5.0, 0.1)  # the caller learns of the crossing, the latch does not hold yet
    assert not latch.tripped()
    latch.trip(5.0, 0.1)
    assert latch.tripped() and latch.kind() == "OVP"
    assert latch.trips == 1  # the carrier's trip action ran once


def test_the_gate_holds_at_and_below_the_thresholds():
    latch = FakeLatch(ovp=4.0, ocp=1.5)
    assert not latch.check(4.0, 1.5)  # at the threshold is not past it
    off = FakeLatch(ocp=1.5)
    assert not off.check(-8.0, 0.1)  # a zero threshold is off: an 8 V terminal crosses nothing


def test_ocp_judges_the_current_and_opp_the_power():
    latch = FakeLatch(ocp=1.5)
    latch.trip(0.0, -2.0)  # a magnitude: a sink at -2 A trips a 1.5 A OCP
    assert latch.kind() == "OCP"
    latch.clear()
    power = FakeLatch(opp=5.0)
    power.trip(2.0, 3.0)  # 6 W past the 5 W OPP (OVP and OCP off)
    assert power.kind() == "OPP"


def test_a_trip_without_a_declared_crossing_latches_prot():
    latch = FakeLatch(ovp=4.0, ocp=1.5)
    latch.trip(1.0, 0.1)  # the carrier judged a fault beyond the declared thresholds
    assert latch.kind() == "PROT" and latch.trips == 1


def test_the_latch_holds_until_cleared():
    latch = FakeLatch(ovp=4.0)
    latch.trip(5.0, 0.0)
    assert not latch.check(9.0, 0.0)  # a crossing judged while the latch holds trips nothing
    latch.trip(9.0, 0.0)
    assert latch.kind() == "OVP" and latch.trips == 1


def test_clear_removes_the_latch_and_runs_the_clear_action():
    latch = FakeLatch(ovp=4.0)
    latch.trip(5.0, 0.0)
    latch.clear()
    assert not latch.tripped() and latch.kind() == ""
    assert latch.clears == 1
    assert latch.check(5.0, 0.0)  # the protection judges again


def test_clear_of_another_kind_leaves_the_latch():
    latch = FakeLatch(ovp=4.0)
    latch.trip(5.0, 0.0)
    latch.clear("OCP")  # CURR:PROT:CLE clears OCP, not the standing OVP
    assert latch.tripped() and latch.clears == 0
    latch.clear("ovp")  # the kind matches in any case
    assert not latch.tripped() and latch.clears == 1


def test_latching_a_named_fault_runs_no_trip_action():
    latch = FakeLatch(ocp=1.5)
    latch.latch("OCP")  # a fault the carrier judged itself (an OCP timer, an overload)
    assert latch.tripped() and latch.kind() == "OCP" and latch.trips == 0
    latch.latch("OVP")  # already latched: the first fault stands
    assert latch.kind() == "OCP"
    latch.clear()
    assert latch.clears == 1


def test_the_flags_show_the_latched_kind():
    latch = FakeLatch(ovp=4.0, ocp=1.5)
    assert latch.status_flags() == {"OV": False}  # only the thresholds that name a flag
    latch.latch("OVP")
    assert latch.status_flags() == {"OV": True}


def test_a_carrier_without_flag_declarations_reports_none():
    class Quiet(FakeLatch):
        thresholds = ()

    assert Quiet().status_flags() == {}


def test_the_trip_and_kind_replies_solve_first_and_format():
    class Session:
        def __init__(self) -> None:
            self.solves = 0

        def solve(self) -> None:
            self.solves += 1

    session = Session()
    latch = FakeLatch(session, ovp=4.0)
    assert latch._trip_reply() == b"0" and session.solves == 1
    latch.trip(5.0, 0.0)
    assert latch._trip_reply() == b"1" and session.solves == 2
    assert latch._trip_reply("OVP") == b"1" and latch._trip_reply("OCP") == b"0"
    assert latch._kind_reply() == b'"OVP"' and session.solves == 5
