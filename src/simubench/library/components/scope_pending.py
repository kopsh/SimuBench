"""The scope's pending acquisitions and the segments one acquisition takes (#134, #957, #1005).

``ScopeAcquisition`` (scope.py) is the run control: it finds the triggers and builds the records
(``acquire_segments``) and keeps them (``store_segments``). This module schedules an acquisition
that finishes in virtual time — when its last segment's record is full — and plans it again when
the trigger has not come or the signals change on the way. It reaches the acquisition only through
the members ``Acquisition`` names, so it is tested with a stand-in, without a bench
(tests/test_scope_pending.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .scope import Record


@dataclass
class _Segment:
    """One record per channel of an acquisition, taken on one trigger (a segmented acquisition takes
    several): whether it triggered, the instant of its trigger on the record's grid (of the
    record's trigger point when it did not) and the instant its record is full."""

    records: dict[int, Record]
    triggered: bool
    trigger_ns: int
    end_ns: int


class Acquisition(Protocol):
    """What the scheduling reads and calls of the run control (``ScopeAcquisition``)."""

    uid: str
    owner: Any  # the Instrument, None before the component is placed
    session: Any
    armed: bool
    last_triggered: bool

    def param(self, name: str) -> Any: ...
    def displayed(self) -> list[int]: ...
    def pending_mode(self) -> bool: ...
    def _is(self, key: str, value: Any = None) -> bool: ...
    def _finish_single(self) -> None: ...
    def acquire_segments(
        self, channels: list[int], taken: list[_Segment] | tuple[()] = (), force: bool = False
    ) -> tuple[list[_Segment], bool]: ...
    def store_segments(self, segments: list[_Segment], used: int) -> int: ...


class PendingAcquisitions:
    """The scope's pending acquisitions (``completion: pending``, #134): a single acquisition, a
    forced frame or a digitize is an operation that finishes in virtual time, when the record after
    the trigger is full; armed for a trigger that has not come, it waits for one, looking again
    whenever a command arrives. The run control (ScopeAcquisition) starts and reads these; the
    scheduling and its state are this class's.

    A segmented acquisition (``segments``) is one such operation: it is done when its last
    segment's record is full. Its triggers are found when it is armed, from the signals as they
    are then; when the signals change before it is done, the segments already triggered are kept
    and the rest are looked for again (#957)."""

    def __init__(self, acq: Acquisition) -> None:
        self.acq = acq
        self.clear()

    def clear(self) -> None:
        """No acquisition scheduled."""
        #: The scheduled acquisition: when it is done, when it (its last segment) triggered, and
        #: what it keeps: the segments, the time it takes, and how it was asked for.
        self.due_ns: int | None = None
        self.trigger_ns: int | None = None
        self._due: tuple[list[_Segment], int, list[int] | None, bool] | None = None
        #: Armed, the trigger not come yet: the channels to acquire once it does (None: those shown).
        self._awaiting: tuple[list[int] | None] | None = None
        #: The segments found so far of an acquisition still waiting for the trigger of a later one.
        self._found: list[_Segment] = []

    def waiting(self) -> bool:
        """A single acquisition (or a digitize) not done yet."""
        acq = self.acq
        return self.due_ns is not None or self._awaiting is not None or (acq.armed and acq._is("single"))

    def schedule(self, channels: list[int] | None, force: bool) -> None:
        """Arm an acquisition that finishes in virtual time: when the record after the trigger is
        full (the last segment's), or, without a trigger, once one comes (``retry``)."""
        if self.due_ns is not None:
            return  # already on its way
        self._plan(channels, force, [])

    def _plan(self, channels: list[int] | None, force: bool, taken: list[_Segment]) -> None:
        """Find the triggers of the segments after ``taken`` in the signals as they are now."""
        acq = self.acq
        assert acq.owner is not None
        acq.session.signal_changed(acq)  # this plan has seen the signals as they are now
        segments, complete = acq.acquire_segments(
            acq.displayed() if channels is None else channels, taken, force
        )
        acq.last_triggered = complete and all(seg.triggered for seg in segments)
        if not complete:
            self._found = segments
            self._awaiting = (channels,)
            acq.owner.block(acq.uid, str(acq.param("no_trigger")) == "wait")
            return
        self._awaiting, self._found = None, []
        used = segments[-1].end_ns + round(float(acq.param("overhead_s")) * 1e9) - acq.session.now_ns
        self.due_ns, self.trigger_ns = acq.owner.now_ns() + used, segments[-1].trigger_ns
        self._due = (segments, used, channels, force)
        acq.owner.block(acq.uid, False)
        acq.owner.register_pending(acq.uid, self.due_ns, self.complete, waited=bool(acq.param("opc_waits")))

    def complete(self) -> None:
        """The scheduled acquisition is done: keep its records; a single acquisition then stops."""
        assert self._due is not None
        segments, used, _, _ = self._due
        self.clear()
        acq = self.acq
        acq.store_segments(segments, used)
        if acq._is("single"):
            acq._finish_single()
        else:
            acq.armed = False

    def cancel(self) -> None:
        """Stop waiting: nothing scheduled, nothing blocking ``*OPC?``."""
        acq = self.acq
        if acq.owner is not None:
            acq.owner.cancel_pending(acq.uid)
            acq.owner.block(acq.uid, False)
        self.clear()

    def _triggered_by(self, segments: list[_Segment], now_ns: int) -> list[_Segment]:
        return [seg for seg in segments if seg.trigger_ns <= now_ns]

    def retry(self) -> None:
        """Armed for a trigger that had not come: look again now (the signal may have changed). A
        segmented acquisition on its way whose signals changed looks again for the segments that
        have not triggered yet."""
        acq = self.acq
        if not acq.pending_mode() or acq.owner is None:
            return
        now = acq.owner.now_ns()
        if self.due_ns is not None:
            if self._due is None or len(self._due[0]) < 2 or not acq.session.signal_changed(acq):
                return
            segments, _, channels, force = self._due
            taken = self._triggered_by(segments, now)
            if len(taken) < len(segments):
                acq.owner.cancel_pending(acq.uid)
                self.due_ns, self.trigger_ns, self._due = None, None, None
                self._plan(channels, force, taken)
        elif self._awaiting is not None:
            self._plan(self._awaiting[0], False, self._triggered_by(self._found, now))
        elif acq.armed and acq._is("single"):  # armed from the panel, waiting for its trigger
            self._plan(None, False, [])
