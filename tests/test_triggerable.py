"""The triggerable primitive (#632): the arm → ready → fire → abort machine, its ready and
trigger delays in virtual time, its ignored-trigger policies and its settling — tested directly
on a minimal carrier.

The carriers only add their increments (the trigger model's readings, the supply's transient
step); the machine lives once in ``library/components/trigger.py`` and these tests keep its
semantics.
"""

from __future__ import annotations

from typing import Any

import pytest

from simubench.library.components.trigger import Triggerable

pytestmark = pytest.mark.unit


class Clock:
    """The session's clock: the mode the primitive collapses a firing on."""

    def __init__(self, mode: str) -> None:
        self.clock_mode = mode


class FakeOwner:
    """A minimal instrument: the clock the primitive reads and the pending table it schedules on.
    The command clock's ``wait_until`` returns the time without spending it — the caller's
    message spends it — and ``settle`` runs what is due by now, as a dispatch would."""

    def __init__(self, clock: str = "command") -> None:
        self.now = 1_000_000  # ns
        self.session = Clock(clock)
        self.pending: dict[str, tuple[int, Any]] = {}
        self.canceled = 0

    def now_ns(self) -> int:
        return self.now

    def register_pending(self, key: str, done_ns: int, on_done: Any, *, waited: bool = True) -> None:
        self.pending[key] = (int(done_ns), on_done)

    def cancel_pending(self, key: str) -> None:
        if self.pending.pop(key, None) is not None:
            self.canceled += 1

    def wait_until(self, done_ns: int) -> int:
        return max(0, int(done_ns) - self.now)

    def settle(self) -> None:
        for key, (done, on_done) in sorted(self.pending.items(), key=lambda kv: kv[1][0]):
            if done <= self.now:
                del self.pending[key]
                on_done()


class FakeTrigger(Triggerable):
    """A minimal carrier: source and delays as params, the hooks counted; ``span`` makes a firing
    take that long past ``_act_ns``, ``next`` is where a firing settles."""

    uid = "fake"

    def __init__(self, owner: FakeOwner, **params: Any) -> None:
        self.owner = owner
        self._params = params
        self._init_trigger()
        self.arms = 0  # _on_arm ran
        self.triggers = 0  # _on_trigger ran (a trigger was accepted)
        self.acts = 0  # _act ran (the action was taken)
        self.settles = 0  # _on_settled ran

    def param(self, key: str) -> Any:
        return self._params.get(key)

    def _on_arm(self) -> None:
        self.arms += 1

    def _on_trigger(self) -> None:
        self.triggers += 1

    def _fire_span(self) -> int | None:
        return self._act_ns + int(self.span) if getattr(self, "span", None) is not None else None

    def _act(self) -> None:
        self.acts += 1

    def _next_after_act(self) -> str:
        return str(self._params.get("next", "idle"))

    def _on_settled(self) -> None:
        self.settles += 1


def test_arm_readies_at_once_and_runs_the_arm_hook():
    trig = FakeTrigger(FakeOwner(), source="bus")
    trig.arm()
    assert trig.ready and trig.armed and not trig.delaying
    assert trig.arms == 1
    assert trig.owner.pending == {}  # no ready delay: nothing scheduled


def test_the_ready_delay_keeps_the_trigger_out_until_it_is_over():
    owner = FakeOwner()
    trig = FakeTrigger(owner, source="bus", ready_delay=0.005)
    trig.arm()
    assert trig.armed and not trig.ready  # TRAN-active, not yet WTG-ready
    assert owner.pending[trig.uid] == (owner.now + 5_000_000, trig._became_ready)
    trig.trigger_now()
    assert trig.acts == 0  # too early: not ready for one
    owner.now += 5_000_000
    owner.settle()
    assert trig.ready and trig.armed  # ready, and armed with it
    trig.trigger_now()
    assert trig.acts == 1  # accepted once ready


def test_a_trigger_before_the_system_is_ready_is_ignored():
    trig = FakeTrigger(FakeOwner(), source="bus", ready_delay=0.005)
    trig.arm()
    trig.trigger_in("bus")  # no trigger_ignored_error: ignored silently (0 means the same)
    assert trig.acts == 0 and trig.triggers == 0


def test_a_trigger_while_not_ready_reports_its_configured_error():
    trig = FakeTrigger(FakeOwner(), source="bus", trigger_ignored_error=-211)
    with pytest.raises(Exception) as e:
        trig.trigger_in("bus")
    assert getattr(e.value, "code", None) == -211
    with pytest.raises(Exception) as e:
        trig.trigger_now(why="not ready")
    assert "not ready" in str(e.value)  # the carrier's own wording for its trigger command


def test_an_arm_while_armed_restarts_nothing():
    trig = FakeTrigger(FakeOwner(), source="bus")
    trig.arm()
    trig.arm()  # no ignored_error param: silently nothing restarts
    assert trig.arms == 1


def test_an_arm_while_armed_reports_ignored_error():
    trig = FakeTrigger(FakeOwner(), source="bus", ignored_error=-213)
    trig.arm()
    with pytest.raises(Exception) as e:
        trig.arm()
    assert getattr(e.value, "code", None) == -213
    assert "Init ignored" in str(e.value)


def test_an_immediate_source_fires_when_it_is_ready():
    trig = FakeTrigger(FakeOwner(), source="immediate")
    trig.arm()
    assert trig.acts == 1  # the action ran as part of the arm
    assert not trig.armed and trig.settles == 1  # a one-shot settles to idle


def test_a_bus_trigger_is_accepted_only_while_waiting_for_a_bus_one():
    trig = FakeTrigger(FakeOwner(), source="bus")
    trig.arm()
    trig.trigger_in("immediate")  # a trigger from elsewhere is not this source's
    assert trig.triggers == 0
    trig.trigger_in("BUS")  # the source's name in any case
    assert trig.triggers == 1 and trig.acts == 1


def test_the_models_own_trigger_takes_any_source():
    trig = FakeTrigger(FakeOwner(), source="external")
    trig.arm()
    trig.trigger_now()
    assert trig.triggers == 1 and trig.acts == 1


def test_the_trigger_delay_defers_the_action():
    owner = FakeOwner()
    trig = FakeTrigger(owner, source="bus", delay=0.01)
    trig.arm()
    trig.trigger_in("bus")
    assert trig.delaying and trig.acts == 0  # triggered, the delay runs
    assert owner.pending[trig.uid] == (owner.now + 10_000_000, trig._fire_step)
    owner.now += 10_000_000
    owner.settle()
    assert trig.acts == 1 and not trig.armed  # the action ran, the system is idle again


def test_a_firing_that_takes_time_runs_the_action_at_its_end():
    owner = FakeOwner()
    trig = FakeTrigger(owner, source="bus")
    trig.span = 40_000_000  # the action itself takes 40 ms past the trigger
    trig.arm()
    trig.trigger_in("bus")
    assert owner.pending[trig.uid][0] == owner.now + 40_000_000  # one pending for the whole firing
    owner.now += 39_000_000
    owner.settle()
    assert trig.acts == 0  # not yet
    owner.now += 1_000_000
    owner.settle()
    assert trig.acts == 1 and not trig.armed


def test_a_firing_settles_ready_for_the_runs_next_trigger():
    trig = FakeTrigger(FakeOwner(), source="bus", next="ready")
    trig.arm()
    trig.trigger_in("bus")
    assert trig.ready and trig.acts == 1  # the next trigger of the run may come


def test_wait_fired_drives_the_firing_and_returns_the_time_it_takes():
    owner = FakeOwner()
    trig = FakeTrigger(owner, source="bus", delay=0.01)
    trig.span = 40_000_000
    trig.arm()
    trig.trigger_in("bus")
    t0 = owner.now
    used = trig.wait_fired()
    owner.now += used
    assert trig.acts == 1 and not trig.armed  # driven to the end, action included
    assert used == 50_000_000  # the trigger delay and the action's own time
    assert owner.now == t0 + used
    assert trig.wait_fired() == 0  # nothing in progress: no more time


def test_abort_drops_a_pending_ready_and_a_firing():
    owner = FakeOwner()
    trig = FakeTrigger(owner, source="bus", ready_delay=0.005, delay=0.01)
    trig.arm()
    trig.abort()
    assert not trig.armed and trig.acts == 0
    owner.now += 10_000_000
    owner.settle()
    assert trig.acts == 0  # the pending ready is gone, not a forgotten callback
    trig.arm()
    trig.trigger_in("bus")
    trig.abort()
    assert not trig.delaying and trig.acts == 0
    owner.now += 10_000_000
    owner.settle()
    assert trig.acts == 0  # the pending firing is gone too
    assert trig.settles == 2  # the abort settled each time


def test_a_firing_is_instant_on_a_stepped_clock():
    owner = FakeOwner(clock="stepped")
    trig = FakeTrigger(owner, source="bus")
    trig.span = 40_000_000  # nothing would ever complete it: the firing is taken at once
    trig.arm()
    trig.trigger_in("bus")
    assert trig.acts == 1 and not trig.armed
    assert owner.pending == {}


def test_the_trigger_delay_stands_on_a_stepped_clock():
    """The trigger delay is timer semantics, not the action's cost: on a stepped clock it still
    defers the action, which ``advance`` runs — only the firing's own span collapses (issue 659)."""
    owner = FakeOwner(clock="stepped")
    trig = FakeTrigger(owner, source="bus", delay=0.01)
    trig.span = 40_000_000
    trig.arm()
    trig.trigger_in("bus")
    assert trig.delaying and trig.acts == 0
    assert owner.pending[trig.uid] == (owner.now + 10_000_000, trig._fire_step)  # the delay, not the span
    owner.now += 9_000_000
    owner.settle()
    assert trig.acts == 0  # still within the delay
    owner.now += 1_000_000
    owner.settle()
    assert trig.acts == 1 and not trig.armed
