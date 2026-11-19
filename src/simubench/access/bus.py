"""Bus-level state every access path shares: resource locks and remote/local control.

A real instrument has one set of locks and one REN/GTL/LLO state no matter which interface a
script uses (VXI-11, HiSLIP, GPIB, USBTMC), so the state lives here, per instrument of a
:class:`~simubench.engine.session.Session`, and the access paths only translate their wire
messages into it (D41, D63).

Locks follow VISA (VPP-4.3 ``viLock``) and IVI-6.1 2.6: one exclusive lock, or one shared lock
that sessions join by presenting the same key; a session that is not a holder is kept out while
any lock is held. Holders are whatever hashable object the access path uses for a session.
"""

from __future__ import annotations

import asyncio
import contextlib
import weakref
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from ..engine.session import Session

#: How long a lock wait sleeps between wake-ups when no release event fires (a missed event must
#: not hang the wait). One decision for every access path (#1475): the four call-site copies
#: polled at 0.1 / 0.05 / 0.02 / 0.02 s. A wake on the release event is immediate; the poll only
#: bounds how stale a wait can be, so 50 ms costs nothing perceptible and spares the event loop
#: the VXI-11 copy's 50 wake-ups per second per waiter.
LOCK_POLL_S = 0.05


class BusError(Exception):
    """A condition VISA reports as an error status: ``code`` is its VI_ERROR_* value."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


#: viGpibControlREN modes (VPP-4.3), which are also the HiSLIP AsyncRemoteLocalControl codes
#: (IVI-6.1 table 22) and what the VXI-11 and USB488 calls map onto.
REMOTE_LOCAL: dict[int, str] = {
    0: "disable remote",
    1: "enable remote",
    2: "disable remote and go to local",
    3: "enable remote and go to remote",
    4: "enable remote and lock out local",
    5: "enable remote, go to remote and lock out local",
    6: "go to local",
}
REN_DEASSERT, REN_ASSERT, REN_DEASSERT_GTL, REN_ASSERT_ADDRESS = 0, 1, 2, 3
REN_ASSERT_LLO, REN_ASSERT_ADDRESS_LLO, REN_ADDRESS_GTL = 4, 5, 6


@dataclass
class RemoteLocal:
    """The IEEE 488.1 remote/local state of one instrument (REN line, remote state, LLO)."""

    ren: bool = False
    remote: bool = False
    lockout: bool = False

    def control(self, code: int) -> str:
        """Apply one control code; returns its name. ``KeyError`` for a code that does not exist."""
        action = REMOTE_LOCAL[code]
        if code in (REN_DEASSERT, REN_DEASSERT_GTL):
            self.ren = self.remote = False
            if code == REN_DEASSERT_GTL:
                self.lockout = False
        elif code == REN_ASSERT:
            self.ren = True
        elif code in (REN_ASSERT_ADDRESS, REN_ASSERT_ADDRESS_LLO):
            self.ren = self.remote = True
            self.lockout = self.lockout or code == REN_ASSERT_ADDRESS_LLO
        elif code == REN_ASSERT_LLO:
            self.ren = self.lockout = True
        else:  # REN_ADDRESS_GTL: GTL returns to local; a lockout stays until REN is released
            self.remote = False
        return action

    # The 488.1 remote/local interface function (RL1) as local messages and bus messages.

    def assert_ren(self) -> None:
        self.ren = True

    def release_ren(self) -> None:
        """REN false returns every state to LOCS, lockout included."""
        self.ren = self.remote = self.lockout = False

    def listen(self) -> None:
        """Addressed to listen, or a message received: REMS (RWLS under lockout) when REN is true."""
        if self.ren:
            self.remote = True

    def gtl(self) -> None:
        """Go to local: REMS -> LOCS, RWLS -> LWLS; a lockout stays."""
        self.remote = False

    def llo(self) -> None:
        """Local lockout: LOCS -> LWLS, REMS -> RWLS."""
        self.lockout = True

    @property
    def state(self) -> str:
        """``local`` | ``remote`` | ``local_lockout`` | ``remote_lockout`` (488.1 LOCS/REMS/LWLS/RWLS)."""
        if self.lockout:
            return "remote_lockout" if self.remote else "local_lockout"
        return "remote" if self.remote else "local"


@dataclass
class ServiceRequest:
    """The request-service latch (RQS) of IEEE 488.1 2.7 and 488.2 11.2.

    The instrument's status byte carries MSS in bit 6: a summary, true while any reason enabled by
    the service request enable register exists. A service request is a *new* reason, which sets
    RQS and asserts SRQ; a serial poll returns the status byte with RQS in bit 6 and clears it. So
    a script that waits for SRQ and then polls sees the request once, however long the reason lasts.
    """

    latch: bool = False
    reasons: int = 0  # the enabled reasons seen so far (status bits ANDed with the enable register)

    def observe(self, stb: int, sre: int) -> None:
        """Look at the status byte after something may have changed it."""
        current = stb & sre & ~0x40
        if current & ~self.reasons:
            self.latch = True
        self.reasons = current

    def poll(self, stb: int, sre: int) -> int:
        """A serial poll: the status byte as 488.1 returns it, and RQS cleared."""
        self.observe(stb, sre)
        out = (stb & ~0x40) | (0x40 if self.latch else 0)
        self.latch = False
        return out

    def edge(self, sent: bool) -> tuple[bool, bool]:
        """One listener's SRQ edge, read and renewed in one call: ``(announce, sent)``.

        ``sent`` is the listener's own "the pending request went out" flag — every client keeps
        one (HiSLIP's AsyncServiceRequest, VXI-11's device_intr_srq: one announcement per
        request, HiSLIP 6.13 / IEEE 488.2 11.2). Call after :meth:`observe`. A pending request
        the listener has not announced announces once, and the flag is set; no pending request
        clears the flag, so the next request announces again; a pending request already
        announced leaves it. A serial poll has carried RQS to whoever asked (the latch cleared),
        so calling this after one clears the flag the same way: the request went out by poll.
        """
        return self.latch and not sent, self.latch


@dataclass
class LockTable:
    """Locks of one instrument, shared by every session that reaches it.

    The one book of who holds what (#1475): the servers ask :meth:`held_by` whether a client is
    in control, VISA sessions stack their ``viLock``s here (:meth:`acquire`), and a server waits
    for the lock with :meth:`wait_accessible` instead of a poll loop of its own."""

    exclusive: Hashable | None = None
    shared: set[Hashable] = field(default_factory=set)
    shared_key: bytes = b""
    #: Fired whenever a lock is released, so asyncio waiters re-check and blocked channels resume.
    released: asyncio.Event = field(default_factory=asyncio.Event)
    #: How many viLocks each holder has stacked (VPP-4.3: as many unlocks). Only :class:`Link`
    #: sessions nest their locks; the network servers' locks are one grant, one release.
    depth: dict[Hashable, int] = field(default_factory=dict)

    def held_by(self, holder: Hashable) -> bool:
        return holder is self.exclusive or holder in self.shared

    def accessible(self, holder: Hashable) -> bool:
        """No locks at all, or this session is one of the holders."""
        return (self.exclusive is None and not self.shared) or self.held_by(holder)

    def grant(self, holder: Hashable, exclusive: bool, key: bytes = b"") -> bool:
        """Take the lock if it is free to this session; sessions share a lock by sharing its key."""
        if exclusive:
            if self.exclusive is not None or self.shared:
                return False
            self.exclusive = holder
            return True
        if self.exclusive is not None or (self.shared and key != self.shared_key):
            return False
        if not self.shared:
            self.shared_key = key
        self.shared.add(holder)
        return True

    def acquire(self, holder: Hashable, exclusive: bool, key: bytes = b"") -> bool:
        """VISA ``viLock`` with nesting: a lock of the kind (and key) the holder already holds
        counts up and needs as many unlocks; another kind (or key) is refused while held."""
        if self.held_by(holder):
            if (self.exclusive is holder) == exclusive and (exclusive or self.shared_key == key):
                self.depth[holder] = self.depth.get(holder, 0) + 1
                return True
            return False
        if self.grant(holder, exclusive, key):
            self.depth[holder] = 1
            return True
        return False

    def release(self, holder: Hashable) -> int:
        """Release what this session holds; returns the HiSLIP AsyncLockResponse code."""
        self.depth.pop(holder, None)
        if holder is self.exclusive:
            self.exclusive = None
            self.released.set()
            return 1  # released an exclusive lock
        if holder in self.shared:
            self.shared.discard(holder)
            self.released.set()
            return 2  # released a shared lock
        return 3  # error: this session held no lock

    def release_one(self, holder: Hashable) -> bool:
        """One stacked lock undone; the resource is freed at the outermost (``False``: held none)."""
        left = self.depth.get(holder, 0) - 1
        if left < 0:
            return False
        if left:
            self.depth[holder] = left
        else:
            self.release(holder)
        return True

    async def wait_accessible(
        self,
        holder: Hashable,
        timeout_s: float | None = None,
        *,
        exclusive: bool | None = None,
        key: bytes = b"",
        give_up: Callable[[], bool] | None = None,
    ) -> bool:
        """Wait until ``holder`` may use the instrument — no lock held, or its own — and, when
        ``exclusive`` is given, take the lock the moment it is free: a successful wait grants
        (VXI-11's device_lock granted inside its poll loop; that timing is the semantics, #1475).

        ``exclusive=None`` waits for accessibility only, as the guard before I/O does. False when
        ``timeout_s`` (``None`` waits unboundedly) is spent first or ``give_up`` turns true (a
        connection being torn down stops waiting). The wait wakes on every release; the
        :data:`LOCK_POLL_S` sleep only bounds one whose release event was missed."""
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while not (give_up is not None and give_up()):
            if exclusive is None:
                if self.accessible(holder):
                    return True
            elif self.grant(holder, exclusive, key):
                return True
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                return False
            self.released.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self.released.wait(),
                    timeout=LOCK_POLL_S if remaining is None else min(remaining, LOCK_POLL_S),
                )
        return False

    def holders(self) -> int:
        return (1 if self.exclusive is not None else 0) + len(self.shared)


@dataclass
class InstrumentBus:
    """Everything the interfaces of one instrument have in common."""

    locks: LockTable = field(default_factory=LockTable)
    remote_local: RemoteLocal = field(default_factory=RemoteLocal)
    service_request: ServiceRequest = field(default_factory=ServiceRequest)


_REGISTRY: weakref.WeakKeyDictionary[Session, dict[str, InstrumentBus]] = weakref.WeakKeyDictionary()


def bus_of(session: Session, instrument: str) -> InstrumentBus:
    """The bus state of one instrument; every access path of a session sees the same object."""
    buses = _REGISTRY.get(session)
    if buses is None:
        buses = _REGISTRY[session] = {}
        # The status byte changes with every message, whichever interface it came over, so the
        # request-service latch watches the engine's events instead of relying on the access paths:
        # a reason that goes away and comes back between two polls is still a new reason.
        session.events.subscribe(lambda event: _watch(session, event))
    return buses.setdefault(instrument, InstrumentBus())


def _watch(session: Session, event: dict) -> None:
    name = event.get("instrument")
    if event.get("kind") == "scpi" and name in session.instruments:
        observe(session, name)


def observe(session: Session, instrument: str) -> None:
    """Let the instrument's request-service latch see its status byte (call after any I/O)."""
    inst = session.instrument(instrument)
    # Only a look: the observer must not latch what the status registers have not latched yet.
    bus_of(session, instrument).service_request.observe(inst.stb(commit=False), inst.sre)


def serial_poll(session: Session, instrument: str) -> int:
    """A serial poll of one instrument, the same on every bus that has one."""
    inst = session.instrument(instrument)
    return bus_of(session, instrument).service_request.poll(inst.stb(), inst.sre)
