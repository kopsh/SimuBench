"""Noticing that a turn goes in circles (#873, D66).

A turn has no step limit, so the server watches for no progress instead: the same call with the
same arguments getting the same result :data:`REPEAT` times **in a row**, or :data:`ERROR_STREAK`
failed calls in a row with the same error. Anything new — another call, another result — resets
the count, so a self-repair loop (write, verify, read again) with other calls in between keeps
going. When it trips, the turn ends with the reason in words; what the turn wrote can be restored
with "restore to before the last turn".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

REPEAT = 3
ERROR_STREAK = 4


class Stuck(Exception):
    """The turn made no progress; the message says why, for the user."""


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class LoopGuard:
    def __init__(self) -> None:
        self.last: tuple[str, str, str] | None = None
        self.repeats = 0
        self.errors: list[str] = []

    def record(self, tool: str, args: Any, ok: bool, outcome: Any) -> None:
        """Note one call and its outcome (a result or an error text); raise :class:`Stuck` when the
        turn is going round."""
        key = (tool, _digest(args), _digest(outcome))
        if key == self.last:
            self.repeats += 1
        else:  # a different call or result is progress: the count starts over
            self.last, self.repeats = key, 1
        if ok:
            self.errors.clear()
        else:
            self.errors.append(str(outcome))
        if self.repeats >= REPEAT:
            what = f"：{outcome}" if not ok else ""
            raise Stuck(f"同一个操作（{tool}）连续 {REPEAT} 次得到同样的结果，没有进展，已停下{what}")
        streak = self.errors[-ERROR_STREAK:]
        if len(streak) == ERROR_STREAK and len(set(streak)) == 1:
            raise Stuck(f"连续 {ERROR_STREAK} 次遇到同一个错误，没有进展，已停下：{streak[0]}")
