"""The session's event stream (D57's emitter side, #1531).

Every event the bench produces goes through :meth:`EventBus.emit`: it gets its cursor id
(``eid``) and its virtual time (``t_ns``), is counted by kind (the metrics), kept in the recent
history (a client connecting late sees what happened), written to the run record, and handed to
the listeners. The shape of the events is declared in :mod:`.events`.
"""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .instrument import Instrument

Listener = Callable[[dict[str, Any]], None]

#: How many recent events the history keeps.
HISTORY_LENGTH = 20_000


class EventBus:
    def __init__(self, now: Callable[[], int]) -> None:
        self._now = now
        self._listeners: list[Listener] = []
        #: Recent events, so that a client connecting late sees what happened before (UI history).
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_LENGTH)
        #: Counters for metrics (M4): events by kind, circuit solves and the time they took.
        self.stats: dict[str, float] = {}
        #: Id of the last emitted event; ids are stable cursors for event readers.
        self.event_id = 0
        #: Where every event is also written (the run record's writer), or None.
        self.record: Listener | None = None

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        """Call ``fn`` with every event from now on; the returned function unsubscribes."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def emit(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind"))
        self.stats[kind] = self.stats.get(kind, 0) + 1
        self.event_id += 1
        event = {"eid": self.event_id, "t_ns": self._now(), **event}
        self.history.append(event)
        if self.record is not None:
            self.record(event)
        for fn in list(self._listeners):
            with contextlib.suppress(Exception):  # a misbehaving listener must not break the bench
                fn(event)

    def instrument_event(self, inst: Instrument, kind: str, data: dict[str, Any]) -> None:
        """An event about one instrument (an error, a warning, a file, an ignored command)."""
        self.emit({"kind": kind, "instrument": inst.name, **data})
