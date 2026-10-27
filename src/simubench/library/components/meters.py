"""Meters: a digital multimeter's voltage functions, with a calibratable measurement path (M5)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

#: Calibration errors (positive device codes, as on real meters).
CAL_SECURED = 701
CAL_INPUT = 702


@component("dmm_core")
class DmmCore(Component):
    """DC (mean) and true-rms AC voltage of the net on its input.

    AC is the rms of the lines above ``ac_low`` Hz (the AC path's coupling), DC is the mean. The
    measurement path has a hidden gain error (ppm) and offset (V), the instrument's own errors;
    calibration coefficients correct them::

        raw     = true × (1 + gain_error_ppm·1e-6) + offset
        reading = (raw − cal.offset) × cal.gain

    Because the hidden errors are known, the true as-found and as-left errors are known too, and a
    calibration script's conclusions can be checked (calibration-notes.md 3).
    """

    ports = {"in": PortSpec("in", "signal")}
    defaults = {
        "ac_low": 3.0,
        "digits": 7,
        "gain_error_ppm": 0.0,
        "offset": 0.0,
        "cal_gain": 1.0,
        "cal_offset": 0.0,
        "cal_value": 0.0,
        "cal_secured": True,
        "cal_code": "SIMUBENCH",
        "cal_tolerance": 0.1,
    }
    units = {"offset": "V", "cal_offset": "V", "cal_value": "V"}

    # ---- the measurement path ------------------------------------------------------------------------

    def true_values(self) -> dict[str, float]:
        sig = self.input("in").merged()
        ac = sig.amp[sig.freq >= float(self.param("ac_low"))]
        return {"DC": sig.mean(), "AC": math.sqrt(float(np.sum(np.abs(ac) ** 2)) / 2)}

    def raw(self, what: str) -> float:
        """The uncorrected reading: the true value through the meter's own errors."""
        true = self.true_values()[what]
        gained = true * (1 + float(self.param("gain_error_ppm")) * 1e-6)
        return gained + float(self.param("offset")) if what == "DC" else gained

    def reading(self, what: str) -> float:
        cal_offset = float(self.param("cal_offset")) if what == "DC" else 0.0
        return (self.raw(what) - cal_offset) * float(self.param("cal_gain"))

    def readings(self) -> dict[str, Any]:
        values: dict[str, Any] = {"DC": self.reading("DC"), "AC": self.reading("AC")}
        if self.session.reveal_true_values:
            values.update({f"true_{k}": v for k, v in self.true_values().items()})
        return {"values": values}

    @op("measure")
    def op_measure(self, what: str = "DC", **_: Any) -> OpResult:
        return OpResult(V.format_float(self.reading(what), int(self.param("digits"))))

    # ---- calibration ---------------------------------------------------------------------------------

    def _set(self, key: str, value: Any) -> None:
        assert self.owner is not None
        self.owner.set_state(key, value, source="scpi", internal=True)

    def _unsecured(self) -> None:
        if self.param("cal_secured"):
            raise ScpiError(CAL_SECURED, "Calibration error; calibration is secured")

    @op("cal_secure")
    def op_cal_secure(self, query: bool = False, values: list[bytes] | None = None, **_: Any) -> OpResult:
        """:CALibration:SECure:STATe {ON|OFF},<code>: unsecuring needs the security code."""
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
        self._unsecured()
        raw = self.raw("DC")
        full = max(abs(float(self.param("cal_value"))), 1.0)
        if abs(raw) > float(self.param("cal_tolerance")) * full:
            self.owner.push_error(CAL_INPUT, "Calibration error; input is not zero")  # type: ignore[union-attr]
            return OpResult(b"1")
        self._set("cal.offset", raw)
        return OpResult(b"0")

    @op("cal_gain")
    def op_cal_gain(self, **_: Any) -> OpResult:
        """:CALibration[:GAIN]? — with :CALibration:VALue applied, make the reading equal it."""
        self._unsecured()
        target = float(self.param("cal_value"))
        corrected = self.raw("DC") - float(self.param("cal_offset"))
        if target == 0 or corrected == 0 or abs(corrected / target - 1) > float(self.param("cal_tolerance")):
            self.owner.push_error(CAL_INPUT, "Calibration error; input does not match the calibration value")  # type: ignore[union-attr]
            return OpResult(b"1")
        self._set("cal.gain", target / corrected)
        return OpResult(b"0")

    @op("cal_store")
    def op_cal_store(self, **_: Any) -> OpResult:
        """:CALibration:STORe — keep the coefficients; counts the calibration and dates it."""
        self._unsecured()
        assert self.owner is not None
        self._set("cal.count", int(self.owner.get_state("cal.count")) + 1)
        self._set("cal.date", self.owner.clock().date().isoformat())
        return OpResult()
