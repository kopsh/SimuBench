"""An instrument's settings: their declared specs, their current values, and what moves them (#895).

``specs`` is the definition's ``state`` (per channel, with the bench's overrides); ``values`` is the
current value of each. The store owns the rules that need nothing of a running instrument: coercing
a value to its spec's type, returning every setting to its ``*RST`` / power-on value, moving a
numeric setting's bounds at run time and restoring them. Reacting to a change (watchers, events,
rollback) stays with the instrument.
"""

from __future__ import annotations

from typing import Any

from ..models.definition import InstrumentDefinition, StateSpec
from ..scpi import values as V


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
