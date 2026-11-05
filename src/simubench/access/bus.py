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
import weakref
from collections.abc import Hashable
from dataclasses import dataclass, field

from ..engine.session import Session

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

    @property
    def state(self) -> str:
        """``local`` | ``remote`` | ``local_lockout`` | ``remote_lockout`` (488.1 LOCS/REMS/LWLS/RWLS)."""
        if self.lockout:
            return "remote_lockout" if self.remote else "local_lockout"
        return "remote" if self.remote else "local"


@dataclass
class LockTable:
    """Locks of one instrument, shared by every session that reaches it."""

    exclusive: Hashable | None = None
    shared: set[Hashable] = field(default_factory=set)
    shared_key: bytes = b""
    #: Fired whenever a lock is released, so asyncio waiters re-check and blocked channels resume.
    released: asyncio.Event = field(default_factory=asyncio.Event)

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

    def release(self, holder: Hashable) -> int:
        """Release what this session holds; returns the HiSLIP AsyncLockResponse code."""
        if holder is self.exclusive:
            self.exclusive = None
            self.released.set()
            return 1  # released an exclusive lock
        if holder in self.shared:
            self.shared.discard(holder)
            self.released.set()
            return 2  # released a shared lock
        return 3  # error: this session held no lock

    def holders(self) -> int:
        return (1 if self.exclusive is not None else 0) + len(self.shared)


@dataclass
class InstrumentBus:
    """Everything the interfaces of one instrument have in common."""

    locks: LockTable = field(default_factory=LockTable)
    remote_local: RemoteLocal = field(default_factory=RemoteLocal)


_REGISTRY: weakref.WeakKeyDictionary[Session, dict[str, InstrumentBus]] = weakref.WeakKeyDictionary()


def bus_of(session: Session, instrument: str) -> InstrumentBus:
    """The bus state of one instrument; every access path of a session sees the same object."""
    return _REGISTRY.setdefault(session, {}).setdefault(instrument, InstrumentBus())
