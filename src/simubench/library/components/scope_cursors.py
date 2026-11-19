"""The cursors: the marker modes, their positions and deltas, the tracked Y and the units. Part of ``ScopeMeasurements`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import (
    MathSource,
    OpResult,
)
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .scope import short_source
from .scope_math import function_index
from .scope_measurements_host import MeasurementsHost


class Cursors:
    def __init__(self, host: MeasurementsHost) -> None:
        self.host = host

    def _marker_mode(self) -> str:
        """The marker mode as one of ``MODES``' values, ``off`` when the model has none."""
        word = str(self.host.param("marker_mode") or "").upper()
        for key, mode in self.host.MODES.items():
            if word.startswith(key):
                return mode
        return "off"

    def _marker_record(self, which: int) -> tuple[Any, int]:
        """The record the A (``which`` 1) or B (2) markers read: their source's, a channel's or a
        math function's (#1074). A numberless ``FUNCtion`` — or its alias ``MATH`` (2000 X-series
        Programmer's Guide 02.66 p. 361) — names the one function a single-function layout has."""
        return self.source_record(str(self.host.param(f"marker_source{which}") or ""))

    def source_record(self, word: str) -> tuple[Any, int]:
        """The record a marker's source word names (see ``_marker_record``); empty: channel 1."""
        f = function_index(word) if word else None
        if f is None and word.strip().upper() in ("FUNCTION", "FUNC", "F", "MATH"):
            f = 1
        if f is not None:
            block = self.host.ref("math")
            if block is None or not isinstance(block, MathSource):
                raise ScpiError(-224, f"{word}: this model has no math functions to mark")
            return block.read_function(f)  # type: ignore[return-value]
        n = self.host.channel_of(None, word.encode() if word else None)
        return self.host.acq().measure_record(n)  # type: ignore[return-value]

    def value_at(self, rec: Any, x: float) -> float:
        """The record's value at x (its grid's unit — seconds, or Hz on a transform), the sample
        nearest x: the markers of the real instrument read the point the screen draws."""
        if rec.points < 1:
            return math.nan
        k = (x - rec.origin) / rec.dt_s
        i = int(min(max(round(k), 0), rec.points - 1))
        return float(rec.span(i, i + 1)[0])

    def _marker_y(self, which: int) -> tuple[float, int]:
        """The A (1) or B (2) marker's y: in the manual modes its setting, in the waveform mode
        the value its source's record has at the marker's x; the measurement mode tracks the
        measurement ``marker_measurement`` names."""
        mode = self._marker_mode()
        if mode in ("off", "x_only"):
            return math.nan, 0
        if mode == "measurement":
            n = self.host.param("marker_measurement")
            if n in (None, ""):
                # no tracked-measurement command: the cursors track the most recent measurement
                # on screen (the 2000 X-series semantics, Programmer's Guide 02.66 p. 359)
                if not self.host.results.installed:
                    return math.nan, 0
                what, channel = self.host.results.installed[-1]
                return self.host.values.value(what, channel)
            which_meas = int(n)
            if 1 <= which_meas <= len(self.host.results.installed):
                what, channel = self.host.results.installed[which_meas - 1]
                return self.host.values.value(what, channel)
            return math.nan, 0
        if mode == "waveform":
            rec, used = self._marker_record(which)
            x = self.host.param(f"marker_x{which}")
            return self.value_at(rec, float(x) if x is not None else 0.0), used
        y = self.host.param(f"marker_y{which}")  # manual and y_only: the marker is its setting
        return (float(y) if y is not None else math.nan), 0

    def _marker_word(self, mode: str) -> str:
        """The SCPI word a mode's setting carries, as the definition's options spell it."""
        assert self.host.owner is not None
        path = self.host.bound_path("marker_mode")
        if path is None:
            return mode
        for option in self.host.owner.specs[path].options or []:
            if option.upper().startswith(mode[:3]):
                return str(option)
        return mode

    def cursor(self, which: str = "STARt", query: bool = True, **_: Any) -> OpResult:
        assert self.host.owner is not None
        word = str(which).strip().upper()
        if not word.startswith(("DEL", "STA", "STO")):
            raise ScpiError(-224, f"{which}: DELTa, STARt or STOP")
        mode_path = self.host.bound_path("marker_mode")
        if self._marker_mode() != "waveform" and mode_path is not None:
            self.host.owner.set_state(mode_path, self._marker_word("WAV"), source="scpi", internal=True)
        if word.startswith("DEL"):
            x1, x2 = (float(self.host.param(k) or 0.0) for k in ("marker_x1", "marker_x2"))
            ya, yb, used = self._marker_ys()
            return OpResult(b",".join(self.host.owner.format_number(v) for v in (x2 - x1, yb - ya)), used)
        n = 1 if word.startswith("STA") else 2
        y, used = self._marker_y(n)
        x = float(self.host.param(f"marker_x{n}") or 0.0)
        return OpResult(b",".join(self.host.owner.format_number(v) for v in (x, y)), used)

    def x_delta(self, query: bool = True, **_: Any) -> OpResult:
        assert self.host.owner is not None
        x1, x2 = (float(self.host.param(k) or 0.0) for k in ("marker_x1", "marker_x2"))
        shown = self._marker_diff("x", x1, x2)
        if not math.isfinite(shown):
            return OpResult(str(self.host.param("invalid_value")).encode())
        return OpResult(self.host.owner.format_number(shown))

    def y_delta(self, query: bool = True, **_: Any) -> OpResult:
        assert self.host.owner is not None
        ya, yb, used = self._marker_ys()
        value = self._marker_diff("y", ya, yb)
        if not math.isfinite(value) and self.host.param("invalid_value") != "UNDEF":
            return OpResult(str(self.host.param("invalid_value")).encode(), used)
        return OpResult(self.host.owner.format_number(value), used)

    def _marker_ys(self) -> tuple[float, float, int]:
        """Both markers' y and the virtual time reading them took. In the MEASurement mode both
        sit on the one result of the measurement they track: it is taken once, so their
        difference is that result's, not two acquisitions' noise apart."""
        if self._marker_mode() == "measurement":
            y, used = self._marker_y(1)
            return y, y, used
        ya, ua = self._marker_y(1)
        yb, ub = self._marker_y(2)
        return ya, yb, ua + ub

    def _marker_units(self, axis: str) -> str:
        """The X or Y cursors' unit as this block spells it, from the setting's word."""
        word = str(self.host.param(f"marker_{axis}_units") or "").upper()
        table = self.host.XUNITS if axis == "x" else self.host.YUNITS
        for key, unit in table.items():
            if word.startswith(key):
                return unit
        return table[next(iter(table))]  # the first: seconds, or base units

    def _marker_scaled(self, axis: str, value: float) -> float:
        """A position in the axis's own units expressed in its cursors' unit: the degrees or
        percent from the unit's 0 anchor (:MARKer:XUNits:USE, :MARKer:YUNits:USE set the
        anchors); without anchors the value passes through (the unit has nothing to scale
        against). A difference between two positions scales the same way without the anchor's
        offset — see ``_marker_diff``."""
        if self._marker_units(axis) not in ("deg", "pct"):
            return value
        a1 = self.host.param(f"marker_{axis}a1")
        a2 = self.host.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return value
        span = float(a2) - float(a1)
        full = 360.0 if self._marker_units(axis) == "deg" else 100.0
        return full * (value - float(a1)) / span

    def _marker_diff(self, axis: str, one: float, two: float) -> float:
        """The cursors' difference in their unit: the value at each cursor in the unit, minus —
        the 2000 X-series manual's Xdelta/Ydelta (degrees and percent scale the difference)."""
        unit = self._marker_units(axis)
        if unit == "s" or unit == "base":
            return two - one
        if unit == "hz":
            if one <= 0.0 or two <= 0.0:
                return math.nan
            return 1.0 / two - 1.0 / one
        a1 = self.host.param(f"marker_{axis}a1")
        a2 = self.host.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return two - one
        span = float(a2) - float(a1)
        full = 360.0 if unit == "deg" else 100.0
        return full * (two - one) / span

    def op_x_position(self, which: str = "X1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        return self._position(which, value, query, "x")

    def op_y_position(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        return self._position(which, value, query, "y")

    def op_tracked_y(self, which: str = "Y1", value: Any = None, query: bool = False, **_: Any) -> OpResult:
        assert self.host.owner is not None
        n = 1 if str(which).strip().upper().endswith("1") else 2
        param = f"marker_y{n}"
        if query:
            if self._marker_mode() in ("waveform", "measurement"):
                shown, used = self._marker_y(n)
                if not math.isfinite(shown):
                    return OpResult(str(self.host.param("invalid_value")).encode(), used)
                return OpResult(self.host.owner.format_number(shown), used)
            stored = self.host.param(param)
            return OpResult(self.host.owner.format_number(float(stored) if stored is not None else 0.0))
        path = self.host.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.host.uid}: the {param} setting is not bound")
        self.host.owner.set_state(path, self._marker_number("y", value), source="scpi", internal=True)
        return OpResult()

    def _position(self, which: str, value: Any, query: bool, axis: str) -> OpResult:
        """One X or Y cursor's read or write, in the cursors' unit, with the mode moves the
        manual gives a position write (MANual unless tracking; a tracking Y is refused)."""
        assert self.host.owner is not None
        n = 1 if str(which).strip().upper().endswith("1") else 2
        param = f"marker_{axis}{n}"
        mode_path = self.host.bound_path("marker_mode")
        mode = self._marker_mode()
        if query:
            if mode == "off":
                raise ScpiError(-222, "the cursors are off: their positions are not defined")
            used = 0
            if axis == "y" and mode in ("waveform", "measurement"):
                shown, used = self._marker_y(n)
                if not math.isfinite(shown) and self.host.param("invalid_value") != "UNDEF":
                    return OpResult(str(self.host.param("invalid_value")).encode(), used)
            else:
                shown = float(self.host.param(param) if self.host.param(param) is not None else 0.0)
            unit = self._marker_units(axis)
            if unit == "hz":
                if shown <= 0.0:
                    return OpResult(str(self.host.param("invalid_value")).encode(), used)
                shown = 1.0 / shown
            elif unit in ("deg", "pct"):
                shown = self._marker_scaled(axis, shown)
            return OpResult(self.host.owner.format_number(shown), used)
        if axis == "y" and mode == "waveform":
            raise ScpiError(-222, "the cursors track the waveform: Y positions cannot be set")
        if mode != "waveform" and mode_path is not None:
            self.host.owner.set_state(mode_path, self._marker_word("MAN"), source="scpi", internal=True)
        path = self.host.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.host.uid}: the {param} setting is not bound")
        number = self._marker_number(axis, value)
        self.host.owner.set_state(path, number, source="scpi", internal=True)
        return OpResult()

    def _marker_number(self, axis: str, value: Any) -> float:
        """A cursor position write in the cursors' unit, stored in the axis's own units (seconds
        or volts): Hz is its inverse, degrees and percent ride the anchors (:UNits:USE)."""
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if value is None:
            raise ScpiError(-109)
        text = value.decode("ascii", errors="replace") if isinstance(value, bytes) else str(value)
        v = float(V.parse_number(text))
        unit = self._marker_units(axis)
        if unit == "s" or unit == "base":
            return v
        if unit == "hz":
            if v <= 0.0:
                raise ScpiError(-222, f"{v:g} Hz: a cursor's frequency is positive")
            return 1.0 / v
        a1 = self.host.param(f"marker_{axis}a1")
        a2 = self.host.param(f"marker_{axis}a2")
        if a1 is None or a2 is None or float(a2) == float(a1):
            return v  # nothing to scale against: the value passes through (the model's convention)
        full = 360.0 if unit == "deg" else 100.0
        return float(a1) + (float(a2) - float(a1)) * v / full

    def op_units_use(self, axis: str = "X", **_: Any) -> OpResult:
        assert self.host.owner is not None
        axis = axis.strip().lower()
        if axis not in ("x", "y"):
            raise ScpiError(-224, f"{axis}: X or Y")
        one = float(self.host.param(f"marker_{axis}1") or 0.0)
        two = float(self.host.param(f"marker_{axis}2") or 0.0)
        for name, value in ((f"marker_{axis}a1", one), (f"marker_{axis}a2", two)):
            path = self.host.bound_path(name)
            if path is None:
                raise ScpiError(-300, f"{self.host.uid}: the {name} setting is not bound")
            self.host.owner.set_state(path, value, source="scpi", internal=True)
        return OpResult()

    def op_marker_source(
        self, which: str = "X1Y1", value: Any = None, query: bool = False, **_: Any
    ) -> OpResult:
        assert self.host.owner is not None
        param = f"marker_source{2 if str(which).strip().upper().endswith('2') else 1}"
        if query or value is None:
            return OpResult(short_source(self.host.param(param) or "NONE").encode())
        if isinstance(value, list):  # the raw parameter arrives as the unit's parameter list
            value = value[0] if value else None
        if value is None:
            raise ScpiError(-109)
        word = V.parse_string(value if isinstance(value, bytes) else str(value).encode())
        if word.strip().upper() == "MATH":
            word = "FUNCtion"
        path = self.host.bound_path(param)
        if path is None:
            raise ScpiError(-300, f"{self.host.uid}: the {param} setting is not bound")
        self.host.owner.set_state(path, word, source="scpi", internal=True)
        return OpResult()
