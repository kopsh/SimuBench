"""Macros: a component written the earlier, one-component way expands into standard modules when a
definition loads (instrument-authoring.md 8.1). The expanded form is the only implementation; a
definition written the earlier way and one with the modules written out are the same definition.

A component type that is a macro implements ``expand_macro(data, spec)`` as a classmethod: it
rewrites the raw definition data in place (components, channel components, wiring) for its spec,
and does nothing when the modules are already written out.
"""

from __future__ import annotations

import copy
from typing import Any

from ..engine.component import component_type


def expand_macros(data: Any) -> Any:
    """The definition data with every macro expanded (a copy; ``data`` is left as it is).

    Data that is not shaped like a definition is returned unchanged for the schema to report.
    Raises ValueError when a macro cannot expand."""
    if not isinstance(data, dict) or not isinstance(data.get("components"), list):
        return data
    out = copy.deepcopy(data)
    for spec in list(out["components"]):
        if not isinstance(spec, dict) or not isinstance(spec.get("type"), str):
            continue
        try:
            cls = component_type(spec["type"])
        except KeyError:
            continue  # unknown types are the validator's to report
        hook = getattr(cls, "expand_macro", None)
        if hook is not None:
            hook(out, spec)
    return out
