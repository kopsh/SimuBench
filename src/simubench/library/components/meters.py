"""Meters: a digital multimeter's voltage functions, built from its real measurement chain (M5).

Since #190 (E1) the multimeter is three blocks (D50, component-map.md multimeter):
``dmm_input_terminal`` (the electrical terminal and the input impedance that reaches the circuit),
``dmm_conditioning`` (function, ranging, the AC filter and the analog errors) and ``dmm_adc``
(digits, NPLC reading time, resolution, calibration and the reading). The blocks are wired
``terminal.out -> conditioning.in``; the calibration store sits beside the ADC.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from ...engine.circuit import GROUND, Circuit
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...engine.signal import Signal
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: Calibration errors (positive device codes, as on real meters).
CAL_SECURED = 701
CAL_INPUT = 702


def _flag(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().upper() in ("1", "ON", "TRUE")


@component("dmm_input_terminal")
class DmmInputTerminal(Component):
    """The DMM's input terminals (#190 E1): the electrical input and its impedance to ground.

    ``pin`` is the instrument's electrical terminal (a definition exposes it as ``IN``); ``out``
    carries the measured signal on to the conditioning block. With ``z_in`` the terminal models its
    own input stage, so the resistance enters the bench circuit: ``z_in_high`` on the
    ``high_z_ranges`` when ``high_z`` is set. Without ``z_in`` the terminal is a pass-through and
    an ``input_port`` in front owns the electrical side.

    The high-impedance choice follows the range in use, which the conditioning block knows; the
    terminal references it (``conditioning``).
    """

    refs = {"conditioning": ("dmm_conditioning", "instrument")}
    ports = {
        "pin": PortSpec("in", "electrical", "the instrument's input terminal"),
        "out": PortSpec("out", "signal", "the measured signal, to the conditioning block"),
    }
    units = {"z_in": "OHM", "z_in_high": "OHM"}
    defaults = {
        "z_in": None,  # input resistance the terminal puts on the net itself, ohm (None: the wiring's)
        "z_in_high": None,  # input resistance when high_z applies, ohm
        "high_z": False,  # high input impedance (a setting)
        "high_z_ranges": [],  # the ranges on which high_z applies (full-scale values)
        "conditioning": "",  # the conditioning block whose range decides high_z
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stamped_z: float | None = None  # the input impedance currently in the circuit

    def _conditioning(self) -> DmmConditioning:
        assert self.owner is not None
        return self.owner.components[str(self.param("conditioning"))]  # type: ignore[return-value]

    def signal(self) -> Signal:
        """The signal the meter measures: the net on its own terminal when it models the input
        stage, else the signal wired to it."""
        if self.param("z_in") is not None and self.session.port_ref(self, "pin") is not None:
            return self.pin_signal("pin")
        return self.input("pin")

    def output(self, port: str) -> Signal:
        return self.signal()

    def effective_z_in(self, *, derive: bool = True) -> float | None:
        """The input resistance the terminal itself puts on its net (None: it does not model one)."""
        z = self.param("z_in")
        if z is None:
            return None
        high = self.param("z_in_high")
        if high is not None and _flag(self.param("high_z")):
            conditioning = self._conditioning()
            r = conditioning.effective_range(conditioning.active_function(), derive=derive)
            if r is not None and r in [float(x) for x in self.param("high_z_ranges") or []]:
                return float(high)
        return float(z)

    def _input_changed(self) -> None:
        """Re-stamp the circuit when a high-z change moved the terminal's input impedance."""
        z = self.effective_z_in(derive=False)
        if z == self._stamped_z:
            return
        self.session.invalidate(electrical=True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        self._input_changed()

    def stamp(self, circuit: Circuit, node: Callable[[str], str]) -> None:
        if self.param("z_in") is None or self.session.port_ref(self, "pin") is None:
            return  # no input stage modeled here: an input_port in front owns the electrical side
        z = self.effective_z_in(derive=False)
        assert z is not None
        self._stamped_z = z
        circuit.resistor(node("pin"), GROUND, z)


@component("dmm_conditioning")
class DmmConditioning(Component):
    """Signal conditioning (#190 E1): function, ranges and the AC filter, and the analog errors.

    DC (mean) and true-rms AC of the signal on its input; AC is the rms of the lines above
    ``ac_low`` Hz (the AC path's coupling). The measurement path has a hidden gain error (ppm) and
    offset (V), the instrument's own errors; the ADC's calibration coefficients correct them::

        raw     = true × (1 + gain_error_ppm·1e-6) + offset
        reading = (raw − cal.offset) × cal.gain

    With ``ranges`` the meter ranges (metrology-authoring.md 2): the function's full-scale values,
    a manual ``range`` (a value the range must be able to measure; the smallest fitting range is
    used, as SCPI prescribes) and ``autorange`` (the range follows the true value; above
    ``autorange_up`` × full scale or below ``autorange_down`` × it, the smallest range that fits
    the value is taken). The hidden errors may be one number (every range alike) or per function
    and range: ``{DC: {0.1: 8, 1: 3}, AC: 60}``.
    """

    ports = {"in": PortSpec("in", "signal", "the signal from the input terminal")}
    defaults = {
        "ac_low": 3.0,  # Hz above which lines count into the AC reading
        "function": "DC",  # DC | AC: what a command without one measures; what sample() reads
        "ranges": None,  # full-scale value of each range, per function ({DC: [...]}) or one list
        "range": None,  # manual range: a value the range must be able to measure (None, <= 0: auto)
        "autorange": True,  # the range follows the true value
        "autorange_up": 1.2,  # fraction of full scale above which the range switches up
        "autorange_down": 0.1,  # fraction of full scale below which the range switches down
        "gain_error_ppm": 0.0,  # the meter's own gain error, ppm (hidden); per function and range
        "offset": 0.0,  # the meter's own DC offset error, V (hidden); per function and range
    }
    units = {"offset": "V"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._auto_range: dict[str, float] = {}  # each function's auto-chosen range

    # ---- the function and the true value ---------------------------------------------------------

    def active_function(self, what: str | None = None) -> str:
        """The function a measurement asks for, or the meter's own (DC when neither is given)."""
        return what or self.param("function") or "DC"

    def true_values(self) -> dict[str, float]:
        sig = self.input("in").merged()
        ac = sig.amp[sig.freq >= float(self.param("ac_low"))]
        return {"DC": sig.mean(), "AC": math.sqrt(float(np.sum(np.abs(ac) ** 2)) / 2)}

    def raw(self, what: str) -> float:
        """The uncorrected reading: the true value through the meter's own errors."""
        true = self.true_values()[what]
        function = self.active_function(what)
        r = self.effective_range(function)
        gained = true * (1 + self._hidden("gain_error_ppm", function, r) * 1e-6)
        return gained + self._hidden("offset", function, r) if function == "DC" else gained

    def _hidden(self, key: str, function: str, r: float | None) -> float:
        """The meter's own error (``gain_error_ppm``, ``offset``): a number for every range, or a
        number per function, or a number per function and range."""
        v = self.param(key)
        if not isinstance(v, dict):
            return float(v)
        if function not in v:
            return float(self.defaults[key])
        v = v[function]
        if not isinstance(v, dict):
            return float(v)
        if r is None:
            raise ValueError(f"{self.uid}: {key} is per range but the {function} function has no range")
        for k, value in v.items():
            if float(k) == float(r):
                return float(value)
        raise ValueError(f"{self.uid}: {key} has no entry for the {function} range {r}")

    # ---- ranging ---------------------------------------------------------------------------------

    def ranges(self, function: str) -> list[float]:
        """The function's range full-scale values, ascending (empty: the meter does not range)."""
        spec = self.param("ranges")
        if spec is None:
            return []
        if isinstance(spec, dict):
            if function not in spec:
                raise ValueError(f"{self.uid}: ranges have no entry for the {function} function")
            spec = spec[function]
        return sorted(float(x) for x in spec)

    def fitting_range(self, function: str, value: float) -> float | None:
        """The smallest range that can measure ``value`` (the largest range when none can)."""
        ranges = self.ranges(function)
        for r in ranges:
            if r >= abs(value) * (1 - 1e-12):
                return r
        return ranges[-1] if ranges else None

    def range_of_setting(self, function: str) -> float | None:
        """The manual range: the smallest range that can measure the range setting."""
        value = self.param("range")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None  # nothing usable is set: the meter autoranges
        return self.fitting_range(function, value)

    def effective_range(self, function: str, *, derive: bool = True) -> float | None:
        """The range in use: the manual setting's, or the auto one (``derive`` takes it from the
        true value the first time)."""
        if not self.ranges(function):
            return None
        if not _flag(self.param("autorange")):
            return self.range_of_setting(function)
        r = self._auto_range.get(function)
        if r is None and derive:
            self._adopt_range(function, self.fitting_range(function, abs(self.true_values()[function])))
            r = self._auto_range[function]
        return r

    def autorange_to(self, function: str, true: float) -> None:
        """The auto-range decision for a true value: outside the hysteresis band
        ``autorange_down`` .. ``autorange_up`` × the range's full scale, the smallest range that
        fits the value."""
        old = self._auto_range.get(function)
        high = float(self.param("autorange_up")) * old if old is not None else None
        low = float(self.param("autorange_down")) * old if old is not None else None
        if old is None or not low <= abs(true) <= high:
            self._adopt_range(function, self.fitting_range(function, true))

    def _adopt_range(self, function: str, r: float | None) -> None:
        """Take a range as the auto one; the input terminal re-stamps when the range moved (the
        high-impedance choice follows it)."""
        if r == self._auto_range.get(function):
            return
        self._auto_range[function] = r
        self.session.invalidate(electrical=True)

    def on_state(self, path: str, old: Any, new: Any) -> None:
        """A bound setting moved (the range, the function, ...): the input may re-stamp."""
        self.session.invalidate(electrical=True)

    def reset(self) -> None:
        self._auto_range = {}


@component("dmm_adc")
class DmmAdc(Component):
    """The A/D (#190 E1): digits, NPLC reading time, resolution, calibration and the reading.

    It reads the conditioning block's raw value, applies the calibration coefficients and the
    resolution quantization, and turns a reading beyond ``overrange`` × full scale into
    ``overload_value``. Calibration: with ``store`` naming a ``calibration_store`` component the
    reading is corrected with the store's per-key coefficients (metrology-authoring.md 4) and the
    ``cal_*`` operations delegate to it; without a store the single legacy coefficient set below
    applies. The component is the instrument's sampler (metrology-authoring.md 3.1).
    """

    refs = {"conditioning": ("dmm_conditioning", "instrument")}
    defaults = {
        "digits": 7,  # display digits of a reading
        "overrange": 1.2,  # a reading beyond this fraction of full scale is an overload
        "overload_value": 9.9e37,  # the reading an overload produces
        "resolution": None,  # quantization step as a fraction of full scale (a mapping of a setting)
        "reading_time": 0.0,  # s of virtual time one reading takes (integration and overhead)
        "store": "",  # the calibration_store whose per-key coefficients correct the reading
        "cal_gain": 1.0,  # legacy (no store): calibration gain coefficient
        "cal_offset": 0.0,  # legacy (no store): calibration zero (offset) coefficient
        "cal_value": 0.0,  # legacy (no store): the reference a gain calibration assumes on the input
        "cal_secured": True,  # legacy (no store): calibration secured: changing it needs cal_code
        "cal_code": "SIMUBENCH",  # legacy (no store): calibration security code
        "cal_tolerance": 0.1,  # legacy (no store): fraction of the cal value the input may be off by
        "conditioning": "",  # the signal-conditioning block it reads
    }
    units = {"cal_offset": "V", "cal_value": "V"}

    def _conditioning(self) -> DmmConditioning:
        assert self.owner is not None
        return self.owner.components[str(self.param("conditioning"))]  # type: ignore[return-value]

    # The calibration store's interface to the "measuring core": the function being measured and
    # the range in use live in the conditioning block, the calibrated raw value too.

    def active_function(self) -> str:
        return self._conditioning().active_function()

    def raw(self, function: str) -> float:
        return self._conditioning().raw(function)

    def effective_range(self, function: str, *, derive: bool = True) -> float | None:
        return self._conditioning().effective_range(function, derive=derive)

    # ---- the reading -----------------------------------------------------------------------------

    def reading(self, what: str) -> float:
        conditioning = self._conditioning()
        function = conditioning.active_function(what)
        gain, cal_offset = self._cal(function)
        value = (conditioning.raw(function) - (cal_offset if function == "DC" else 0.0)) * gain
        r = conditioning.effective_range(function)
        if r is not None:
            step = float(self.param("resolution") or 0.0) * r
            if step > 0:
                value = round(value / step) * step
            if abs(value) > float(self.param("overrange")) * r:
                return float(self.param("overload_value"))
        return value

    def _store(self) -> Component | None:
        """The calibration_store the meter references, or None (legacy coefficients)."""
        name = str(self.param("store") or "")
        if not name:
            return None
        assert self.owner is not None
        return self.owner.components[name]

    def _cal(self, function: str) -> tuple[float, float]:
        """The (gain, offset) correcting the raw reading: the store's per-key coefficients, or the
        legacy single set. The offset corrects the DC reading only (zero is a DC concept)."""
        store = self._store()
        if store is not None:
            return store.coefficients(function)
        return float(self.param("cal_gain")), float(self.param("cal_offset"))

    def readings(self) -> dict[str, Any]:
        values: dict[str, Any] = {"DC": self.reading("DC"), "AC": self.reading("AC")}
        if self.session.reveal_true_values:
            values.update({f"true_{k}": v for k, v in self._conditioning().true_values().items()})
        return {"values": values}

    # ---- a Sampler for trigger_model (metrology-authoring.md 3.1) ---------------------------------

    def sample_time_ns(self) -> int:
        return round(float(self.param("reading_time")) * 1e9)

    def sample(self, index: int) -> float:
        return self.reading(self._conditioning().active_function())

    @op("measure")
    def op_measure(self, what: str | None = None, **_: Any) -> OpResult:
        conditioning = self._conditioning()
        function = conditioning.active_function(what)
        if _flag(conditioning.param("autorange")) and conditioning.ranges(function):
            conditioning.autorange_to(function, conditioning.true_values()[function])
        return OpResult(V.format_float(self.reading(function), int(self.param("digits"))))

    @op("range")
    def op_range(self, query: bool = False, **_: Any) -> OpResult:
        """The range in use, the auto-chosen one included (the definition's range query)."""
        conditioning = self._conditioning()
        function = conditioning.active_function()
        r = conditioning.effective_range(function)
        if r is None:
            raise ValueError(f"{self.uid}: no ranges configured for the {function} function")
        return OpResult(V.format_float(r, int(self.param("digits"))))

    # ---- calibration -----------------------------------------------------------------------------

    def _set(self, key: str, value: Any) -> None:
        assert self.owner is not None
        self.owner.set_state(key, value, source="scpi", internal=True)

    def _unsecured(self) -> None:
        store = self._store()
        secured = store.is_secured if store is not None else bool(self.param("cal_secured"))
        if secured:
            raise ScpiError(CAL_SECURED, "Calibration error; calibration is secured")

    @op("cal_secure")
    def op_cal_secure(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """:CALibration:SECure:STATe {ON|OFF},<code>: unsecuring needs the security code."""
        store = self._store()
        if store is not None:
            return store.op_secure(query=query, values=values)
        if query:
            return OpResult(V.format_bool(bool(self.param("cal_secured"))))
        params = list(values or [])
        if not params:
            raise ScpiError(-109)
        secure = V.parse_bool(params[0])
        code = V.parse_string(params[1]) if len(params) > 1 else None
        if not secure and code != str(self.param("cal_code")):
            raise ScpiError(CAL_SECURED, "Calibration error; wrong security code")
        self._set("cal.secured", secure)
        return OpResult()

    @op("cal_zero")
    def op_cal_zero(self, **_: Any) -> OpResult:
        """:CALibration:ZERO? — with 0 V applied, make the reading zero. Replies 0 (done) or 1 (failed)."""
        store = self._store()
        if store is not None:
            return store.adjust_offset(self)
        raw = self._conditioning().raw("DC")
        full = max(abs(float(self.param("cal_value"))), 1.0)
        if abs(raw) > float(self.param("cal_tolerance")) * full:
            self.owner.push_error(CAL_INPUT, "Calibration error; input is not zero")  # type: ignore[union-attr]
            return OpResult(b"1")
        self._set("cal.offset", raw)
        return OpResult(b"0")

    @op("cal_gain")
    def op_cal_gain(self, **_: Any) -> OpResult:
        """:CALibration[:GAIN]? — with :CALibration:VALue applied, make the reading equal it."""
        store = self._store()
        if store is not None:
            return store.adjust_gain(self)
        target = float(self.param("cal_value"))
        corrected = self._conditioning().raw("DC") - float(self.param("cal_offset"))
        if target == 0 or corrected == 0 or abs(corrected / target - 1) > float(self.param("cal_tolerance")):
            self.owner.push_error(CAL_INPUT, "Calibration error; input does not match the calibration value")  # type: ignore[union-attr]
            return OpResult(b"1")
        self._set("cal.gain", target / corrected)
        return OpResult(b"0")

    @op("cal_store")
    def op_cal_store(self, **_: Any) -> OpResult:
        """:CALibration:STORe — keep the coefficients; counts the calibration and dates it."""
        store = self._store()
        if store is not None:
            return store.op_store()
        self._unsecured()
        assert self.owner is not None
        self._set("cal.count", int(self.owner.get_state("cal.count")) + 1)
        self._set("cal.date", self.owner.clock().date().isoformat())
        return OpResult()


__all__ = ["CAL_INPUT", "CAL_SECURED", "DmmAdc", "DmmConditioning", "DmmInputTerminal"]
