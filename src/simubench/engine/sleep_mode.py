"""The ``sleep`` idle mode (D47): ``time.sleep(t)`` advances virtual time instead of waiting.

The patch is process-wide but counted by open session: it installs when the first sleep-mode
session opens and restores the real function when the last one closes. A fake sleep advances
*every* open sleep-mode session, from whichever thread calls it; sessions in other modes are not
advanced (do not mix modes in one process and expect the wall clock to follow a fake sleep).

Only ``time.sleep`` is intercepted: ``from time import sleep`` done before the bench opened,
``asyncio.sleep`` and every other wait still wait for real. External processes (SOCKET, HiSLIP)
cannot be intercepted at all, which is why ``simubench serve`` treats ``sleep`` as ``off``.
"""

from __future__ import annotations

import threading
import time
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session import Session

_real_sleep = time.sleep
_lock = threading.Lock()
_open: weakref.WeakSet[Session] = weakref.WeakSet()


class _Patch:
    """Whether ``time.sleep`` is currently the fake one (guarded by ``_lock``)."""

    active = False


def _fake_sleep(seconds: float) -> None:
    if seconds < 0:
        raise ValueError("sleep length must be non-negative")
    with _lock:
        targets = [s for s in _open if s is not None]
    for session in targets:
        session.advance(seconds, reason="sleep")


def register(session: Session) -> None:
    with _lock:
        _open.add(session)
        if not _Patch.active:
            time.sleep = _fake_sleep  # ty: ignore[invalid-assignment] — the deliberate monkeypatch
            _Patch.active = True


def unregister(session: Session) -> None:
    with _lock:
        _open.discard(session)
        if _Patch.active and not _open:
            time.sleep = _real_sleep
            _Patch.active = False


def real_sleep_is_active() -> bool:
    """Whether the real ``time.sleep`` is in place (tests assert the patch is undone)."""
    return not _Patch.active
