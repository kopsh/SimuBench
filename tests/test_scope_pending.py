"""The scope's pending acquisitions (``scope_pending.py``, #134, #957, #1005): planning and planning
again, against a stand-in acquisition — no bench, no signals. Each segment says when it triggered
and when its record is full; the stand-in hands out the segments it was given, after those taken."""

from __future__ import annotations

from typing import Any

from simubench.library.components.scope_pending import PendingAcquisitions, _Segment


def seg(trigger_ns: int, end_ns: int, triggered: bool = True) -> _Segment:
    return _Segment({}, triggered, trigger_ns, end_ns)


class Owner:
    def __init__(self) -> None:
        self.now = 0
        self.blocked: list[tuple[str, bool]] = []
        self.registered: list[int] = []
        self.cancelled = 0

    def now_ns(self) -> int:
        return self.now

    def block(self, uid: str, wait: bool) -> None:
        self.blocked.append((uid, wait))

    def register_pending(self, uid: str, due_ns: int, done: Any, waited: bool) -> None:
        self.registered.append(due_ns)

    def cancel_pending(self, uid: str) -> None:
        self.cancelled += 1


class Session:
    def __init__(self, owner: Owner) -> None:
        self.owner = owner
        self.changed = False

    @property
    def now_ns(self) -> int:
        return self.owner.now

    def signal_changed(self, acq: Any) -> bool:
        changed, self.changed = self.changed, False
        return changed


class Acquisition:
    """What PendingAcquisitions reads of a ScopeAcquisition. ``plans`` are the segment lists each
    planning returns (after the ``taken`` it is given), with whether all of them triggered."""

    uid = "scope.acq"
    armed = True
    last_triggered = False

    def __init__(self, *plans: tuple[list[_Segment], bool]) -> None:
        self.owner = Owner()
        self.session = Session(self.owner)
        self.plans = list(plans)
        self.taken: list[list[_Segment]] = []

    def param(self, name: str) -> Any:
        return {"overhead_s": 1e-6, "no_trigger": "wait", "opc_waits": True}[name]

    def displayed(self) -> list[int]:
        return [1]

    def pending_mode(self) -> bool:
        return True

    def _is(self, key: str) -> bool:
        return key == "single"

    def acquire_segments(
        self, channels: list[int], taken: list[_Segment], force: bool
    ) -> tuple[list[_Segment], bool]:
        self.taken.append(list(taken))
        more, complete = self.plans.pop(0)
        return [*taken, *more], complete


def test_a_complete_plan_is_due_when_the_last_record_is_full_plus_the_overhead():
    acq = Acquisition(([seg(100, 2_000), seg(3_000, 5_000)], True))
    pending = PendingAcquisitions(acq)  # type: ignore[arg-type]
    pending.schedule(None, False)
    assert (pending.due_ns, pending.trigger_ns, acq.owner.registered) == (6_000, 3_000, [6_000])


def test_a_plan_whose_trigger_has_not_come_waits_and_blocks():
    acq = Acquisition(([seg(100, 2_000)], False))
    pending = PendingAcquisitions(acq)  # type: ignore[arg-type]
    pending.schedule(None, False)
    assert (pending.due_ns, pending.waiting(), acq.owner.blocked[-1]) == (None, True, ("scope.acq", True))


def test_a_retry_while_waiting_keeps_the_segments_that_have_triggered():
    first, late = seg(100, 2_000), seg(9_000, 11_000)
    acq = Acquisition(([first, late], False), ([seg(12_000, 14_000)], True))
    pending = PendingAcquisitions(acq)  # type: ignore[arg-type]
    pending.schedule(None, False)
    acq.owner.now = 5_000  # the first segment's trigger is past, the second one's is not
    pending.retry()
    assert (acq.taken[-1], pending.due_ns) == ([first], 5_000 + 14_000 + 1_000 - 5_000)


def test_a_retry_after_the_signals_changed_plans_again_the_segments_not_triggered_yet():
    first, second = seg(100, 2_000), seg(9_000, 11_000)
    acq = Acquisition(([first, second], True), ([seg(7_000, 9_000)], True))
    pending = PendingAcquisitions(acq)  # type: ignore[arg-type]
    pending.schedule(None, False)
    acq.owner.now, acq.session.changed = 5_000, True
    pending.retry()
    assert (acq.taken[-1], acq.owner.cancelled, pending.trigger_ns) == ([first], 1, 7_000)


def test_a_retry_without_a_signal_change_keeps_the_plan():
    acq = Acquisition(([seg(100, 2_000), seg(3_000, 5_000)], True))
    pending = PendingAcquisitions(acq)  # type: ignore[arg-type]
    pending.schedule(None, False)
    acq.owner.now = 2_500
    pending.retry()
    assert (len(acq.taken), acq.owner.cancelled, pending.due_ns) == (1, 0, 6_000)
