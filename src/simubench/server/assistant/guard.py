"""Noticing that a turn goes in circles (#873, D66).

A turn has no step limit, so the server watches for no progress instead. Two things stop it:

* the same call getting the same result :data:`REPEAT` times **in a row**, or :data:`ERROR_STREAK`
  failed calls in a row with the same error;
* a call that changes something (a write, a live setting, a start or stop) repeated with the same
  arguments and the same result :data:`REPEAT` times **anywhere in the turn**, even with look-ups
  between them. That also catches A → B → A → B: the same change comes back and is counted again.
  The tools that declare themselves observations (``observes``, one line each at their definition
  in :mod:`tools`, #1734) are observations, not progress: they neither reset that count nor count
  themselves. Without this, interleaving ``get_instrument_state`` with a repeated
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


def _observed() -> frozenset[str]:
    """The names of the tools that declared themselves observations, one line each at their
    definition (tools.py, #1734) — read lazily, since tools imports this module. Every tool must
    have declared: an undeclared one would count as progress, and the same legal read three times
    would then stop a healthy turn (a false stop, not a missed check)."""
    from .tools import TOOLS

    undeclared = [f.__name__ for f in TOOLS if not isinstance(getattr(f, "observes", None), bool)]
    assert not undeclared, f"工具没有声明是否只观察：{undeclared}"
    return frozenset(f.__name__ for f in TOOLS if f.observes)


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
        self.observed = _observed()  # the tools' own declaration, checked complete (#1734)

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
        if tool not in self.observed:
            seen = self.mutations.get(key, 0) + 1
            self.mutations[key] = seen
            if seen >= REPEAT:  # look-ups in between do not hide the same change made again
                raise Stuck(
                    f"同一个操作（{tool}）在一轮里重复 {REPEAT} 次得到同样的结果，没有进展，已停下{what}"
                )
        streak = self.errors[-ERROR_STREAK:]
        if len(streak) == ERROR_STREAK and len(set(streak)) == 1:
            raise Stuck(f"连续 {ERROR_STREAK} 次遇到同一个错误，没有进展，已停下：{streak[0]}")
