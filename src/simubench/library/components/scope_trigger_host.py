"""What the ScopeTrigger collaborators ask of the component: the one interface they read it by."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from ...engine.signal import Signal

if TYPE_CHECKING:
    from ...engine.instrument import Instrument
    from ...engine.session import Session
    from .scope_trigger_advanced import Advanced
    from .scope_trigger_crossings import Crossings
    from .scope_trigger_runs import Runs
    from .scope_trigger_sources import Sources


@dataclass
class TriggerHit:
    """Where a trigger search found its edge: the instant (seconds after the search's base
    instant, interpolated between samples) and the first record-grid sample at or after it."""

    t_s: float
    index: int


@dataclass
class _Crossing:
    """A crossing of the trigger level found on a search grid: between the samples at ``before``
    and ``at`` (s), ``estimate`` linear between them (``final``: that is the instant; otherwise
    the grid was coarse and the instant is still to be bisected). ``direction`` None: no crossing,
    only how far the search has looked."""

    direction: str | None
    before: float
    at: float
    estimate: float
    final: bool


def _between(t_p: float, v_p: float, t_k: float, v_k: float, level: float) -> float:
    """Where the line from (t_p, v_p) to (t_k, v_k) meets ``level``, kept within the two instants."""
    frac = (level - v_p) / (v_k - v_p) if v_k != v_p else 1.0
    return t_p + min(max(frac, 0.0), 1.0) * (t_k - t_p)


def _period(sig: Signal) -> float | None:
    """The time after which the signal repeats (its lines are harmonics of one frequency), None
    when it has no line."""
    f = np.unique(sig.freq[(sig.freq > 0) & (np.abs(sig.amp) > 0)])
    if not f.size:
        return None
    g = float(f[0])
    for x in f[1:]:
        a, b = float(x), g
        while b > 1e-9 * float(x):  # Euclid, a remainder within rounding of a multiple is none
            r = a - b * round(a / b)
            a, b = b, (0.0 if abs(r) < 1e-9 * a else a % b)
        g = a
    return 1.0 / g


class TriggerHost(Protocol):
    channel_param: Any

    def choice(self, key: str) -> str | None: ...

    name: Any
    owner: Instrument | None

    def param(self, key: str) -> Any: ...

    session: Session

    @property
    def uid(self) -> str: ...

    sources: Sources
    crossings: Crossings
    runs: Runs
    advanced: Advanced
