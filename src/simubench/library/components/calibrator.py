"""Oscilloscope calibrator: multi-channel square, pulse and sine output set through a SCPI tree.

The commands are ``:CHANnel<n>:BASE:WAVe|FREQuency|AMPLitude|OFFSet|DUTY``, ``:CHANnel<n>:LOAD``
and ``:CHANnel<n>:OUTPut`` (docs/lowfreq-cal.md). Each channel is the shared
``function_generator_core`` behind a 50 Ω ``output_stage``; this module adds what differs:

* the waveform names (``SQUare``, ``PULSe``, ``SINe``, ``DC``, ``TRIangle``, ``RAMP``) and the duty
  cycle as a ratio (``0.5``; a value above 1 is read as a percentage);
* the amplitude unit (``:CHANnel<n>:AMPLitude:UNIT VPP|VPK|VRMS``) for ``:BASE:AMPLitude``.

Square and pulse swing ``offset ± amplitude / 2``; the amplitude is defined for the load setting
(D13), and ``LOAD 10000`` is the high-impedance setting (``output_stage`` ``hiz_from``).
"""

from __future__ import annotations

import math
from typing import Any

from ...engine.component import Component, OpResult, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError
from .sources import FunctionGeneratorCore

#: Waveform name -> (function_generator_core function, ramp symmetry in %).
WAVES = {
    "SQUare": ("SQUare", None),
    "PULSe": ("SQUare", None),
    "SINe": ("SINusoid", None),
    "DC": ("DC", None),
    "TRIangle": ("RAMP", 50.0),
    "RAMP": ("RAMP", 100.0),
}
#: Peak-to-peak volts per RMS volt, by core function (``:AMPLitude:UNIT VRMS``).
VPP_PER_VRMS = {"SINusoid": 2 * math.sqrt(2), "SQUare": 2.0, "RAMP": 2 * math.sqrt(3)}


@component("calibrator_core")
class CalibratorCore(FunctionGeneratorCore):
    """``function_generator_core`` with the calibrator's waveform names, duty ratio and amplitude unit."""

    defaults = {**FunctionGeneratorCore.defaults, "wave": "SQUare", "duty": 0.5, "amplitude_unit": "VPP"}

    def param(self, key: str) -> Any:
        if key not in ("function", "symmetry", "duty", "amplitude"):
            return super().param(key)
        function, symmetry = WAVES[str(super().param("wave"))]
        if key == "function":
            return function
        if key == "symmetry":
            return symmetry if symmetry is not None else super().param("symmetry")
        if key == "duty":
            return float(super().param("duty")) * 100.0  # the core takes % high
        amp = float(super().param("amplitude"))
        unit = str(super().param("amplitude_unit"))
        if unit == "VPK":
            return 2 * amp
        if unit == "VRMS":
            return amp * VPP_PER_VRMS.get(function, VPP_PER_VRMS["SINusoid"])
        return amp


@component("scope_calibrator")
class ScopeCalibrator(Component):
    """Commands that are not plain settings: the duty cycle, read as a ratio or a percentage."""

    @op("duty")
    def duty(self, query: bool = False, channel: int | None = None, value: float | None = None,
             **_: Any) -> OpResult:  # fmt: skip
        """``:CHANnel<n>:BASE:DUTY 0.25`` (ratio); ``25`` is read as 25 %; the query answers the ratio."""
        assert self.owner is not None and channel is not None
        path = f"ch{channel}.duty"
        if query:
            f = self.owner.definition.format
            v = float(self.owner.get_state(path))
            return OpResult(
                V.format_float(v, f.digits, style=f.number, sign=f.sign, exponent_digits=f.exponent_digits)
            )
        if value is None:
            raise ScpiError(-109)
        ratio = value / 100.0 if value > 1 else value
        if not 0 < ratio <= 1:
            raise ScpiError(-222, "duty cycle outside (0, 1] (or 0..100 %)")
        self.owner.set_state(path, ratio)
        return OpResult()
