"""The electronic-load blocks' model options (#661): the Von latch, separate rise/fall slew,
declared ranges with the setpoint brought into a new range, OCP/OPP switches and trip delays in
virtual time, and the protection/input status flags. Defaults keep the DL3021 / sb-el-1 behaviour
(their cases and tests still pass); the B&K 8614 definition declares the options (#337)."""

from __future__ import annotations

from typing import Any

import pytest

from simubench.library.components.eload import EloadModeLoop, EloadPowerStage, EloadProtection

pytestmark = pytest.mark.unit


class FakeOwner:
    """Settings, a virtual clock and the pending-operation table, as a component sees them."""

    name = "load"

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.t_ns = 0
        self.pending: dict[str, int] = {}
        self.writes: list[tuple[str, Any]] = []

    def now_ns(self) -> int:
        return self.t_ns

    def set_state(self, path: str, value: Any) -> None:
        self.writes.append((path, value))
        self.state[path] = value

    def register_pending(self, key: str, done_ns: int, on_done: Any = None, *, waited: bool = True) -> None:
        self.pending[key] = done_ns

    def cancel_pending(self, key: str) -> None:
        self.pending.pop(key, None)


class FakeSession:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate(self, electrical: bool = False) -> None:
        self.invalidations += 1


def _loop(stage: EloadPowerStage | None = None, **params: Any) -> EloadModeLoop:
    owner = FakeOwner()
    loop = EloadModeLoop("loop", {"stage": "stage", **params}, owner, FakeSession())  # type: ignore[arg-type]
    stage = stage or EloadPowerStage("stage", {"r_min": 0.01, "i_max": 240.0, "p_max": 0.0}, None, None)  # type: ignore[arg-type]
    loop.ref = lambda key: stage  # type: ignore[method-assign]
    return loop


def _protection(**params: Any) -> EloadProtection:
    return EloadProtection("prot", params, FakeOwner(), FakeSession())  # type: ignore[arg-type]


def test_von_without_latch_stops_below_von():
    loop = _loop(function="CC", current=5.0, von=10.0)
    assert loop._decide(12.0, 0.0) == ("i", 5.0)
    assert loop._decide(8.0, 0.0) == ("open", 0.0)


def test_von_latch_keeps_sinking_below_von_until_the_input_turns_off():
    loop = _loop(function="CC", current=5.0, von=10.0, von_latch=True)
    assert loop._decide(8.0, 0.0) == ("open", 0.0)  # never above Von yet
    assert loop._decide(12.0, 0.0) == ("i", 5.0)
    assert loop._decide(8.0, 0.0) == ("i", 5.0)  # latched
    loop.open()  # input off
    assert loop._decide(8.0, 0.0) == ("open", 0.0)


def test_rise_and_fall_slew_apply_to_their_own_direction():
    loop = _loop(function="CC", current=0.0, slew=1.0, slew_rise=0.5, slew_fall=2.0)
    owner = loop.owner
    loop.mode = "i"  # conducting: a step ramps from the level reached
    loop._params["current"] = 10.0
    loop.on_state("current", "current.level", 0.0, 10.0)
    owner.t_ns = 4_000  # 4 µs
    assert loop._level() == pytest.approx(2.0)  # 0.5 A/µs up
    owner.t_ns = 20_000
    assert loop._level() == pytest.approx(10.0)
    loop._params["current"] = 0.0
    loop.on_state("current", "current.level", 10.0, 0.0)
    owner.t_ns = 22_000  # 2 µs later
    assert loop._level() == pytest.approx(6.0)  # 2 A/µs down


def test_a_range_write_selects_the_smallest_range_that_holds_it():
    loop = _loop()
    loop.bound_path = lambda key: {"current": "current.level", "current_range": "current.range"}.get(key)  # type: ignore[method-assign]
    loop.param = lambda key: {"current": 5.0, "current_ranges": [[0, 24], [0, 240]]}.get(key, 0.0)  # type: ignore[method-assign]
    loop.on_state("current_range", "current.range", 240.0, 30.0)
    assert loop.owner.writes == [("current.range", 240.0)]
    loop.owner.writes.clear()
    loop.on_state("current_range", "current.range", 240.0, 5.0)
    assert loop.owner.writes == [("current.range", 24.0)]


def test_a_setpoint_outside_the_new_range_goes_to_its_nearest_edge():
    loop = _loop()
    paths = {
        "current": "current.level",
        "current_range": "current.range",
        "resistance": "res.level",
        "resistance_range": "res.range",
    }
    values = {
        "current": 100.0,
        "current_ranges": [[0, 24], [0, 240]],
        "resistance": 2.0,
        "resistance_ranges": [[0.01, 10], [10, 7500]],
    }
    loop.bound_path = lambda key: paths.get(key)  # type: ignore[method-assign]
    loop.param = lambda key: values.get(key, 0.0)  # type: ignore[method-assign]
    loop.on_state("current_range", "current.range", 240.0, 24.0)
    assert loop.owner.writes == [("current.level", 24.0)]  # above the low range: its maximum
    loop.owner.writes.clear()
    loop.on_state("resistance_range", "res.range", 10.0, 7500.0)
    assert loop.owner.writes == [("res.level", 10.0)]  # below the high range: its minimum


def test_a_step_while_open_ramps_from_zero():
    loop = _loop(function="CC", current=20.0, slew=1.0)
    loop._params["current"] = 10.0
    loop.on_state("current", "current.level", 20.0, 10.0)  # the load conducts nothing (open)
    assert loop._ramp_from == 0.0
    loop.owner.t_ns = 5_000
    assert loop._level() == pytest.approx(5.0)


def test_without_ranges_a_range_is_only_a_setting():
    loop = _loop()
    loop.on_state("current_range", "current.range", 4.0, 40.0)
    assert loop.owner.writes == []


def test_disabled_ocp_never_trips():
    prot = _protection(ocp=10.0, ocp_enabled=False)
    assert not prot.judge(5.0, 50.0)
    assert not prot.tripped()


def test_ocp_without_delay_trips_at_once():
    prot = _protection(ocp=10.0)
    assert prot.judge(5.0, 12.0)
    assert prot.kind() == "OCP"


def test_ocp_delay_trips_only_when_the_overcurrent_lasts():
    prot = _protection(ocp=10.0, ocp_delay=3.0)
    owner = prot.owner
    assert not prot.judge(5.0, 12.0)  # the delay starts
    assert owner.pending == {"load/prot/OCP": 3_000_000_000}
    assert prot.status_flags()["OC"] and not prot.status_flags()["PS"]
    owner.t_ns = 2_000_000_000
    assert not prot.judge(5.0, 12.0)
    owner.t_ns = 3_000_000_000
    assert prot.judge(5.0, 12.0)
    assert prot.kind() == "OCP" and prot.status_flags()["PS"]


def test_an_overcurrent_that_ends_before_the_delay_does_not_trip():
    prot = _protection(ocp=10.0, ocp_delay=3.0)
    owner = prot.owner
    prot.judge(5.0, 12.0)
    owner.t_ns = 1_000_000_000
    assert not prot.judge(5.0, 8.0)  # back under the limit: the delay stops
    assert owner.pending == {}
    owner.t_ns = 5_000_000_000
    assert not prot.judge(5.0, 12.0)  # a new delay from now
    assert owner.pending == {"load/prot/OCP": 8_000_000_000}


def test_ovp_stays_immediate_and_sets_ov_and_vf():
    prot = _protection(ovp=130.0, ocp_delay=3.0)
    assert prot.judge(131.0, 1.0)
    flags = prot.status_flags()
    assert flags["OV"] and flags["VF"] and not flags["PS"]


def test_opp_delay_and_clear():
    prot = _protection(opp=1500.0, opp_delay=1.0)
    owner = prot.owner
    assert not prot.judge(100.0, 16.0)  # 1600 W
    owner.t_ns = 1_000_000_000
    assert prot.judge(100.0, 16.0)
    assert prot.kind() == "OPP" and prot.status_flags()["OP"]
    prot.clear()
    assert not prot.tripped() and not prot.status_flags()["OP"]
