"""Drawing individual deviations (个体偏差) from the bench seed (metrology-authoring.md 7).

Each value is a pure function of (seed, instance, component, parameter, table cell): the same bench
and seed always give the same unit, whatever else the bench holds or the order things are built in.
"""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from ..models.deviation import DeviationRange, limit_for
from .noise import gaussian, stream_key, uniform

#: Draws tried for a truncated normal before giving up on staying inside the limit (never reached
#: in practice: each draw is inside with probability ≥ 0.68 for k ≥ 1).
_TRIES = 64


def draw_offset(rng: DeviationRange, seed: int, *who: object) -> float:
    """One deviation d within ±limit, in the limit's own terms (see :func:`apply`)."""
    cell = who[-1] if who else None
    limit = limit_for(rng, cell) if rng.by else limit_for(rng, None)
    assert limit is not None  # checked by the model
    key = stream_key("deviation", seed, *who)
    if rng.distribution == "uniform":
        return float(limit * (2.0 * uniform(key, np.array([0]))[0] - 1.0))
    sigma = limit / rng.coverage
    z = gaussian(key, np.arange(_TRIES)) * sigma
    inside = z[np.abs(z) <= limit]
    return float(inside[0]) if inside.size else 0.0


def apply(rng: DeviationRange, nominal: float, d: float) -> float:
    """The unit's value: relative (% or ppm of the nominal) or added in the parameter's unit."""
    if rng.unit == "%":
        return nominal * (1.0 + d * 1e-2)
    if rng.unit == "ppm":
        return nominal * (1.0 + d * 1e-6)
    return nominal + d


def _cell_nominal(nominal: Any, path: tuple[Any, ...]) -> float:
    """The nominal value of one table cell: a nested table's entry, or the same number for all."""
    value = nominal
    for key in path:
        if not isinstance(value, dict):
            break
        hit = next((v for k, v in value.items() if k == key or str(k) == str(key)), None)
        value = 0.0 if hit is None else hit
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def draw(rng: DeviationRange, nominal: Any, seed: int, *who: object) -> Any:
    """A parameter's value for this unit: a number, or the nested table ``rng.by`` describes."""
    if not rng.by:
        return apply(rng, _cell_nominal(nominal, ()), draw_offset(rng, seed, *who))
    keys = list(rng.by)
    table: dict[Any, Any] = {}
    for path in itertools.product(*(rng.by[k] for k in keys)):
        node = table
        for key in path[:-1]:
            node = node.setdefault(key, {})
        d = draw_offset(rng, seed, *who, *path)
        node[path[-1]] = apply(rng, _cell_nominal(nominal, path), d)
    return table
