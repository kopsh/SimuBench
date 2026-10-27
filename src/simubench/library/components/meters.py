"""Meters: a digital multimeter's voltage functions."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import Component, OpResult, PortSpec, component, op
from ...scpi import values as V


@component("dmm_core")
class DmmCore(Component):
    """DC (mean) and true-rms AC voltage of the net on its input.

    AC is the rms of the lines above ``ac_low`` Hz (the AC path's coupling), DC is the mean.
    Readings are exact; the meter's own noise and resolution are applied as ``digits``.
    """

    ports = {"in": PortSpec("in", "signal")}
    defaults = {"ac_low": 3.0, "digits": 7}

    def readings(self) -> dict[str, Any]:
        sig = self.input("in").merged()
        ac = sig.amp[sig.freq >= float(self.param("ac_low"))]
        return {"values": {"DC": sig.mean(), "AC": math.sqrt(float(np.sum(np.abs(ac) ** 2)) / 2)}}

    @op("measure")
    def op_measure(self, what: str = "DC", **_: Any) -> OpResult:
        sig = self.input("in").merged()
        if what == "AC":
            ac = sig.amp[sig.freq >= float(self.param("ac_low"))]
            value = math.sqrt(float(np.sum(np.abs(ac) ** 2)) / 2)
        else:
            value = sig.mean()
        return OpResult(V.format_float(value, int(self.param("digits"))))
