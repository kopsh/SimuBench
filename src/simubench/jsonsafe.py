"""The one spelling of the numbers JSON cannot carry, forward and back (#628).

JSON has no infinity and no NaN. Wherever a SimuBench number reaches JSON — the run record and
the event stream (:func:`simubench.engine.record.jsonable`, the one helper since #595), MCP
results, bench file echoes — a non-finite float is spelled the bench files' way: ``"INF"``,
``"-INF"`` or ``"NAN"``. Clients (Web, TUI) read the same spellings back, so the interface layer
shares this knowledge with the engine instead of copying it: this module imports nothing.

#595's JSON-safe convergence left the converter (bytes, datetimes, numpy scalars, recursion) in
``engine/record.py``; only the spelling lives here.
"""

from __future__ import annotations

import math


def spelling(value: float) -> str:
    """How a non-finite float is spelled: ``"INF"``, ``"-INF"`` or ``"NAN"``."""
    return "NAN" if math.isnan(value) else "INF" if value > 0 else "-INF"


#: Spelling -> the float it means. Derived from :func:`spelling`, so the words are defined once.
SPECIAL_NUMBERS: dict[str, float] = {spelling(v): v for v in (math.inf, -math.inf, math.nan)}


def as_number(value: object) -> object:
    """A value as JSON carried it: ``"INF"`` / ``"-INF"`` / ``"NAN"`` back as floats, anything
    else unchanged."""
    if isinstance(value, str) and value.upper() in SPECIAL_NUMBERS:
        return SPECIAL_NUMBERS[value.upper()]
    return value
