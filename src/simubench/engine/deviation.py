"""Drawing individual deviations (个体偏差) from the bench seed (metrology-authoring.md 7).

Each value is a pure function of (seed, instance, component, parameter, table cell): the same bench
and seed always give the same unit, whatever else the bench holds or the order things are built in.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from numbers import Real
from typing import TYPE_CHECKING, Any

import numpy as np

from ..models.deviation import DeviationRange, limit_for
from .noise import gaussian, stream_key, uniform

if TYPE_CHECKING:
    from .component import Component
    from .instrument import Instrument

#: Draws tried for a truncated normal before giving up on staying inside the limit (never reached
#: in practice: each draw is inside with probability ≥ 0.68 for k ≥ 1).
_TRIES = 64


def draw_offset(rng: DeviationRange, seed: int, *who: object, limit: float | None = None) -> float:
    """One deviation d within ±limit, in the limit's own terms (see :func:`apply`). ``limit``
    given: that limit, not the range's own (a band edge takes the tighter of its two bands)."""
    if limit is None:
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


def draw_curve(rng: DeviationRange, nominal: Any, seed: int, *who: object) -> list[list[float]]:
    """A curve parameter's value for this unit (``rng.bands``): [[frequency Hz, value], …].

    Each band gets a value at both edges and at ``rng.points`` frequencies between them (evenly
    spaced in log frequency), each drawn within the band's ±limit; an edge two bands share is one
    node, within the tighter of the two limits. Straight lines between the nodes of one band are
    convex combinations, so the whole band stays inside its limit. The reference frequency is a
    node at 0. Between two bands the deviation goes straight from one band's edge value to the
    next (#1010); beyond the outer bands it is 0, a step at their edges (the data sheet limits
    only the bands). The nominal curve (0 where it has no table) is added node by node, its own
    nodes included."""
    bands = rng.bands or []
    limits = [limit_for(rng, i if isinstance(rng.limit, list) else None) or 0.0 for i in range(len(bands))]
    nodes: dict[float, float] = {}
    for i, (lo, hi) in enumerate(bands):
        if not (i > 0 and bands[i - 1][1] == lo):  # a shared edge was drawn with the band below
            nodes[lo] = draw_offset(rng, seed, *who, "band", i, "lo", limit=limits[i])
        for j, f in enumerate(np.geomspace(lo, hi, rng.points + 2)[1:-1]):
            nodes[float(f)] = draw_offset(rng, seed, *who, "band", i, j, limit=limits[i])
        joins = i + 1 < len(bands) and bands[i + 1][0] == hi
        edge = min(limits[i], limits[i + 1]) if joins else limits[i]
        nodes[hi] = draw_offset(rng, seed, *who, "band", i, "hi", limit=edge)
    if rng.reference is not None and any(lo <= rng.reference <= hi for lo, hi in bands):
        nodes[rng.reference] = 0.0
    xs = np.array(sorted(nodes))
    ds = np.array([nodes[x] for x in xs])
    rows = [(float(r[0]), float(r[1])) for r in nominal or ()]
    nx, nv = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    freqs = np.union1d(xs, nx)
    inside = (freqs >= bands[0][0]) & (freqs <= bands[-1][1])
    values = np.where(inside, np.interp(freqs, xs, ds), 0.0)
    if rows:
        values = values + np.interp(freqs, nx, nv, left=0.0, right=0.0)
    return [[float(f), float(v)] for f, v in zip(freqs, values, strict=True)]


def draw(rng: DeviationRange, nominal: Any, seed: int, *who: object) -> Any:
    """A parameter's value for this unit: a number, the nested table ``rng.by`` describes, or
    the curve ``rng.bands`` describes."""
    if rng.bands:
        return draw_curve(rng, nominal, seed, *who)
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


# ---- the override write protocol (#462) -------------------------------------------------------------
#
# Every write of ``comp.overrides`` outside construction — the session's hidden state, the drawn
# deviations, faults, ``Session.set_parameter`` — goes through the primitives below, so the
# priority hidden > drawn deviation > ``$state`` binding is one place's business, not four.


def check_value(comp: Component, parameter: str, value: Any, path: str | None = None) -> None:
    """A written value must fit the parameter's declared type — the type of its default (#495):
    numbers must be finite, and a numeric or string parameter also accepts a per-cell table (a
    deviation table) whose every leaf fits. A default that declares no type constrains nothing:
    ``None`` (rfgen's ``manner``), and a ``$state`` binding — a ``"$path"`` string or a mapping —
    unless the parameter declares a unit, which makes it a finite number (the scope trigger's
    ``level`` binds to a setting but is volts)."""
    default = comp.defaults.get(parameter)

    def reject(expected: str, *, path: str | None = path) -> None:
        where = path if path is not None else f"{getattr(comp, 'name', type(comp).__name__)}.{parameter}"
        raise ValueError(f"{where} expects {expected}, got {value!r}")

    binding = default is None or (isinstance(default, str) and default.startswith("$"))
    if binding and parameter in getattr(comp, "units", {}):
        if not (isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)):
            reject("a finite number")
        return
    if binding:
        return
    if isinstance(value, dict) and not isinstance(default, dict):
        base = path if path is not None else f"{getattr(comp, 'name', type(comp).__name__)}.{parameter}"
        for key, leaf in value.items():
            check_value(comp, parameter, leaf, path=f"{base}[{key!r}]")
        return
    if isinstance(default, bool):
        ok = isinstance(value, (bool, np.bool_))
        expected = "a bool"
    elif isinstance(default, (int, float)):
        # numbers.Real takes the numpy scalars: np.int64 is not an int and np.float32 is not a
        # float, but a drawn or computed value is exactly that (#501).
        expected = "a finite number"
        ok = isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    elif isinstance(default, str):
        expected, ok = "a string", isinstance(value, str)
    elif isinstance(default, (list, tuple)):
        expected, ok = "a list", isinstance(value, (list, tuple))
    elif isinstance(default, dict):
        expected, ok = "a table", isinstance(value, dict)
    else:
        return  # no declared shape to check against
    if not ok:
        reject(expected)


def set_override(comp: Component, parameter: str, value: Any, *, invalidate: bool = True) -> None:
    """Write one hidden override: validated against the component's parameters and the value
    against the parameter's declared type, written, and the session told (the signals may
    change). Construction-time writers pass ``invalidate=False`` — nothing has been solved yet,
    and the session is still being built."""
    known = set(comp._params) | set(comp.defaults)
    if parameter not in known:
        raise ValueError(
            f"{comp.name} has no parameter {parameter!r}; parameters: {', '.join(sorted(known))}"
        )
    check_value(comp, parameter, value)
    comp.overrides[parameter] = value
    session = getattr(comp, "session", None)
    if invalidate and session is not None:
        session.invalidate(electrical=True)


def clear_override(comp: Component, parameter: str) -> None:
    """Undo an override: the parameter reads its binding or default again."""
    comp.overrides.pop(parameter, None)
    session = getattr(comp, "session", None)
    if session is not None:
        session.invalidate(electrical=True)


def draw_overrides(
    comp: Component, ranges: Iterable[tuple[str, DeviationRange]], seed: int, *who: object
) -> dict[str, Any]:
    """Draw the overrides a deviation table describes and write them, skipping the parameters an
    explicit ``hidden`` already occupies (metrology-authoring.md 7: hidden beats the draw).
    Construction-time: nothing is invalidated. ``*who`` is the caller's stream prefix; the
    component and parameter names join it, exactly as the draws have always been keyed. Returns
    ``{parameter: value}`` for the caller's record."""
    drawn: dict[str, Any] = {}
    for parameter, rng in ranges:
        if parameter in comp.overrides:
            continue
        value = draw(rng, comp.param(parameter), seed, *who, comp.name, parameter)
        comp.overrides[parameter] = value
        drawn[parameter] = value
    return drawn


def deviation_keys(inst: Instrument, component: str, parameter: str) -> list[str]:
    """The deviation-record keys of one spec: ``'<component>.<parameter>'``, or one
    ``'ch<n>.<component>.<parameter>'`` per channel when the component is a channel component.
    The one place the ``ch<n>`` rule lives (#462)."""
    if component in {c.name for c in inst.definition.channel_components}:
        return [f"ch{n}.{component}.{parameter}" for n in range(1, inst.channel_count + 1)]
    return [f"{component}.{parameter}"]
