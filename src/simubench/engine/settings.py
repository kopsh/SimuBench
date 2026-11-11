"""An instrument's settings: their declared specs, their current values, and what moves them (#895).

``specs`` is the definition's ``state`` (per channel, with the bench's overrides); ``values`` is the
current value of each. The store owns the rules that need nothing of a running instrument: coercing
a value to its spec's type, returning every setting to its ``*RST`` / power-on value, moving a
numeric setting's bounds at run time and restoring them. Reacting to a change (watchers, events,
rollback) stays with the instrument.
"""

from __future__ import annotations

import math
from typing import Any

from ..models.definition import InstrumentDefinition, StateSpec
from ..scpi import values as V
from ..scpi.errors import ScpiError

_LADDERS = {"1-2-5": (1.0, 2.0, 5.0), "1-3": (1.0, 3.0), "1-2-4": (1.0, 2.0, 4.0), "1-10": (1.0,)}


def snap_ladder(value: float, ladder: str | list[float], lo: float | None, hi: float | None) -> float:
    """Snap ``value`` to the nearest rung on a log scale."""
    if value <= 0:
        return lo if lo is not None else value
    if isinstance(ladder, list):
        rungs = sorted(ladder)
    else:
        mant = _LADDERS[ladder]
        k0 = math.floor(math.log10(value))
        rungs = [m * 10.0**k for k in range(k0 - 1, k0 + 2) for m in mant]
    if lo is not None:
        rungs = [r for r in rungs if r >= lo * (1 - 1e-12)]
    if hi is not None:
        rungs = [r for r in rungs if r <= hi * (1 + 1e-12)]
    if not rungs:
        return value
    return min(rungs, key=lambda r: abs(math.log10(r) - math.log10(value)))


def validate_value(spec: StateSpec, value: Any) -> Any:
    """``value`` checked against the setting's spec and returned in its stored form (raises the
    SCPI error the instrument answers with: read-only -221, out of range -222, bad option -224)."""
    if spec.readonly:
        raise ScpiError(-221, "read-only setting")
    if spec.type in ("float", "int"):
        v = float(value)
        special_values = set((spec.special or {}).values())
        if v in special_values:
            return v
        if spec.ladder is not None:
            v = snap_ladder(v, spec.ladder, spec.min, spec.max)
        # The relative tolerance widens the range on both signs (#407: a minimum of -5 V must
        # accept -5; min * (1 - 1e-12) would move a negative bound inwards).
        if math.isnan(v) or (spec.min is not None and v < spec.min - abs(spec.min) * 1e-12 - 1e-300):
            raise ScpiError(-222)
        if spec.max is not None and v > spec.max + abs(spec.max) * 1e-12 + 1e-300:
            raise ScpiError(-222)
        return round(v) if spec.type == "int" else v
    if spec.type == "enum":
        if value not in (spec.options or []):
            # A value written outside the parse layer (a definition's `writes`/`sets` constant,
            # the UI's configure) may name the option in any SCPI word form, as a word
            # arriving over SCPI does (#625): the option itself is stored.
            canonical = V.fit_enum(value, spec.options or [])
            if canonical is None:
                raise ScpiError(-224)
            value = canonical
        return value
    if spec.type == "bool":
        return bool(value)
    if spec.type == "enum_list":
        # A set of options kept in the options' order, whatever order it was written in
        # (#252: :FORM:ELEM:SENS answers voltage, current, … however the list was given).
        chosen = set(value)
        if not chosen or not chosen <= set(spec.options or []):
            raise ScpiError(-224)
        return [o for o in spec.options or [] if o in chosen]
    if spec.type == "int_list":
        out = [int(v) for v in value]
        for v in out:
            if spec.min is not None and v < spec.min:
                raise ScpiError(-222)
            if spec.max is not None and v > spec.max:
                raise ScpiError(-222)
        return out
    return str(value)


class SettingStore:
    def __init__(self, definition: InstrumentDefinition, channel_count: int, name: str) -> None:
        self.name = name
        self.specs: dict[str, StateSpec] = {}
        for key, spec in definition.state.items():
            self.specs[key] = spec
        for n in range(1, channel_count + 1):
            over = definition.channels.overrides.get(n, {})
            for key, spec in definition.channel_state.items():
                patch = over.get(key)
                self.specs[f"ch{n}.{key}"] = spec.model_copy(update=patch) if patch else spec
        #: The definition's spec of a setting whose bounds a component moved at run time.
        self._origin: dict[str, StateSpec] = {}
        self.values: dict[str, Any] = {}

    def coerce(self, path: str, value: Any) -> Any:
        """``value`` as the type the setting's spec declares."""
        spec = self.specs[path]
        if spec.type == "enum":  # "" is an enum not set yet (an optional value a query leaves out)
            return "" if value == "" else V.parse_enum(str(value).encode(), spec.options or [])
        if spec.type == "float":
            return float(value)
        if spec.type == "int":
            return int(value)
        if spec.type == "bool":
            return bool(value)
        return value

    def reset(self, power_on: bool = False) -> None:
        """Every setting to its ``*RST`` value, or at power-on to its ``default`` (the power-on
        value, which differs from ``rst`` when the manual says so: the X-Series powers on sweeping
        continuously, *RST stops it, #616)."""
        for path, spec in self.specs.items():
            value = spec.default if power_on or spec.rst is None else spec.rst
            if value is None:
                value = {"float": 0.0, "int": 0, "bool": False, "string": ""}.get(spec.type)
                if spec.type == "enum":
                    value = (spec.options or [""])[0]
            self.values[path] = self.coerce(path, value) if value is not None else value

    def get(self, path: str) -> Any:
        try:
            return self.values[path]
        except KeyError:
            raise KeyError(f"instrument {self.name!r} has no state {path!r}") from None

    def set_bounds(self, path: str, *, minimum: float | None = None, maximum: float | None = None) -> None:
        """Move a numeric setting's limits at run time, relative to the definition's: a combination
        that widens a channel's range (#259: the E36312A's series pair programs output 2 to 50 V).
        Validation, MIN/MAX and the state view follow; ``None`` keeps the definition's limit."""
        origin = self._origin.setdefault(path, self.specs[path])
        update = {
            "min": origin.min if minimum is None else minimum,
            "max": origin.max if maximum is None else maximum,
        }
        self.specs[path] = origin.model_copy(update=update)

    def restore_bounds(self, path: str) -> None:
        """The definition's limits again (see :meth:`set_bounds`)."""
        origin = self._origin.pop(path, None)
        if origin is not None:
            self.specs[path] = origin
