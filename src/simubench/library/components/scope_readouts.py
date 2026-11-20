"""The direct readings: one measurement by name or by number, the clipping and overload flags of a record. Part of ``ScopeMeasurements`` (signal-chain.md 3.4, #1540)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ...engine.component import (
    OpResult,
)
from ...scpi.errors import ScpiError
from .measurements import (
    LEVEL_MEASUREMENTS,
)
from .scope_measurements_host import MeasurementsHost


class Readouts:
    def __init__(self, host: MeasurementsHost) -> None:
        self.host = host

    def clip_flags(self, n: int) -> int:
        """Whether channel n's record reaches the ADC's limits: 1 low, 2 high, 3 both, 0 neither
        (a channel without a record or an unquantized ADC: 0)."""
        acq = self.host.acq()
        if n < 0:
            return 0  # a math function has no ADC to reach
        try:
            rec, _ = acq.measure_record(n)
        except ScpiError:
            return 0
        if getattr(rec, "clip", None) is not None:
            return int(rec.clip)  # the converter's own, a filter after it notwithstanding (#1262)
        v = np.asarray(rec.volts, dtype=float)
        if not v.size or not acq.adc_unit().param("bits"):
            return 0
        high, low = acq.adc_unit().rails(rec)
        return (1 if v.min() <= low else 0) | (2 if v.max() >= high else 0)

    def op_clipped(self, channel: int | None = None, sides: bool = False, **_: Any) -> OpResult:
        flags = self.clip_flags(channel or 1)  # the header's channel, not the measurement source
        return OpResult(str(flags if sides else int(bool(flags))).encode())

    def op_overload(self, **_: Any) -> OpResult:
        return OpResult(str(self.host.acq().overload_bits()).encode())

    def op_all(self, source: Any = None, channel: int | None = None, **_: Any) -> OpResult:
        n = self.host.channel_of(channel, source)
        used = 0
        parts = []
        for what in self.host.param("all") or []:
            value, u = self.host.values.value(str(what), n)
            used += u
            parts.append(self.host.number(value))
        return OpResult(b",".join(parts), used)

    def measure(
        self,
        what: str = "",
        source: Any = None,
        channel: int | None = None,
        query: bool = True,
        interval: str | None = None,
        direction: str | None = None,
        coupling: str | None = None,
        units: str | None = None,
        impedance: Any = None,
        **_: Any,
    ) -> OpResult:
        if interval and str(interval).upper().startswith("CYCL") and f"{what}:CYCLe" in LEVEL_MEASUREMENTS:
            what += ":CYCLe"
        if coupling and str(coupling).upper().startswith("AC") and f"{what}:AC" in LEVEL_MEASUREMENTS:
            what += ":AC"
        if what in LEVEL_MEASUREMENTS and direction and str(direction).upper().startswith("FALL"):
            what += ":FALLing"
        if units and str(units).upper().startswith("DBM"):
            what += f":DBM{self.host.values.impedance(impedance):g}"
        n = self.host.channel_of(channel, source)
        self.host.follow(n, source)
        if not query:
            self.host.results.install(what, n)
            return OpResult()
        value, used = self.host.values.value(what, n)
        self.host.results.keep(what, n, value)
        if not math.isfinite(value) and self.host.param("invalid_value") != "UNDEF":
            return OpResult(str(self.host.param("invalid_value")).encode(), used)
        return OpResult(self.host.format_number(value), used)  # NaN: the SCPI sentinel 9.91E+37

    def named(self, channel: int | None = None, kind: str | None = None, **_: Any) -> OpResult:
        if kind is None:
            raise ScpiError(-109)
        table = {str(k).upper(): v for k, v in (self.host.param("names") or {}).items()}
        name = kind.strip().upper()
        if name not in table:
            raise ScpiError(-224, f"measurement {kind} not implemented in this definition")
        engine_name, unit = table[name]
        n = self.host.channel_of(channel)
        value, used = self.host.values.value(str(engine_name), n)
        self.host.results.keep(name, n, value)
        good, bad = self.host.param("status")
        ok = math.isfinite(value)
        text = self.host.format_number(value) if ok else str(self.host.param("invalid_value")).encode()
        return OpResult(text, used, {"kind": name, "unit": str(unit), "status": str(good if ok else bad)})
