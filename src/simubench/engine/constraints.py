"""Settings an instrument does not allow together (#1013, D69).

A definition's ``constraints`` say which values of some settings exclude values of others, and
whether the instrument refuses the change or changes the other setting. They are checked on the
state a change would leave, so the order in which the settings were set does not matter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models.definition import ConstraintSpec, StateSpec
from ..scpi.errors import ScpiError
from .settings import validate_value


def _canonical(spec: StateSpec | None, value: Any) -> Any:
    """A constraint's value in the form the state stores it (an enum's option, a float)."""
    if spec is None:
        return value
    try:
        return validate_value(spec.model_copy(update={"readonly": False}), value)
    except ScpiError:
        return value


def _values(spec: StateSpec | None, wanted: Any) -> list[Any]:
    return [_canonical(spec, v) for v in (wanted if isinstance(wanted, list) else [wanted])]


def holds(c: ConstraintSpec, specs: Mapping[str, StateSpec], state: Mapping[str, Any]) -> bool:
    """Whether ``state`` breaks the constraint: every ``when`` setting has its value and a
    ``forbid`` setting one of the excluded values."""
    if not all(state.get(p) in _values(specs.get(p), v) for p, v in c.when.items()):
        return False
    return any(state.get(p) in _values(specs.get(p), v) for p, v in c.forbid.items())


def broken(
    constraints: list[ConstraintSpec], specs: Mapping[str, StateSpec], state: Mapping[str, Any]
) -> ConstraintSpec | None:
    """The first constraint ``state`` breaks, or None."""
    return next((c for c in constraints if holds(c, specs, state)), None)


def settle(
    constraints: list[ConstraintSpec],
    specs: Mapping[str, StateSpec],
    state: Mapping[str, Any],
    pending: Mapping[str, Any],
) -> dict[str, Any]:
    """The writes the constraints add to ``pending`` (coerced settings), or ScpiError when the
    state ``pending`` would leave breaks a refusing constraint — or still breaks one after the
    coercions. Nothing is written here."""
    if not constraints:
        return {}
    after = {**state, **pending}
    extra: dict[str, Any] = {}
    for c in constraints:
        if not holds(c, specs, after):
            continue
        if c.action == "refuse":
            raise ScpiError(c.error, _describe(c))
        for p, v in (c.coerce or {}).items():
            value = _canonical(specs.get(p), v)
            extra[p] = value
            after[p] = value
    still = broken(constraints, specs, after)
    if still is not None:
        raise ScpiError(still.error, _describe(still))
    return extra


def _describe(c: ConstraintSpec) -> str:
    when = ", ".join(f"{p} {v}" for p, v in c.when.items())
    forbid = ", ".join(f"{p} {'/'.join(map(str, v))}" for p, v in c.forbid.items())
    return f"settings conflict: {forbid} is not available with {when}"
