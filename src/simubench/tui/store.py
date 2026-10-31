"""The event store: every session event, deduped by ``seq`` (tui-screens.md 7.2).

The server replays the FULL history on every (re)connect; ``seq`` is the event's stable id in
the session history, so an event is new exactly when its seq is greater than anything seen
before. The buffer keeps the most recent 10 000 events; older ones live in the run records.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Rows kept in memory for the fullscreen log (tui-screens.md 7.2).
BUFFER_LIMIT = 10_000


class EventStore:
    """Append-only store with seq dedupe; ``version`` lets screens notice changes cheaply."""

    def __init__(self, limit: int = BUFFER_LIMIT) -> None:
        self.limit = limit
        self.events: list[dict[str, Any]] = []
        self._max_seq = 0
        self.dropped = 0  # events that fell out of the buffer (they live in the run records)
        self.version = 0

    def add(self, ev: Mapping[str, Any]) -> bool:
        """Add one event; False when it is a replay of something already stored (or older)."""
        seq = ev.get("seq")
        if seq is None or int(seq) <= self._max_seq:
            return False
        self._max_seq = max(self._max_seq, int(seq))
        self.events.append(dict(ev))
        if len(self.events) > self.limit:
            self.dropped += len(self.events) - self.limit
            del self.events[: len(self.events) - self.limit]
        self.version += 1
        return True

    def add_many(self, events: Iterable[Mapping[str, Any]]) -> int:
        """Add a backlog; returns how many were new."""
        added = 0
        for ev in events:
            if self.add(ev):
                added += 1
        return added

    def clear(self) -> None:
        """Drop everything (a fresh session after a reset starts a new history)."""
        self.events.clear()
        self._max_seq = 0
        self.dropped = 0
        self.version += 1

    def __len__(self) -> int:
        return len(self.events)
