"""The protection latch behind the over-limit protections of sources and sinks (#631).

``ProtectionLatch`` is the latch a protection carries: the thresholds declared on the class, the
trip latch and its kind, the clear semantics, the latch's status flags and the tripped/kind reply
words. It is a component-internal implementation, not a component type of its own (D50): mixed
into a protection component (the supply's and the load's) or into a control block (the
calibrator's output control).

What a trip *does* beyond latching is the carrier's increment, hooked through ``_on_trip`` /
``_on_clear``: the supply switches its output off through the output setting and restores it on
clear, the load's input simply stops conducting while the latch holds, the calibrator returns to
standby and queues its definition's error. The supply's OCP delay in constant current (#271,
virtual time) is likewise its own increment — the latch knows no clocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from ...scpi import values as V


@dataclass(frozen=True)
class TripThreshold:
    """One over-limit threshold of a protection, declared on the carrier's class.

    ``kind`` is the latch's kind when this threshold is crossed (OVP, OCP, OPP ...), ``param``
    the parameter carrying the threshold (0: that protection is off) and ``quantity`` what it
    judges: "v" (|v|), "i" (|i|) or "p" (|v·i|). ``tolerance`` is a relative margin the crossing
    must exceed (the trip needs the quantity beyond limit·(1 + tolerance)); ``flag`` names the
    status flag the latch raises while this kind stands ("" : none).
    """

    kind: str
    param: str
    quantity: str
    tolerance: float = 0.0
    flag: str = ""


if TYPE_CHECKING:
    from ...engine.session import Session


class ProtectionLatch:
    """The trip latch of an over-limit protection: declared thresholds, the latch and its kind,
    the clear semantics and the tripped/kind replies. The carrier is a component (or a component
    mixin): it provides ``param`` and, for the hooks and the replies, ``owner`` / ``session``.

    ``trip`` latches the first declared threshold the operating point crosses and runs the
    carrier's trip action; ``latch`` stores a fault the carrier judged itself (an OCP timer's
    expiry, the calibrator's overload) without one. ``clear`` removes the latch (only a ``kind``
    fault when one is named) and runs the carrier's clear action; ``_init_latch`` resets it
    without actions (*RST).
    """

    #: The thresholds this protection judges, in trip priority order; () for a carrier that only
    #: uses the latch (its judging is its own, e.g. the calibrator's overload).
    thresholds: ClassVar[tuple[TripThreshold, ...]] = ()

    if TYPE_CHECKING:
        # What the carrier component provides (#898): declared here, not assumed. Not defined at
        # run time — ``Component`` supplies them.
        session: Session

        def param(self, key: str) -> Any: ...

    def _init_latch(self) -> None:
        self._latched = ""  # the threshold crossed ("OVP" | ... | "PROT"); '' when clear

    # ---- the latch --------------------------------------------------------------------------------

    def tripped(self) -> bool:
        return bool(self._latched)

    def kind(self) -> str:
        return self._latched

    def _crossed(self, threshold: TripThreshold, v: float, i: float) -> bool:
        limit = float(self.param(threshold.param))
        if limit <= 0:
            return False
        got = abs(v if threshold.quantity == "v" else i if threshold.quantity == "i" else v * i)
        return got > limit * (1 + threshold.tolerance)

    def check(self, v: float, i: float) -> bool:
        """Whether the operating point crosses a declared threshold (the caller then trips);
        False while the latch holds."""
        return not self._latched and any(self._crossed(t, v, i) for t in self.thresholds)

    def trip(self, v: float, i: float) -> None:
        """Latch the first threshold the operating point crosses ("PROT" when none does: the
        caller judged the fault beyond the declared thresholds) and run the trip action."""
        if self._latched:
            return
        self._latched = next((t.kind for t in self.thresholds if self._crossed(t, v, i)), "PROT")
        self._on_trip()

    def latch(self, kind: str) -> None:
        """Latch a named fault directly, without the trip action (the carrier does its own)."""
        if self._latched:
            return
        self._latched = kind

    def clear(self, kind: str | None = None) -> None:
        """Remove the latch (only a ``kind`` fault when one is named: CURR:PROT:CLE clears OCP,
        VOLT:PROT:CLE OVP) and run the clear action."""
        if kind is not None and self._latched != kind.upper():
            return
        self._latched = ""
        self._on_clear()

    # ---- the carrier's increments ------------------------------------------------------------------

    def _on_trip(self) -> None:
        """What a trip does beyond latching (switching the output off, queueing the model's
        error); nothing by default — the latch standing is the whole effect (the load)."""

    def _on_clear(self) -> None:
        """What a clear does beyond unlatching (restoring the output state); nothing by default."""

    # ---- the status bits and the replies -------------------------------------------------------------

    def status_flags(self) -> dict[str, bool]:
        """Each declared threshold's flag while its kind is latched (the questionable register's
        OV while OVP stands, OC while OCP does, #539); none without flags."""
        return {t.flag: self._latched == t.kind for t in self.thresholds if t.flag}

    def _trip_reply(self, kind: str | None = None) -> bytes:
        """The ``TRIP?`` answer: whether a fault is latched; with ``kind`` whether that one is.
        Solving first — a trip happens inside the mode update of a solve."""
        self.session.solve()
        if kind is not None:
            return V.format_bool(self._latched == kind.upper())
        return V.format_bool(self.tripped())

    def _kind_reply(self) -> bytes:
        """The ``KIND?`` answer: the threshold crossed, '' when none stands."""
        self.session.solve()
        return V.format_string(self.kind())


__all__ = ["ProtectionLatch", "TripThreshold"]
