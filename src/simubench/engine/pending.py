"""The operations an instrument has started that finish at a later virtual instant (#895).

``*OPC``, ``*OPC?`` and ``*WAI`` wait for them; ``*OPC`` sets ESR bit 0 once they are all done. The
bookkeeping lives here, on its own: no clock, no status register, no session. The instrument asks
what is due at a given instant and sets the ESR bit itself, so this class is tested by calling it.
"""

from __future__ import annotations

from collections.abc import Callable


class PendingOps:
    def __init__(self) -> None:
        #: key -> (done at, called when done).
        self.pending: dict[str, tuple[int, Callable[[], None] | None]] = {}
        #: Pending operations *OPC, *OPC? and *WAI do not wait for (the model says so, e.g. a scope
        #: whose *OPC? answers at once while an acquisition runs).
        self.unwaited: set[str] = set()
        #: Operations waiting for an event that may never come (a scope armed for a trigger that does
        #: not occur): while any is waiting, *OPC? does not answer, as on hardware the reply never comes.
        self.blockers: set[str] = set()
        self.opc_armed = False

    def register(
        self, key: str, done_ns: int, on_done: Callable[[], None] | None = None, *, waited: bool = True
    ) -> None:
        """An operation that finishes at virtual time ``done_ns``; ``on_done`` runs once it has.

        ``waited``: *OPC, *OPC? and *WAI wait for it (False: they answer regardless)."""
        self.pending[key] = (int(done_ns), on_done)
        if waited:
            self.unwaited.discard(key)
        else:
            self.unwaited.add(key)

    def cancel(self, key: str) -> None:
        self.pending.pop(key, None)
        self.unwaited.discard(key)

    def block(self, key: str, waiting: bool) -> None:
        """Mark (or clear) an operation that waits for an event that may never come."""
        if waiting:
            self.blockers.add(key)
        else:
            self.blockers.discard(key)

    def waited(self) -> list[int]:
        """The finish instants of the operations *OPC, *OPC? and *WAI wait for."""
        return [done for key, (done, _) in self.pending.items() if key not in self.unwaited]

    def finish_due(self, now_ns: int) -> None:
        """Finish, earliest first, what is due by ``now_ns`` (running each ``on_done``)."""
        for key, (done, on_done) in sorted(self.pending.items(), key=lambda kv: kv[1][0]):
            if done <= now_ns:
                del self.pending[key]
                self.unwaited.discard(key)
                if on_done is not None:
                    on_done()

    def opc_due(self) -> bool:
        """True once, when an armed *OPC has nothing left to wait for (the caller sets ESR bit 0)."""
        if self.opc_armed and not self.waited() and not self.blockers:
            self.opc_armed = False
            return True
        return False

    def arm_opc(self) -> bool:
        """``*OPC``: True when nothing is pending (the caller sets ESR bit 0 now); else armed."""
        if self.waited() or self.blockers:
            self.opc_armed = True
            return False
        return True

    def wait_target(self) -> int | None:
        """The instant everything waited for is done, None when nothing is."""
        waited = self.waited()
        return max(waited) if waited else None

    def clear(self) -> None:
        self.pending.clear()
        self.unwaited.clear()
        self.blockers.clear()
        self.opc_armed = False
