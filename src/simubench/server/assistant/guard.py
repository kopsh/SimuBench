"""Noticing that a turn goes in circles (#873, D66).

A turn has no step limit, so the server watches for no progress instead. Two things stop it:

* the same call getting the same result :data:`REPEAT` times **in a row**, or :data:`ERROR_STREAK`
  failed calls in a row with the same error;
* a call that changes something (a write, a live setting, a start or stop) repeated with the same
  arguments and the same result :data:`REPEAT` times **anywhere in the turn**, even with look-ups
  between them. That also catches A → B → A → B: the same change comes back and is counted again.
  Read-only calls (``READ_ONLY``) are observations, not progress: they neither reset that count
  nor count themselves. Without this, interleaving ``get_instrument_state`` with a repeated
  ``set_live_setting`` hid the loop (a turn went 8 calls round without tripping).

When it trips, the turn ends with the reason in words; what the turn wrote can be restored with
"restore to before the last turn".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

REPEAT = 3
ERROR_STREAK = 4

#: Looking at things: no bench, file or session changes, so they are not progress.
READ_ONLY = frozenset(
    {
        "list_catalog",
        "get_definition",
        "list_benches",
        "read_bench",
        "validate_bench",
        "describe_session",
        "get_instrument_state",
        "read_definition",
        "validate_definition",
        "run_cases",
        "try_definition",
    }
)


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
        self.mutations: dict[tuple[str, str, str], int] = {}

    def record(self, tool: str, args: Any, ok: bool, outcome: Any) -> None:
        """Note one call and its outcome (a result or an error text); raise :class:`Stuck` when the
        turn is going round."""
        key = (tool, _digest(args), _digest(outcome))
        if key == self.last:
            self.repeats += 1
        else:  # a different call or result: the consecutive count starts over
            self.last, self.repeats = key, 1
        if ok:
            self.errors.clear()
        else:
            self.errors.append(str(outcome))
        what = f"：{outcome}" if not ok else ""
        if self.repeats >= REPEAT:
            raise Stuck(f"同一个操作（{tool}）连续 {REPEAT} 次得到同样的结果，没有进展，已停下{what}")
        if tool not in READ_ONLY:
            seen = self.mutations.get(key, 0) + 1
            self.mutations[key] = seen
            if seen >= REPEAT:  # look-ups in between do not hide the same change made again
                raise Stuck(
                    f"同一个操作（{tool}）在一轮里重复 {REPEAT} 次得到同样的结果，没有进展，已停下{what}"
                )
        streak = self.errors[-ERROR_STREAK:]
        if len(streak) == ERROR_STREAK and len(set(streak)) == 1:
            raise Stuck(f"连续 {ERROR_STREAK} 次遇到同一个错误，没有进展，已停下：{streak[0]}")
